"""The MCP server's routes on the ASGI service.

Stateless so any container can answer any request. Every call needs a token
from the site's own OAuth (``auth``), so a signed-in account. DNS rebinding
protection allows only ``ALLOWED_HOSTS``; browser origins only locally in
development (for the MCP Inspector).
"""

from django.conf import settings
from mcp.server.auth.provider import ProviderTokenVerifier
from mcp.server.auth.routes import create_auth_routes
from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyHttpUrl
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from govuk.features import ai_enabled
from govuk.service.app import Contribution, RateLimit

from . import MCP_PATH, metadata
from .auth import OAUTH_PATH, SCOPE, SiteOAuthProvider, issuer_url, resource_url
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
    provider = SiteOAuthProvider()
    auth = AuthSettings(
        issuer_url=AnyHttpUrl(issuer_url()),
        resource_server_url=AnyHttpUrl(resource_url()),
        required_scopes=[SCOPE],
        validate_token_resource=True,
    )
    app = create_server(auth=auth, token_verifier=ProviderTokenVerifier(provider)).streamable_http_app(
        streamable_http_path=MCP_PATH,
        json_response=True,
        stateless_http=True,
        transport_security=transport_security(),
    )
    oauth = Mount(
        OAUTH_PATH,
        routes=create_auth_routes(
            provider=provider,
            issuer_url=AnyHttpUrl(issuer_url()),
            client_registration_options=metadata.REGISTRATION,
            revocation_options=metadata.REVOCATION,
        ),
    )
    # For a client that reaches the service directly, as in development.
    discovery = Route(
        metadata.AUTHORIZATION_SERVER_PATH, lambda request: JSONResponse(metadata.authorization_server())
    )
    return Contribution(
        name="mcp",
        routes=[*app.routes, oauth, discovery],
        lifespan=lambda: app.router.lifespan_context(app),
        rate_limits=[RateLimit(prefix=MCP_PATH, namespace="mcp", limit=settings.MCP_RATE_LIMIT_PER_MINUTE)],
        # The SDK checks bearer tokens in middleware, which copying its routes alone would leave out.
        middleware=list(app.user_middleware),
    )
