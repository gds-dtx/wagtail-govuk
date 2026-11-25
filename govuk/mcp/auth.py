"""The MCP server's OAuth: the SDK's handlers, with the site's SSO sign-in as the consent step.

A client registers (RFC 7591), sends the reader to /mcp/oauth/authorize, and
the reader signs in and agrees on the site (``views``); the client then swaps
the one-time code, with its PKCE verifier, for a one-hour access token. The SDK
checks the PKCE verifier, redirect URI and code expiry; this stores and loads.
"""

import hashlib
import secrets
from datetime import timedelta
from ipaddress import ip_address
from urllib.parse import urlsplit

from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RegistrationError,
    TokenError,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from govuk.models import (
    MCPAccessToken,
    MCPAuthorisationCode,
    MCPAuthorisationRequest,
    MCPClient,
)
from govuk.service.db import release_connections

from . import MCP_PATH

SCOPE = "mcp"
OAUTH_PATH = f"{MCP_PATH}/oauth"
REQUEST_LIFETIME = timedelta(minutes=10)
CODE_LIFETIME = timedelta(minutes=1)
TOKEN_LIFETIME = timedelta(hours=1)
LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1")


def mcp_base_url() -> str:
    return settings.MCP_BASE_URL or settings.WAGTAILADMIN_BASE_URL.rstrip("/")


def resource_url() -> str:
    """The MCP server's resource identifier (RFC 8707); every token is bound to it."""
    return f"{mcp_base_url()}{MCP_PATH}"


