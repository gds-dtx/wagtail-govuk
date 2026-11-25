"""The OAuth discovery documents, served by the service and by Django.

Deployed, only /mcp reaches the service, so the root /.well-known/ paths are Django's.
"""

from mcp.server.auth.routes import build_metadata
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import ProtectedResourceMetadata
from pydantic import AnyHttpUrl

from . import MCP_PATH
from .auth import OAUTH_PATH, SCOPE, issuer_url, resource_url

REGISTRATION = ClientRegistrationOptions(enabled=True, valid_scopes=[SCOPE], default_scopes=[SCOPE])
REVOCATION = RevocationOptions(enabled=True)

AUTHORIZATION_SERVER_PATH = f"/.well-known/oauth-authorization-server{OAUTH_PATH}"
PROTECTED_RESOURCE_PATH = f"/.well-known/oauth-protected-resource{MCP_PATH}"


def authorization_server() -> dict:
    metadata = build_metadata(AnyHttpUrl(issuer_url()), None, REGISTRATION, REVOCATION)
    # No refresh tokens are issued: an hour on, the client sends the reader back through sign-in.
    metadata.grant_types_supported = ["authorization_code"]
    return metadata.model_dump(mode="json", exclude_none=True)


def protected_resource() -> dict:
    metadata = ProtectedResourceMetadata(
        resource=AnyHttpUrl(resource_url()),
        authorization_servers=[AnyHttpUrl(issuer_url())],
        scopes_supported=[SCOPE],
    )
    return metadata.model_dump(mode="json", exclude_none=True)
