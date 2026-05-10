from __future__ import annotations

from urllib.parse import urlencode, urljoin

from allauth import app_settings as allauth_settings
from allauth.account.internal.decorators import login_not_required
from allauth.socialaccount.adapter import get_adapter
from allauth.socialaccount.models import SocialApp
from allauth.socialaccount.providers.base.utils import respond_to_login_on_get
from allauth.socialaccount.providers.oauth2.views import OAuth2CallbackView
from allauth.socialaccount.providers.openid_connect.views import (
    OpenIDConnectOAuth2Adapter,
)
from allauth.utils import build_absolute_uri
from django.conf import settings
from django.http import Http404
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

ADMIN_OIDC_NEXT_URL_KEY = "oidc_next_url"
OIDC_ID_TOKEN_SESSION_KEY = "oidc_id_token"


def build_oidc_callback_url(
    request,
    provider_id: str,
    protocol: str | None = None,
) -> str:
    callback_path = reverse(
        "openid_connect_callback", kwargs={"provider_id": provider_id}
    )
    redirect_base_url = (
        getattr(settings, "BASE_URL", None)
        or getattr(settings, "WAGTAILADMIN_BASE_URL", None)
        or ""
    ).rstrip("/")
    if redirect_base_url:
        return urljoin(f"{redirect_base_url}/", callback_path.lstrip("/"))

    return build_absolute_uri(request, callback_path, protocol)


def build_oidc_login_url(next_url: str | None = None) -> str:
    provider_id = getattr(settings, "OIDC_PROVIDER_ID", "internal-access")
    base_url = f"/accounts/oidc/{provider_id}/login/"
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
    """Uses the public callback URL and stores the raw ID token in session."""

    def get_callback_url(self, request, app):
        return build_oidc_callback_url(
            request,
            self.provider_id,
            self.redirect_uri_protocol,
        )

    def complete_login(self, request, app, token, **kwargs):
        id_token = kwargs.get("response", {}).get("id_token")
        if id_token:
            request.session[OIDC_ID_TOKEN_SESSION_KEY] = id_token
        return super().complete_login(request, app, token, **kwargs)


@login_not_required
def oidc_login(request, provider_id):
    if allauth_settings.HEADLESS_ONLY:
        raise Http404

    try:
        provider = get_adapter(request).get_provider(request, provider=provider_id)
        provider.oauth2_adapter_class = SessionOIDCCallbackAdapter
        response = respond_to_login_on_get(request, provider)
        if response:
            return response
        return provider.redirect_from_request(request)
    except SocialApp.DoesNotExist:
        raise Http404


def oidc_callback(request, provider_id):
    try:
        view = OAuth2CallbackView.adapter_view(
            SessionOIDCCallbackAdapter(request, provider_id)
        )
        return view(request)
    except SocialApp.DoesNotExist:
        raise Http404
