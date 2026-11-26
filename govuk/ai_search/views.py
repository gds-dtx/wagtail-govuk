"""/search/ with AI search, and the matches and summary endpoints.

Without JavaScript, matches are ranked by search alone: Claude is never called
from gunicorn. /search/ is a GET, so its results can be returned to,
reloaded and shared; the app's own logs still never record the question.
"""

import logging
import re
import time
from urllib.parse import urlencode

from django.conf import settings
from django.http import Http404, HttpResponseRedirect, JsonResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.cache import add_never_cache_headers
from django.utils.html import format_html
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from wagtail.models import Site

from govuk import rate_limit
from govuk.content_index.search import HYBRID, SearchError, is_close, search
from govuk.features import catalogue_enabled
from govuk.models import ContentMainPage, SearchSettings

from . import (
    MATCHES_PATH,
    SUMMARY_PATH,
    claims,
    guard,
    preference,
    rerank as reranking,
    summary,
    usage,
)
from .access import make_access, may_use_claude, read_access
from .cards import MAX_CARDS, cards_for
from .claude import get_claude
from .search_area import EMPTY_ERROR, QUESTION_MAX_LENGTH, search_area

logger = logging.getLogger(__name__)

# More than MAX_CARDS: an entry's parts share its card.
SEARCH_HITS = 20
# A form error, so no full stop, as GOV.UK error messages have none.
RATE_LIMITED = "Rate limit exceeded. Try again in a few minutes"
MATCHES_NAMESPACE = "ai-search"
NOT_BY_AI = "These results are not AI-assisted."
LIMIT_MESSAGES = {
    claims.MINUTE: f"Rate limit exceeded. {NOT_BY_AI} Try again in a few minutes.",
    claims.DAY: f"Rate limit exceeded. {NOT_BY_AI} Try again tomorrow.",
    usage.BUDGET: f"AI-assisted search is not available at the moment. {NOT_BY_AI}",
}
SUMMARY_NAMESPACE = "ai-search-summary"


def _warning_text(message: str) -> str:
    """GOV.UK Warning Text."""
    return format_html(
        '<div class="govuk-warning-text app-ai-search-limit">'
        '<span class="govuk-warning-text__icon" aria-hidden="true">!</span>'
        '<strong class="govuk-warning-text__text"><span class="govuk-visually-hidden">Warning</span> {}</strong>'
        "</div>",
        message,
    )


def limit_warning(limit: str | None) -> str:
    """The warning for a limit that stopped Claude, or ""."""
    return _warning_text(LIMIT_MESSAGES[limit]) if limit else ""


def claude_limit(account: str) -> str | None:
    """What stops Claude for this account now: its own limits first, then the site's budget."""
    return claims.account_limit_reached(account) or (usage.BUDGET if usage.over_budget() else None)


