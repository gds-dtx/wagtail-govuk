"""Checks before AI search's endpoints act on a request: sent by this site's own pages, and small.

A script can fake these headers; a browser on another site cannot, so another
site's pages cannot make their visitors spend AI search's tokens.
"""

from urllib.parse import parse_qsl, urlsplit

from django.conf import settings
from django.http.request import split_domain_port, validate_host

# A question is at most 300 characters and a token or ticket a few KB.
MATCHES_MAX_BYTES = 8 * 1024
SUMMARY_MAX_BYTES = 32 * 1024
MAX_FIELDS = 10

FORBIDDEN = {"status": "forbidden"}
TOO_LARGE = {"status": "too_large"}


class TooLarge(Exception):
    pass


def from_this_site(headers) -> bool:
    """``Sec-Fetch-Site`` must say same-origin; without it, ``Origin`` must be one of ALLOWED_HOSTS."""
    fetch_site = headers.get("sec-fetch-site")
    if fetch_site:
        return fetch_site == "same-origin"
    origin = headers.get("origin") or ""
    host, _port = split_domain_port(urlsplit(origin).netloc)
    return bool(host) and validate_host(host, settings.ALLOWED_HOSTS)


def check_length(headers, limit: int) -> None:
    """Raises ``TooLarge`` when the declared length is over ``limit``, or not a number."""
    length = headers.get("content-length")
    if length is not None and (not length.strip().isdigit() or int(length) > limit):
        raise TooLarge


async def read_form(request, limit: int) -> dict:
    """A Starlette request's URL-encoded form, read no further than ``limit`` bytes."""
    check_length(request.headers, limit)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > limit:
            raise TooLarge
    return parse_form(bytes(body))


def parse_form(body: bytes) -> dict:
    try:
        pairs = parse_qsl(body.decode("utf-8"), keep_blank_values=True, max_num_fields=MAX_FIELDS)
    except (UnicodeDecodeError, ValueError):
        return {}
    return dict(pairs)
