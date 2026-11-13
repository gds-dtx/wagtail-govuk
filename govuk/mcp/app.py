"""The uvicorn service's HTTP app: the MCP server, and Ask's summary.

The MCP server (``FEATURE_MCP``) is the SDK's app at ``MCP_PATH``. Ask's
summary (``FEATURE_ASK``) is a route beside it at ``SUMMARY_PATH``: a call to
Claude takes seconds, which this async service can wait out where the site's
synchronous gunicorn workers would be tied up. Either may be on without the
other. Around them, the site's own rules:

- **Stateless, JSON responses.** No session outlives a request, so any
  process, on any container behind the load balancer, can answer any request.
- **Hosts.** The SDK's DNS rebinding protection is on, answering only the
  hosts Django does (``ALLOWED_HOSTS``). Requests with an ``Origin`` header
  (from a browser) are refused unless it is local, in development, where the
  MCP Inspector runs in one.
- **Maintenance.** Closed with a 503 while the site is: the emergency
  ``MAINTENANCE_MODE`` and the planned Maintenance mode setting alike, since
  there is no signed-in staff member here to let through.
- **A rate limit** of ``MCP_RATE_LIMIT_PER_MINUTE`` requests to ``/mcp`` per
  client address (from ``MCP_CLIENT_IP_HEADER`` behind CloudFront), and of
  ``ASK_SUMMARY_RATE_LIMIT_PER_MINUTE`` summaries. They are a second line: the
  WAF's limit is the first.

The health check at ``HEALTH_PATH`` is exempt from the last two, as the
site's own is, or the orchestrator would replace a closed or busy container.
"""

import logging

from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from wagtail.models import Site

from govuk import rate_limit
from govuk.ask.summary import SUMMARY_PATH, UNAVAILABLE
from govuk.content_index import ask_enabled, mcp_enabled
from govuk.models import MaintenanceModeSettings
from govuk.rate_limit import first_address

from . import HEALTH_PATH, MCP_PATH
from .server import create_server, health
from .tools import release_connections

logger = logging.getLogger(__name__)

RATE_LIMIT_WINDOW_SECONDS = rate_limit.DEFAULT_WINDOW_SECONDS
LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]")


def transport_security() -> TransportSecuritySettings:
    hosts = list(settings.ALLOWED_HOSTS)
    if not hosts and settings.DEBUG:
        hosts = list(LOCAL_HOSTS)  # what Django itself allows in development
    if "*" in hosts:
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)
    allowed_hosts = []
    for host in hosts:
        # A leading dot is Django's "and every subdomain", which the SDK has no
        # way to say; the domain itself is still allowed.
        host = host.lstrip(".")
        allowed_hosts += [host, f"{host}:*"]
    allowed_origins = (
        [f"http://{host}:*" for host in LOCAL_HOSTS] + [f"http://{host}" for host in LOCAL_HOSTS]
        if settings.DEBUG
        else []
    )
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
    )


def _maintenance_on() -> bool:
    if settings.MAINTENANCE_MODE:
        return True
    try:
        site = Site.objects.filter(is_default_site=True).first()
        return bool(site and MaintenanceModeSettings.for_site(site).enabled)
    finally:
        release_connections()


class MaintenanceMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"] != HEALTH_PATH:
            if await sync_to_async(_maintenance_on)():
                response = JSONResponse(
                    {"error": "This service is closed for maintenance. Try again later."},
                    status_code=503,
                    headers={"Retry-After": str(settings.MAINTENANCE_RETRY_AFTER)},
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


class RateLimitMiddleware:
    """At most ``limit`` requests a minute from one client address.

    Counted in fixed one-minute windows in the default cache. The address is
    the one in ``client_ip_header`` (``MCP_CLIENT_IP_HEADER``), when that is
    set and the request carries it; otherwise the connection's, which uvicorn
    takes from ``X-Forwarded-For`` as far as ``MCP_FORWARDED_ALLOW_IPS`` lets it.
    """

    def __init__(
        self, app, *, limit: int, window: int = RATE_LIMIT_WINDOW_SECONDS, client_ip_header: str = ""
    ):
        self.app = app
        self.limit = limit
        self.window = window
        self.client_ip_header = client_ip_header

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "") if scope["type"] == "http" else ""
        is_mcp = path == MCP_PATH or (path.startswith(MCP_PATH + "/") and path != HEALTH_PATH)
        if not is_mcp or self.limit <= 0:
            await self.app(scope, receive, send)
            return
        client = scope_client(scope, self.client_ip_header)
        count, retry_after = await rate_limit.ahit("mcp", client, window=self.window)
        if count > self.limit:
            if count == self.limit + 1:
                logger.warning("MCP rate limit reached", extra={"limit": self.limit})
            response = JSONResponse(
                {"error": "Too many requests. Try again in a minute."},
                status_code=429,
                headers={"Retry-After": str(retry_after)},
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


def scope_client(scope, client_ip_header: str) -> str:
    """The client's address for an ASGI request: the header's, else the connection's."""
    if client_ip_header:
        wanted = client_ip_header.lower().encode("latin-1")
        for name, value in scope.get("headers") or []:
            if name == wanted:
                address = first_address(value.decode("latin-1"))
                if address:
                    return address
    return (scope.get("client") or ("unknown", 0))[0]


def _summary(ticket: str) -> tuple[int, dict]:
    from govuk.ask.views import summary_response

    try:
        return summary_response(ticket, Site.objects.filter(is_default_site=True).first())
    finally:
        release_connections()


async def summary_endpoint(request):
    """Ask's summary (``govuk.ask.summary``), from the ticket the results page carries."""
    limit = settings.ASK_SUMMARY_RATE_LIMIT_PER_MINUTE
    headers = {"Cache-Control": "no-store, private", "X-Robots-Tag": "noindex"}
    if limit > 0:
        count, retry_after = await rate_limit.ahit(
            "ask-summary", scope_client(request.scope, settings.CLIENT_IP_HEADER)
        )
        if count > limit:
            return JSONResponse({"status": UNAVAILABLE}, status_code=429, headers={**headers, "Retry-After": str(retry_after)})
    form = await request.form()
    # A worker thread of its own: the call to Claude takes seconds, and the
    # summary reads the database, which async code may not.
    status, body = await sync_to_async(_summary, thread_sensitive=False)(str(form.get("ticket") or ""))
    return JSONResponse(body, status_code=status, headers=headers)


def create_app():
    if not (mcp_enabled() or ask_enabled()):
        raise ImproperlyConfigured("The MCP server and Ask are both off (FEATURE_MCP and FEATURE_ASK).")
    if mcp_enabled():
        app = create_server().streamable_http_app(
            streamable_http_path=MCP_PATH,
            json_response=True,
            stateless_http=True,
            transport_security=transport_security(),
        )
    else:
        # Ask alone: the health check, which the load balancer still probes.
        app = Starlette(routes=[Route(HEALTH_PATH, health, methods=["GET"])])
    if ask_enabled():
        app.router.routes.append(Route(SUMMARY_PATH, summary_endpoint, methods=["POST"]))
    # The last added runs first: the rate limit, then maintenance.
    app.add_middleware(MaintenanceMiddleware)
    app.add_middleware(
        RateLimitMiddleware,
        limit=settings.MCP_RATE_LIMIT_PER_MINUTE,
        client_ip_header=settings.MCP_CLIENT_IP_HEADER,
    )
    return app
