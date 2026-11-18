"""Fixed-window request counts per client in the default cache.

Per process unless ``CACHE_URL`` is shared, so a second line behind the WAF.
"""

import time

from django.core.cache import cache

DEFAULT_WINDOW_SECONDS = 60


def first_address(value: str) -> str:
    """The first address of a comma-separated list."""
    return (value or "").split(",")[0].strip()


def client_address(request, header: str) -> str:
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
    """Count one request; returns (count this window, seconds until the next)."""
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
