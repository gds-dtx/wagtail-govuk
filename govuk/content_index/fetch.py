"""Fetches a source page into memory for the content index.

Uses the content discovery SSRF guards, plus https only and a size cap.
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

# The name robots.txt rules match; the User-Agent adds a version and site URL.
ROBOTS_AGENT = "wagtail-govuk-content-index"
MAX_BYTES = 5 * 1024 * 1024
MAX_ROBOTS_BYTES = 512 * 1024
TIMEOUT_SECONDS = 10.0
HTML_ACCEPT = "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1"


class FetchError(Exception):
    """``status`` is the HTTP status, when there was one."""

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
        exc.close()  # release the socket
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
    """Each host's robots.txt, read once per run.

    Per RFC 9309, a 4xx allows everything; a 5xx or unreachable host allows nothing.
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
