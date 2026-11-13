"""The /ask/ page: the question box's question, and the closest matches to it."""

import logging
import re
import time

from django.conf import settings
from django.http import Http404
from django.shortcuts import render
from django.utils.cache import add_never_cache_headers
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from govuk import rate_limit
from govuk.content_index import ask_enabled
from govuk.content_index.search import HYBRID, SearchError, is_close, search
from govuk.models import ContentMainPage
from govuk.models.blocks import ASK_DEFAULT_LABEL

from .cards import MAX_CARDS, cards_for

logger = logging.getLogger(__name__)

QUESTION_MAX_LENGTH = 300
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


def _matches(question: str, request) -> tuple[list[dict], str]:
    """The cards for a question, and whether the search had meaning to go on.

    Logged by outcome only: never the question.
    """
    started = time.monotonic()
    threshold = settings.CONTENT_INDEX_MIN_SIMILARITY
    outcome = search(
        question,
        limit=SEARCH_HITS,
        mode=HYBRID,
        min_similarity=threshold,
        max_length=QUESTION_MAX_LENGTH,
    )
    close = [hit for hit in outcome.hits if is_close(hit, threshold, semantic=outcome.semantic)]
    cards = cards_for(close, request, limit=MAX_CARDS)
    logger.info(
        "Ask search",
        extra={
            "results": len(cards),
            "semantic": outcome.semantic,
            "duration_ms": round((time.monotonic() - started) * 1000),
        },
    )
    return cards, outcome.semantic


# Exempt from CSRF: the post only searches, and changes nothing. A token would
# also have to be in the page the question box is on, which may be cached.
@csrf_exempt
@require_http_methods(["GET", "POST"])
def ask_view(request):
    if not ask_enabled():
        raise Http404
    context = {"label": ASK_DEFAULT_LABEL, "max_length": QUESTION_MAX_LENGTH, "asked": False}
    status = 200
    retry_after = None
    if request.method == "POST":
        question = re.sub(r"\s+", " ", request.POST.get("question", "")).strip()
        context["question"] = question
        if not question:
            context["error"] = "Enter your question"
        elif len(question) > QUESTION_MAX_LENGTH:
            context["error"] = f"Your question must be {QUESTION_MAX_LENGTH} characters or fewer"
        else:
            retry_after = _over_limit(request)
            if retry_after is not None:
                context["error"] = "You have asked a lot of questions in a short time. Wait a minute, then try again"
                status = 429
            else:
                try:
                    cards, semantic = _matches(question, request)
                except SearchError as exc:
                    context["error"] = str(exc)
                else:
                    context.update(asked=True, cards=cards, semantic=semantic)
                    if not cards:
                        context["browse_url"] = _browse_url(request)
    response = render(request, "ask/ask_page.html", context, status=status)
    add_never_cache_headers(response)
    response["X-Robots-Tag"] = "noindex"
    if retry_after is not None:
        response["Retry-After"] = str(retry_after)
    return response
