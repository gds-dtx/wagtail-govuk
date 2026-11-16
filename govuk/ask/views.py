"""The /ask/ page: the question box's question, and the closest matches to it.

With Claude set up and switched on (Settings > Ask), it re-ranks the
matches before the page is drawn, and the page carries a ticket for the
summary, which the browser fetches afterwards from ``SUMMARY_PATH``. That
path is served by the uvicorn service deployed (``govuk.mcp.app``), and by
``ask_summary_view`` here otherwise.

With the combined hero with a question box on the home page, the page is
drawn under the home page's band, the question in its box (``hero.py``);
otherwise it has its own form. Opened directly (a GET), it goes to the home
page, which is where the question is asked.

Sent by main.js (the box adds ``js``), a question is only checked before the
page is sent, so the browser goes straight there: the page shows the summary
box saying "Thinking…", and main.js posts the question to ``/ask/matches/``
(``ask_matches_view``), which searches, re-ranks and sends the matches and
the summary's ticket back. Without JavaScript the search is done first, and
the page is sent with the matches in it.
"""

import logging
import re
import time

from django.conf import settings
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.utils.cache import add_never_cache_headers
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from wagtail.models import Site

from govuk import rate_limit
from govuk.content_index import ask_enabled
from govuk.content_index.search import HYBRID, SearchError, is_close, search
from govuk.models import AskSettings, ContentMainPage
from govuk.models.blocks import ASK_DEFAULT_LABEL, ASK_EMPTY_ERROR

from . import rerank as reranking, summary, usage
from .cards import MAX_CARDS, cards_for
from .claude import get_claude
from .hero import ASK_FIELD_ID, home_hero

logger = logging.getLogger(__name__)

QUESTION_MAX_LENGTH = 300
MATCHES_PATH = "/ask/matches/"
# Hits asked for: more than there are cards, since the parts of an entry
# share its card.
SEARCH_HITS = 20


def _browse_url(request) -> str:
    """The content catalogue's list of every entry, to offer when nothing is close."""
    main = ContentMainPage.objects.live().public().order_by("path").first()
    url = main.get_url(request) if main else None
    return f"{url}all/" if url else ""


def _over_limit(request) -> int | None:
    """Seconds to wait, when this client has asked too often; else None."""
    limit = settings.ASK_RATE_LIMIT_PER_MINUTE
    if limit <= 0:
        return None
    client = rate_limit.client_address(request, settings.CLIENT_IP_HEADER)
    count, retry_after = rate_limit.hit("ask", client)
    if count > limit:
        if count == limit + 1:
            logger.warning("Ask rate limit reached", extra={"limit": limit})
        return retry_after
    return None


def _matches(question: str, request) -> dict:
    """The cards for a question, and how they were found.

    Logged by outcome only: never the question.
    """
    started = time.monotonic()
    ask_settings = AskSettings.for_request(request)
    claude = get_claude()
    rerank_on = claude is not None and ask_settings.use_ai_ranking
    threshold = settings.CONTENT_INDEX_MIN_SIMILARITY
    outcome = search(
        question,
        limit=settings.ASK_RERANK_CANDIDATES if rerank_on else SEARCH_HITS,
        mode=HYBRID,
        # Claude judges what is close, so it is shown more than the page would.
        min_similarity=settings.ASK_RERANK_FLOOR if rerank_on else threshold,
        max_length=QUESTION_MAX_LENGTH,
    )
    ranking, hits = "search", None
    if rerank_on and outcome.hits:
        if usage.over_budget():
            ranking = "over_budget"
        else:
            reranked = reranking.rerank(question, outcome.hits, claude, site=ask_settings.site)
            ranking = "ai" if reranked.status == reranking.OK else reranked.status
            if reranked.status == reranking.OK:
                hits = reranked.hits
    if hits is None:
        hits = [hit for hit in outcome.hits if is_close(hit, threshold, semantic=outcome.semantic)]
    cards = cards_for(hits, request, limit=MAX_CARDS)
    ticket = ""
    if cards and claude is not None and ask_settings.show_ai_summary and not usage.over_budget():
        ticket = summary.make_ticket(question, cards)
    logger.info(
        "Ask search",
        extra={
            "results": len(cards),
            "semantic": outcome.semantic,
            "ranking": ranking,
            "summary": bool(ticket),
            "duration_ms": round((time.monotonic() - started) * 1000),
        },
    )
    return {"cards": cards, "semantic": outcome.semantic, "summary_ticket": ticket}


def _question(request) -> str:
    return re.sub(r"\s+", " ", request.POST.get("question", "")).strip()


def _question_error(question: str) -> str:
    if not question:
        return ASK_EMPTY_ERROR
    if len(question) > QUESTION_MAX_LENGTH:
        return f"Your question must be {QUESTION_MAX_LENGTH} characters or fewer"
    return ""