def issuer_url() -> str:
    return f"{mcp_base_url()}{OAUTH_PATH}"


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def acceptable_redirect_uri(uri: str) -> bool:
    """https anywhere; http only back to this machine, which is where desktop clients listen."""
    parts = urlsplit(uri)
    if parts.scheme == "https" and parts.hostname:
        return True
    if parts.scheme != "http" or not parts.hostname:
        return False
    host = parts.hostname
    if host in LOOPBACK_HOSTS:
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def _in_thread(function):
    """Database work off the event loop, with the connection released after."""

    def run(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        finally:
            release_connections()

    return sync_to_async(run, thread_sensitive=False)


def _expired_rows_removed() -> None:
    now = timezone.now()
    MCPAuthorisationRequest.objects.filter(expires_at__lt=now).delete()
    MCPAuthorisationCode.objects.filter(expires_at__lt=now).delete()
    MCPAccessToken.objects.filter(expires_at__lt=now - timedelta(days=1)).delete()


class SiteOAuthProvider:
    """``OAuthAuthorizationServerProvider`` for the SDK, backed by the database."""

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return await _in_thread(self._get_client)(client_id)

    def _get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        client = MCPClient.objects.filter(client_id=client_id).first()
        return OAuthClientInformationFull.model_validate(client.information) if client else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        for uri in client_info.redirect_uris or []:
            if not acceptable_redirect_uri(str(uri)):
                raise RegistrationError(
                    "invalid_redirect_uri", "Redirect URIs must use https, or http back to this machine."
                )
        await _in_thread(self._register)(client_info)

    def _register(self, client_info: OAuthClientInformationFull) -> None:
        MCPClient.objects.create(
            client_id=client_info.client_id,
            client_secret=client_info.client_secret or "",
            name=(client_info.client_name or "")[:200],
            information=client_info.model_dump(mode="json"),
        )

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        """Holds the request and sends the reader to the site to sign in and agree."""
        resource = params.resource or resource_url()
        if resource.rstrip("/") != resource_url():
            raise AuthorizeError("invalid_target", "Tokens here are only for this site's MCP server.")
        request_id = await _in_thread(self._hold_request)(client.client_id, params, resource)
        path = reverse("govuk_mcp_authorise", args=[request_id])
        return f"{settings.WAGTAILADMIN_BASE_URL.rstrip('/')}{path}"

    def _hold_request(self, client_id: str, params: AuthorizationParams, resource: str) -> str:
        _expired_rows_removed()
        request_id = secrets.token_urlsafe(32)
        MCPAuthorisationRequest.objects.create(
            request_id=request_id,
            client=MCPClient.objects.get(client_id=client_id),
            redirect_uri=str(params.redirect_uri),
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            code_challenge=params.code_challenge,
            state=params.state or "",
            scopes=params.scopes or [SCOPE],
            resource=resource_url(),
            expires_at=timezone.now() + REQUEST_LIFETIME,
        )
        return request_id

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        return await _in_thread(self._load_code)(client.client_id, authorization_code)

    def _load_code(self, client_id: str, code: str) -> AuthorizationCode | None:
        row = (
            MCPAuthorisationCode.objects.select_related("client")
            .filter(code_hash=digest(code), client__client_id=client_id)
            .first()
        )
        if row is None:
            return None
        return AuthorizationCode(
            code=code,
            scopes=row.scopes,
            expires_at=row.expires_at.timestamp(),
            client_id=client_id,
            code_challenge=row.code_challenge,
            redirect_uri=row.redirect_uri,
            redirect_uri_provided_explicitly=row.redirect_uri_provided_explicitly,
            resource=row.resource,
            subject=str(row.user_id),
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        token = await _in_thread(self._exchange)(client.client_id, authorization_code.code)
        return OAuthToken(
            access_token=token,
            token_type="Bearer",
            expires_in=int(TOKEN_LIFETIME.total_seconds()),
            scope=" ".join(authorization_code.scopes),
        )

    def _exchange(self, client_id: str, code: str) -> str:
        with transaction.atomic():
            row = (
                MCPAuthorisationCode.objects.select_for_update()
                .select_related("client", "user")
                .filter(code_hash=digest(code), client__client_id=client_id)
                .first()
            )
            # Gone if a request at the same moment used it first: a code is good once.
            if row is None or not row.user.is_active:
                raise TokenError("invalid_grant", "The code is not valid.")
            token = secrets.token_urlsafe(32)
            MCPAccessToken.objects.create(
                token_hash=digest(token),
                client=row.client,
                user=row.user,
                scopes=row.scopes,
                resource=row.resource,
                expires_at=timezone.now() + TOKEN_LIFETIME,
            )
            row.delete()
        return token

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str):
        return None  # none are issued: a token lasts an hour, then the reader signs in again

    async def exchange_refresh_token(self, client, refresh_token, scopes):
        raise TokenError("unsupported_grant_type", "Refresh tokens are not issued.")

    async def load_access_token(self, token: str) -> AccessToken | None:
        return await _in_thread(self._load_token)(token)

    def _load_token(self, token: str) -> AccessToken | None:
        row = (
            MCPAccessToken.objects.select_related("client", "user")
            .filter(token_hash=digest(token), revoked=False, expires_at__gt=timezone.now())
            .first()
        )
        if row is None or not row.user.is_active:
            return None
        return AccessToken(
            token=token,
            client_id=row.client.client_id,
            scopes=row.scopes,
            expires_at=int(row.expires_at.timestamp()),
            resource=row.resource,
            subject=str(row.user_id),
        )

    async def revoke_token(self, token) -> None:
        await _in_thread(self._revoke)(token.token)

    def _revoke(self, token: str) -> None:
        MCPAccessToken.objects.filter(token_hash=digest(token)).update(revoked=True)


def issue_code(request: MCPAuthorisationRequest, user) -> str:
    """The one-time code for an agreed request, which is used up by this."""
    code = secrets.token_urlsafe(32)
    MCPAuthorisationCode.objects.create(
        code_hash=digest(code),
        client=request.client,
        user=user,
        redirect_uri=request.redirect_uri,
        redirect_uri_provided_explicitly=request.redirect_uri_provided_explicitly,
        code_challenge=request.code_challenge,
        scopes=request.scopes,
        resource=request.resource,
        expires_at=timezone.now() + CODE_LIFETIME,
    )
    request.delete()
    return code


def revoke_for(user, client: MCPClient) -> None:
    MCPAccessToken.objects.filter(user=user, client=client, revoked=False).update(revoked=True)
