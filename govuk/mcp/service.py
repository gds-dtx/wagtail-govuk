"""The MCP server's routes on the ASGI service.

Stateless so any container can answer any request. DNS rebinding protection
allows only ``ALLOWED_HOSTS``; browser origins only locally in development
(for the MCP Inspector).
"""

from django.conf import settings
from mcp.server.transport_security import TransportSecuritySettings

from govuk.features import ai_enabled
from govuk.service.app import Contribution, RateLimit

from . import MCP_PATH
from .server import create_server

LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]")


def transport_security() -> TransportSecuritySettings:
    hosts = list(settings.ALLOWED_HOSTS)
    if not hosts and settings.DEBUG:
        hosts = list(LOCAL_HOSTS)  # what Django itself allows in development
    if "*" in hosts:
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)
    allowed_hosts = []
    for host in hosts:
        # The SDK cannot express Django's leading-dot subdomain wildcard.
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


def contribution() -> Contribution | None:
    if not ai_enabled():
        return None
    app = create_server().streamable_http_app(
        streamable_http_path=MCP_PATH,
        json_response=True,
        stateless_http=True,
        transport_security=transport_security(),
    )
    return Contribution(
        name="mcp",
        routes=list(app.routes),
        lifespan=lambda: app.router.lifespan_context(app),
        rate_limits=[RateLimit(prefix=MCP_PATH, namespace="mcp", limit=settings.MCP_RATE_LIMIT_PER_MINUTE)],
    )
