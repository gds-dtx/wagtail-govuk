from __future__ import annotations

from urllib.parse import urlencode

from allauth.socialaccount.models import SocialApp
from allauth.socialaccount.providers.oauth2.views import OAuth2CallbackView
from allauth.socialaccount.providers.openid_connect.views import (
    OpenIDConnectOAuth2Adapter,
)
from django.conf import settings
from django.http import Http404
from django.utils.http import url_has_allowed_host_and_scheme

ADMIN_OIDC_NEXT_URL_KEY = "oidc_next_url"
OIDC_ID_TOKEN_SESSION_KEY = "oidc_id_token"


def build_oidc_login_url(next_url: str | None = None) -> str:
    prefix = settings.SOCIALACCOUNT_OPENID_CONNECT_URL_PREFIX
    provider_id = getattr(settings, "OIDC_PROVIDER_ID", "internal-access")
    base_url = f"/accounts/{prefix}/{provider_id}/login/"
    if next_url:
        return f"{base_url}?{urlencode({'next': next_url})}"
    return base_url


def safe_oidc_next_url(request, next_url: str | None) -> str | None:
    candidate = (next_url or "").strip()
    if not candidate:
        return None

    if url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidate

    return None


def build_oidc_logout_url() -> str:
    end_session_url = getattr(
        settings, "OIDC_END_SESSION_URL", "https://sso.service.security.gov.uk/sign-out"
    )
    client_id = getattr(settings, "OIDC_CLIENT_ID", None)
    if client_id:
        return f"{end_session_url}?{urlencode({'client_id': client_id})}"
    return end_session_url


class SessionOIDCCallbackAdapter(OpenIDConnectOAuth2Adapter):
    """Stores the raw OIDC ID token in session for the signed-in user."""

    def complete_login(self, request, app, token, **kwargs):
        id_token = kwargs.get("response", {}).get("id_token")
        if id_token:
            request.session[OIDC_ID_TOKEN_SESSION_KEY] = id_token
        return super().complete_login(request, app, token, **kwargs)


def oidc_callback(request, provider_id):
    try:
        view = OAuth2CallbackView.adapter_view(
            SessionOIDCCallbackAdapter(request, provider_id)
        )
        return view(request)
    except SocialApp.DoesNotExist:
        raise Http404