def _no_store(response):
    add_never_cache_headers(response)
    response["X-Robots-Tag"] = "noindex"
    return response


# Exempt from CSRF: the post only searches, and changes nothing. A token would
# also have to be in the page the question box is on, which may be cached.
@csrf_exempt
@require_http_methods(["GET", "POST"])
def ask_view(request):
    if not ask_enabled():
        raise Http404
    site = Site.find_for_request(request)
    if request.method == "GET":
        root = site.root_page if site else None
        return redirect((root.get_url(request) if root else None) or "/")
    context = {"label": ASK_DEFAULT_LABEL, "max_length": QUESTION_MAX_LENGTH, "asked": False, "field_id": ASK_FIELD_ID}
    status = 200
    retry_after = None
    question = _question(request)
    context["question"] = question
    error = _question_error(question)
    if error:
        context["error"] = error
    elif request.POST.get("js") == "1":
        # main.js will fetch the matches: the page goes at once.
        context.update(
            asked=True,
            deferred=True,
            matches_url=MATCHES_PATH,
            summary_url=summary.SUMMARY_PATH,
            summary_on=summary.summary_enabled(site) and not usage.over_budget(),
        )
    else:
        retry_after = _over_limit(request)
        if retry_after is not None:
            context["error"] = "You have asked a lot of questions in a short time. Wait a minute, then try again"
            status = 429
        else:
            try:
                matches = _matches(question, request)
            except SearchError as exc:
                context["error"] = str(exc)
            else:
                context.update(asked=True, summary_url=summary.SUMMARY_PATH, **matches)
                if not matches["cards"]:
                    context["browse_url"] = _browse_url(request)
    hero = home_hero(site, question=question, error=context.get("error", ""))
    if hero is not None:
        # base.html draws the hero from ``self``: here, the home page's.
        context.update(self=hero, in_band=True)
    response = _no_store(render(request, "ask/ask_page.html", context, status=status))
    if retry_after is not None:
        response["Retry-After"] = str(retry_after)
    return response


@csrf_exempt
@require_http_methods(["POST"])
def ask_matches_view(request):
    """The matches for a question, for main.js to put in the /ask/ page it
    went straight to: the matches section's HTML, how many there are, and
    the summary's ticket ("" for none). Rate limited, as /ask/ is without
    JavaScript; the question is never logged."""
    if not ask_enabled():
        raise Http404
    question = _question(request)
    error = _question_error(question)
    if error:
        return _no_store(JsonResponse({"status": "invalid", "message": error}, status=400))
    status, context = 200, {}
    retry_after = _over_limit(request)
    if retry_after is not None:
        status = 429
        context["matches_error"] = "You have asked a lot of questions in a short time. Wait a minute, then try again."
    else:
        try:
            context.update(_matches(question, request))
        except SearchError as exc:
            context["matches_error"] = str(exc)
        else:
            if not context["cards"]:
                context["browse_url"] = _browse_url(request)
    body = {
        "status": "error" if "matches_error" in context else "ok",
        "html": render_to_string("ask/includes/matches.html", context, request=request),
        "count": len(context.get("cards", [])),
        "ticket": context.get("summary_ticket", ""),
    }
    response = _no_store(JsonResponse(body, status=status))
    if retry_after is not None:
        response["Retry-After"] = str(retry_after)
    return response


def summary_response(ticket: str, site) -> tuple[int, dict]:
    """The summary endpoint's status and JSON, wherever it is served from."""
    if not summary.summary_enabled(site):
        return 404, {"status": summary.UNAVAILABLE}
    try:
        result = summary.summarise(ticket, site)
    except summary.TicketError:
        return 400, {"status": summary.UNAVAILABLE}
    return 200, {"status": result.status, "html": result.html}


def _summary_over_limit(client: str) -> int | None:
    limit = settings.ASK_SUMMARY_RATE_LIMIT_PER_MINUTE
    if limit <= 0:
        return None
    count, retry_after = rate_limit.hit("ask-summary", client)
    return retry_after if count > limit else None


@csrf_exempt
@require_http_methods(["POST"])
def ask_summary_view(request):
    """The summary, served by Django: locally, and wherever no uvicorn service is in front."""
    if not ask_enabled():
        raise Http404
    retry_after = _summary_over_limit(rate_limit.client_address(request, settings.CLIENT_IP_HEADER))
    if retry_after is not None:
        response = JsonResponse({"status": summary.UNAVAILABLE}, status=429)
        response["Retry-After"] = str(retry_after)
    else:
        status, body = summary_response(request.POST.get("ticket", ""), Site.find_for_request(request))
        response = JsonResponse(body, status=status)
    return _no_store(response)
