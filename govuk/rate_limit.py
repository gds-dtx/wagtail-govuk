"""Requests counted per client address, in fixed windows in the default cache.

Shared by the MCP server (an ASGI middleware, ``govuk.mcp.app``) and Ask
(a Django view, ``govuk.ask.views``). The count is per process unless
``CACHE_URL`` names a shared cache, so it is a second line behind the WAF's.

The address is the one in the configured header (``CLIENT_IP_HEADER`` for
Django, ``MCP_CLIENT_IP_HEADER`` for the MCP server: CloudFront's
true-client-ip, deployed), when the request carries it; otherwise the
connection's.
"""

import time

from django.core.cache import cache

DEFAULT_WINDOW_SECONDS = 60


def first_address(value: str) -> str:
    """One address, as a proxy sets it; the first, if a list."""
    return (value or "").split(",")[0].strip()


def client_address(request, header: str) -> str:
    """The client's address for a Django request."""
    if header:
        address = first_address(request.headers.get(header, ""))
        if address:
            return address
    return request.META.get("REMOTE_ADDR") or "unknown"


def _key(namespace: str, client: str, window: int, now: float) -> str:
    return f"govuk-rate:{namespace}:{client}:{int(now // window)}"


def _retry_after(window: int, now: float) -> int:
    return window - int(now % window)


def hit(namespace: str, client: str, *, window: int = DEFAULT_WINDOW_SECONDS) -> tuple[int, int]:
    """Count one request; the count in this window, and seconds until the next."""
    now = time.time()
    key = _key(namespace, client, window, now)
    if cache.add(key, 1, timeout=window * 2):
        count = 1
    else:
        try:
            count = cache.incr(key)
        except ValueError:  # the window ended between the two calls
            count = 1
    return count, _retry_after(window, now)


async def ahit(namespace: str, client: str, *, window: int = DEFAULT_WINDOW_SECONDS) -> tuple[int, int]:
    now = time.time()
    key = _key(namespace, client, window, now)
    if await cache.aadd(key, 1, timeout=window * 2):
        count = 1
    else:
        try:
            count = await cache.aincr(key)
        except ValueError:
            count = 1
    return count, _retry_after(window, now)