def clean_question(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def question_error(question: str) -> str:
    if not question:
        return EMPTY_ERROR
    if len(question) > QUESTION_MAX_LENGTH:
        return f"Your question must be {QUESTION_MAX_LENGTH} characters or fewer"
    return ""


def default_site():
    return Site.objects.filter(is_default_site=True).first()


def _browse_url(request=None) -> str:
    """The catalogue's all-entries page, offered when nothing is close."""
    if not catalogue_enabled():
        return ""
    main = ContentMainPage.objects.live().public().order_by("path").first()
    url = main.get_url(request) if main else None
    return f"{url}all/" if url else ""


def find_matches(question: str, search_settings, *, use_claude: bool, signed_in: bool, request=None) -> dict:
    """Cards for a question, optionally re-ranked by Claude, with a summary ticket."""
    started = time.monotonic()
    claude = get_claude() if use_claude else None
    rerank_on = claude is not None and search_settings.use_ai_ranking
    threshold = settings.CONTENT_INDEX_MIN_SIMILARITY
    outcome = search(
        question,
        limit=settings.AI_SEARCH_RERANK_CANDIDATES if rerank_on else SEARCH_HITS,
        mode=HYBRID,
        # Lower floor for Claude, which judges closeness itself.
        min_similarity=settings.AI_SEARCH_RERANK_FLOOR if rerank_on else threshold,
        max_length=QUESTION_MAX_LENGTH,
    )
    ranking, hits = "search", None
    if rerank_on and outcome.hits:
        if usage.over_budget():
            ranking = "over_budget"
        else:
            reranked = reranking.rerank(question, outcome.hits, claude, site=search_settings.site)
            ranking = "ai" if reranked.status == reranking.OK else reranked.status
            if reranked.status == reranking.OK:
                hits = reranked.hits
    if hits is None:
        hits = [hit for hit in outcome.hits if is_close(hit, threshold, semantic=outcome.semantic)]
    cards = cards_for(hits, request, limit=MAX_CARDS)
    ticket = ""
    if cards and claude is not None and search_settings.show_ai_summary and not usage.over_budget():
        ticket = summary.make_ticket(question, cards)
    logger.info(
        "AI search",
        extra={
            "results": len(cards),
            "semantic": outcome.semantic,
            "ranking": ranking,
            "summary": bool(ticket),
            "signed_in": signed_in,
            "duration_ms": round((time.monotonic() - started) * 1000),
        },
    )
    return {"cards": cards, "semantic": outcome.semantic, "summary_ticket": ticket}


def matches_body(question, access, site) -> tuple[int, dict]:
    """Status and JSON body for the matches endpoint, which needs an access token from /search/."""
    search_settings = SearchSettings.for_site(site) if site else None
    if search_settings is None or not search_settings.ai_search_on:
        return 404, {"status": "unavailable"}
    question = clean_question(question)
    error = question_error(question)
    if error:
        return 400, {"status": "invalid", "message": error}
    allowed = read_access(str(access or ""), question, search_settings)
    if allowed is None:
        return 403, {"status": "forbidden"}
    # Past a limit the question is searched without Claude.
    limit = claude_limit(allowed["account"])
    if limit in (claims.MINUTE, claims.DAY):
        logger.warning("AI search account limit reached", extra={"limit": limit})
    if not claims.claim(claims.MATCHES, allowed["id"], allowed["account"]):
        return 403, {"status": "forbidden"}
    context = {}
    try:
        context.update(
            find_matches(
                question,
                search_settings,
                use_claude=limit is None,
                signed_in=allowed["signed_in"],
            )
        )
    except SearchError as exc:
        context["matches_error"] = str(exc)
    else:
        if not context["cards"]:
            context["browse_url"] = _browse_url()
    return 200, {
        "status": "error" if "matches_error" in context else "ok",
        "html": render_to_string("ai_search/includes/matches.html", context),
        "count": len(context.get("cards", [])),
        "ticket": context.get("summary_ticket", ""),
        "warning": limit_warning(limit),
    }


def summary_body(ticket, site) -> tuple[int, dict]:
    """Status and JSON body for the summary endpoint."""
    search_settings = SearchSettings.for_site(site) if site else None
    if search_settings is None or not search_settings.ai_search_on or not summary.summary_enabled(site):
        return 404, {"status": summary.UNAVAILABLE}
    try:
        result = summary.summarise(str(ticket or ""), site)
    except summary.TicketError:
        return 400, {"status": summary.UNAVAILABLE}
    return 200, {"status": result.status, "html": result.html}


MATCHES_RATE_LIMITED = {
    "status": "error",
    "html": '<p class="govuk-body">Your question was not searched.</p>',
    "count": 0,
    "ticket": "",
    "warning": _warning_text(f"{RATE_LIMITED}."),
}
SUMMARY_RATE_LIMITED = {"status": summary.UNAVAILABLE}
MATCHES_BUSY = {
    "status": "error",
    "html": '<p class="govuk-body">Your question was not searched.</p>',
    "count": 0,
    "ticket": "",
    "warning": _warning_text("AI-assisted search is busy. Try again in a few minutes."),
}


def _no_store(response):
    add_never_cache_headers(response)
    response["X-Robots-Tag"] = "noindex"
    return response


def _over_limit(request, namespace: str, limit: int) -> int | None:
    """Seconds to wait when over the limit, else None."""
    if limit <= 0:
        return None
    client = rate_limit.client_address(request, settings.CLIENT_IP_HEADER)
    count, retry_after = rate_limit.hit(namespace, client)
    if count > limit:
        if count == limit + 1:
            logger.warning("AI search rate limit reached", extra={"namespace": namespace, "limit": limit})
        return retry_after
    return None


def _home_hero(site):
    """The home page if it has a combined hero, for /search/ to draw its band."""
    home = site.root_page.specific if site else None
    return home if getattr(home, "hero_is_combined", False) else None


def _search_url(**fields) -> str:
    return f"{reverse('search')}?{urlencode(fields)}"


def _posted_fields(posted) -> dict:
    """The GET's fields for the form as it was posted: ``js=1``, ``ai_offered=1`` and the box."""
    fields = {"question": posted["question"]} if "question" in posted else {}
    if posted.get("js") == "1" and posted.get("ai_offered") == "1":
        fields["ai"] = "1" if posted.get("ai") == "1" else "0"
    return fields


def search_page(request, search_settings):
    if request.method == "POST":
        # From a page cached while the form was still posted.
        return _no_store(HttpResponseRedirect(_search_url(**_posted_fields(request.POST)), status=303))
    site = search_settings.site
    context = {"asked": False}
    status, retry_after = 200, None
    data = request.GET
    asked = "question" in data
    question = clean_question(data.get("question")) if asked else ""
    error = question_error(question) if asked else ""
    # ai-search.js sends "ai" only while the box is enabled, so it also says JavaScript ran
    # and, unlike a form drawn while signed out, that the reader could choose.
    chose = asked and data.get("ai") in ("0", "1")
    # A link from elsewhere carries its sender's choice, so it may turn AI search off but
    # not on. Browsers send no Origin on a GET, so one without Sec-Fetch-Site gets the same.
    own_form = guard.from_this_site(request.headers)
    ticked = data.get("ai") == "1"
    use_ai = (ticked if own_form else ticked and preference.remembered(request.user)) if chose else None
    claude_allowed = may_use_claude(search_settings, request.user)
    if chose and own_form and claude_allowed:
        preference.remember(request.user, use_ai)
    limit = None
    if asked and not error:
        context["asked"] = True
        wants_claude = use_ai and claude_allowed
        if wants_claude:
            signed_in = request.user.is_authenticated
            limit = claude_limit(claims.account_for(request.user.pk) if signed_in else "")
            context["limit_warning"] = limit_warning(limit)
        if wants_claude and not limit:
            context.update(
                deferred=True,
                matches_url=MATCHES_PATH,
                access=make_access(request.user, question),
                # For a link with "ai" opened without JavaScript.
                without_js_url=_search_url(question=question),
            )
        else:
            retry_after = _over_limit(request, MATCHES_NAMESPACE, settings.AI_SEARCH_RATE_LIMIT_PER_MINUTE)
            if retry_after is not None:
                error, status, context["asked"] = RATE_LIMITED, 429, False
            else:
                try:
                    context.update(
                        find_matches(
                            question,
                            search_settings,
                            use_claude=False,
                            signed_in=request.user.is_authenticated,
                            request=request,
                        )
                    )
                except SearchError as exc:
                    error, context["asked"] = str(exc), False
                else:
                    if not context["cards"]:
                        context["browse_url"] = _browse_url(request)
    hero_page = _home_hero(site)
    summary_ready = summary.summary_enabled(site) and not usage.over_budget()
    context.update(
        area=search_area(
            search_settings,
            search_page=True,
            question=question,
            error=error,
            in_main=hero_page is None,
            user=request.user,
            use_ai=use_ai,
        ),
        hero_page=hero_page,
        summary_url=SUMMARY_PATH,
        summary_on=summary_ready and bool(context.get("deferred")),
        summary_needs_js=summary_ready and claude_allowed and not chose,
    )
    # base.html reads the search area from "ai_search".
    context["ai_search"] = context["area"]
    response = _no_store(render(request, "ai_search/results.html", context, status=status))
    if retry_after is not None:
        response["Retry-After"] = str(retry_after)
    return response


# Development only (AI_SEARCH_IN_DJANGO): the service's paths served by Django.


def _refused(request, limit: int):
    """The service's checks, so development behaves as deployed."""
    if not guard.from_this_site(request.headers):
        return _no_store(JsonResponse(guard.FORBIDDEN, status=403))
    try:
        guard.check_length(request.headers, limit)
    except guard.TooLarge:
        return _no_store(JsonResponse(guard.TOO_LARGE, status=413))
    return None


@csrf_exempt
@require_http_methods(["POST"])
def matches_view(request):
    refused = _refused(request, guard.MATCHES_MAX_BYTES)
    if refused:
        return refused
    retry_after = _over_limit(request, MATCHES_NAMESPACE, settings.AI_SEARCH_RATE_LIMIT_PER_MINUTE)
    if retry_after is not None:
        response = JsonResponse(MATCHES_RATE_LIMITED, status=429)
        response["Retry-After"] = str(retry_after)
        return _no_store(response)
    status, body = matches_body(request.POST.get("question"), request.POST.get("access"), Site.find_for_request(request))
    if status == 404:
        raise Http404
    return _no_store(JsonResponse(body, status=status))


@csrf_exempt
@require_http_methods(["POST"])
def summary_view(request):
    refused = _refused(request, guard.SUMMARY_MAX_BYTES)
    if refused:
        return refused
    retry_after = _over_limit(request, SUMMARY_NAMESPACE, settings.AI_SEARCH_SUMMARY_RATE_LIMIT_PER_MINUTE)
    if retry_after is not None:
        response = JsonResponse(SUMMARY_RATE_LIMITED, status=429)
        response["Retry-After"] = str(retry_after)
        return _no_store(response)
    status, body = summary_body(request.POST.get("ticket"), Site.find_for_request(request))
    if status == 404:
        raise Http404
    return _no_store(JsonResponse(body, status=status))
