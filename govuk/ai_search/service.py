"""AI search's routes on the ASGI service.

Served here, not by gunicorn, because each Claude call takes seconds. Uses the
default site's Settings > Search.
"""

from concurrent.futures import ThreadPoolExecutor

from asgiref.sync import sync_to_async
from django.conf import settings
from starlette.responses import JSONResponse
from starlette.routing import Route

from govuk.features import ai_enabled
from govuk.service.app import Contribution, RateLimit
from govuk.service.db import release_connections

from . import MATCHES_PATH, SUMMARY_PATH, guard
from .views import (
    MATCHES_BUSY,
    MATCHES_NAMESPACE,
    MATCHES_RATE_LIMITED,
    SUMMARY_NAMESPACE,
    SUMMARY_RATE_LIMITED,
    default_site,
    matches_body,
    summary_body,
)

HEADERS = {"Cache-Control": "no-store, private", "X-Robots-Tag": "noindex"}


BUSY_RETRY_AFTER = "5"

_executor: ThreadPoolExecutor | None = None
_in_flight = 0


def _in_thread(body, *values) -> tuple[int, dict]:
    try:
        return body(*values, default_site())
    finally:
        release_connections()


def _pool() -> ThreadPoolExecutor:
    """AI search's own threads, so a slow Claude call never holds one another route needs."""
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=settings.AI_SEARCH_WORKERS, thread_name_prefix="ai-search")
    return _executor


async def _run(body, *values) -> tuple[int, dict] | None:
    """``body`` on a free AI search thread, or None when every one is busy: refused, never queued."""
    global _in_flight
    # Safe without a lock: the event loop runs one coroutine at a time and nothing awaits in between.
    if _in_flight >= settings.AI_SEARCH_WORKERS:
        return None
    _in_flight += 1
    try:
        # Own thread: the work touches the database (not allowed in async code) and is slow.
        return await sync_to_async(_in_thread, thread_sensitive=False, executor=_pool())(body, *values)
    finally:
        _in_flight -= 1


def _busy(body: dict) -> JSONResponse:
    return JSONResponse(body, status_code=503, headers={**HEADERS, "Retry-After": BUSY_RETRY_AFTER})


async def _checked_form(request, limit: int):
    """The form, or the response refusing the request."""
    if not guard.from_this_site(request.headers):
        return JSONResponse(guard.FORBIDDEN, status_code=403, headers=HEADERS)
    try:
        return await guard.read_form(request, limit)
    except guard.TooLarge:
        return JSONResponse(guard.TOO_LARGE, status_code=413, headers=HEADERS)


async def matches_endpoint(request):
    form = await _checked_form(request, guard.MATCHES_MAX_BYTES)
    if isinstance(form, JSONResponse):
        return form
    answer = await _run(matches_body, form.get("question"), form.get("access"))
    if answer is None:
        return _busy(MATCHES_BUSY)
    status, body = answer
    return JSONResponse(body, status_code=status, headers=HEADERS)


async def summary_endpoint(request):
    form = await _checked_form(request, guard.SUMMARY_MAX_BYTES)
    if isinstance(form, JSONResponse):
        return form
    answer = await _run(summary_body, form.get("ticket"))
    if answer is None:
        return _busy(SUMMARY_RATE_LIMITED)
    status, body = answer
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
