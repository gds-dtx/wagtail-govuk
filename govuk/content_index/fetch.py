"""Fetching a source page for the content index, read only into memory.

Through the content discovery fetcher's guards, as the catalogue import's
GitHub downloads are: no private or loopback addresses, checked again on
every redirect. On top of them, https only, a cap on how much is read, and
the conditional request headers that let an unchanged page answer 304.

``Robots`` reads each host's robots.txt once and is asked before every page.
"""

from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request
from urllib.robotparser import RobotFileParser

from django.conf import settings

from govuk.content_discovery import (
    ContentDiscoveryError,
    _open_source_request,
    _validate_source_fetch_dns_resolution,
    _validate_source_fetch_url,
)

# What robots.txt rules name; the User-Agent header adds a version and where to
# find who is asking.
ROBOTS_AGENT = "wagtail-govuk-content-index"
MAX_BYTES = 5 * 1024 * 1024
MAX_ROBOTS_BYTES = 512 * 1024
TIMEOUT_SECONDS = 10.0
HTML_ACCEPT = "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1"


class FetchError(Exception):
    """A fetch that failed. ``status`` is the HTTP status, when there was one."""

    def __init__(self, message: str, *, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass
class Response:
    status: int
    url: str
    headers: dict[str, str]
    body: bytes

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "").split(";")[0].strip().lower()


def user_agent() -> str:
    site = getattr(settings, "BASE_URL", "") or getattr(settings, "WAGTAILADMIN_BASE_URL", "")
    return f"{ROBOTS_AGENT}/1.0" + (f" (+{site})" if site else "")


def fetch(
    url: str,
    *,
    accept: str = HTML_ACCEPT,
    etag: str = "",
    last_modified: str = "",
    max_bytes: int = MAX_BYTES,
    timeout: float = TIMEOUT_SECONDS,
) -> Response:
    if urlsplit(url).scheme != "https":
        raise FetchError("Only https addresses are fetched.")
    headers = {"User-Agent": user_agent(), "Accept": accept}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    try:
        _validate_source_fetch_url(url)
        _validate_source_fetch_dns_resolution(url)
        with _open_source_request(
            Request(url, headers=headers), timeout=timeout, disable_tls_verification=False
        ) as response:
            final_url = response.geturl()
            status = response.status
            response_headers = {name.lower(): value for name, value in response.headers.items()}
            body = response.read(max_bytes + 1)
    except ContentDiscoveryError as exc:
        raise FetchError(str(exc)) from exc
    except HTTPError as exc:
        exc.close()  # an error answer is a response too, with a socket to give back
        if exc.code == 304:
            return Response(304, url, {name.lower(): value for name, value in exc.headers.items()}, b"")
        raise FetchError(f"The source answered {exc.code}.", status=exc.code) from exc
    except (URLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise FetchError(f"Could not reach the source: {reason}.") from exc
    if urlsplit(final_url).scheme != "https":
        raise FetchError("The source redirected to an address that is not https.")
    if len(body) > max_bytes:
        raise FetchError(f"The page is larger than {max_bytes // (1024 * 1024)} MB.")
    return Response(status, final_url, response_headers, body)


class Robots:
    """Each host's robots.txt, read once, for as long as this lives (one run).

    As RFC 9309 has it: a robots.txt that is missing or refused (any 4xx)
    allows everything; one that cannot be read (5xx, or the host cannot be
    reached) allows nothing, for now.
    """

    def __init__(self, fetcher=None):
        self._fetch = fetcher or fetch
        self._rules: dict[str, RobotFileParser | bool] = {}

    def allows(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._rules:
            self._rules[origin] = self._read(origin)
        rules = self._rules[origin]
        if isinstance(rules, bool):
            return rules
        return rules.can_fetch(ROBOTS_AGENT, url)

    def _read(self, origin: str) -> RobotFileParser | bool:
        try:
            response = self._fetch(f"{origin}/robots.txt", accept="text/plain", max_bytes=MAX_ROBOTS_BYTES)
        except FetchError as exc:
            return bool(exc.status and 400 <= exc.status < 500)
        rules = RobotFileParser()
        rules.parse(response.body.decode("utf-8", "replace").splitlines())
        return rules
