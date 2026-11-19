"""AI search's routes on the ASGI service.

Served here, not by gunicorn, because each Claude call takes seconds. Uses the
default site's Settings > Search.
"""

from asgiref.sync import sync_to_async
from django.conf import settings
from starlette.responses import JSONResponse
from starlette.routing import Route

from govuk.features import ai_enabled
from govuk.service.app import Contribution, RateLimit
from govuk.service.db import release_connections

from . import MATCHES_PATH, SUMMARY_PATH
from .views import (
    MATCHES_NAMESPACE,
    MATCHES_RATE_LIMITED,
    SUMMARY_NAMESPACE,
    SUMMARY_RATE_LIMITED,
    default_site,
    matches_body,
    summary_body,
)

HEADERS = {"Cache-Control": "no-store, private", "X-Robots-Tag": "noindex"}


def _in_thread(body, value) -> tuple[int, dict]:
    try:
        return body(value, default_site())
    finally:
        release_connections()


async def matches_endpoint(request):
    form = await request.form()
    # Own thread: the work touches the database (not allowed in async code) and is slow.
    status, body = await sync_to_async(_in_thread, thread_sensitive=False)(matches_body, form.get("question"))
    return JSONResponse(body, status_code=status, headers=HEADERS)


async def summary_endpoint(request):
    form = await request.form()
    status, body = await sync_to_async(_in_thread, thread_sensitive=False)(summary_body, form.get("ticket"))
    return JSONResponse(body, status_code=status, headers=HEADERS)


def contribution() -> Contribution | None:
    if not ai_enabled():
        return None
    return Contribution(
        name="ai-search",
        routes=[
            Route(MATCHES_PATH, matches_endpoint, methods=["POST"]),
            Route(SUMMARY_PATH, summary_endpoint, methods=["POST"]),
        ],
        rate_limits=[
            RateLimit(
                prefix=MATCHES_PATH,
                namespace=MATCHES_NAMESPACE,
                limit=settings.AI_SEARCH_RATE_LIMIT_PER_MINUTE,
                body=MATCHES_RATE_LIMITED,
            ),
            RateLimit(
                prefix=SUMMARY_PATH,
                namespace=SUMMARY_NAMESPACE,
                limit=settings.AI_SEARCH_SUMMARY_RATE_LIMIT_PER_MINUTE,
                body=SUMMARY_RATE_LIMITED,
            ),
        ],
    )
