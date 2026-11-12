"""The MCP server's HTTP app: the SDK's, with the site's own rules around it.

- **Stateless, JSON responses.** No session outlives a request, so any
  process, on any container behind the load balancer, can answer any request.
- **Hosts.** The SDK's DNS rebinding protection is on, answering only the
  hosts Django does (``ALLOWED_HOSTS``). Requests with an ``Origin`` header
  (from a browser) are refused unless it is local, in development, where the
  MCP Inspector runs in one.
- **Maintenance.** Closed with a 503 while the site is: the emergency
  ``MAINTENANCE_MODE`` and the planned Maintenance mode setting alike, since
  there is no signed-in staff member here to let through.
- **A rate limit** of ``MCP_RATE_LIMIT_PER_MINUTE`` requests per client
  address. It is a second line: the load balancer's limit is the first.

The health check at ``HEALTH_PATH`` is exempt from the last two, as the
site's own is, or the orchestrator would replace a closed or busy container.
"""

import logging
import time

from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse
from wagtail.models import Site

from govuk.content_index import content_index_enabled
from govuk.models import MaintenanceModeSettings

from . import HEALTH_PATH, MCP_PATH
from .server import create_server
from .tools import release_connections

logger = logging.getLogger(__name__)

RATE_LIMIT_WINDOW_SECONDS = 60
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
    the one uvicorn settles on from ``X-Forwarded-For``, believing only the
    proxies in ``MCP_FORWARDED_ALLOW_IPS``.
    """

    def __init__(self, app, *, limit: int, window: int = RATE_LIMIT_WINDOW_SECONDS):
        self.app = app
        self.limit = limit
        self.window = window

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or self.limit <= 0 or scope["path"] == HEALTH_PATH:
            await self.app(scope, receive, send)
            return
        client = (scope.get("client") or ("unknown", 0))[0]
        now = time.time()
        key = f"govuk-mcp-rate:{client}:{int(now // self.window)}"
        if await cache.aadd(key, 1, timeout=self.window * 2):
            count = 1
        else:
            try:
                count = await cache.aincr(key)
            except ValueError:  # the window ended between the two calls
                count = 1
        if count > self.limit:
            if count == self.limit + 1:
                logger.warning("MCP rate limit reached", extra={"limit": self.limit})
            response = JSONResponse(
                {"error": "Too many requests. Try again in a minute."},
                status_code=429,
                headers={"Retry-After": str(self.window - int(now % self.window))},
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


def create_app():
    if not content_index_enabled():
        raise ImproperlyConfigured("The MCP server is off (FEATURE_MCP).")
    server = create_server()
    app = server.streamable_http_app(
        streamable_http_path=MCP_PATH,
        json_response=True,
        stateless_http=True,
        transport_security=transport_security(),
    )
    # The last added runs first: the rate limit, then maintenance.
    app.add_middleware(MaintenanceMiddleware)
    app.add_middleware(RateLimitMiddleware, limit=settings.MCP_RATE_LIMIT_PER_MINUTE)
    return app
