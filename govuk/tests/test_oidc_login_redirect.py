from unittest.mock import PropertyMock, patch
from urllib.parse import parse_qs, urlsplit

from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory, TestCase, override_settings
from django.urls import resolve, reverse

from govuk.adapters import AccountAdapter
from govuk.oidc import (
    ADMIN_OIDC_NEXT_URL_KEY,
    SessionOIDCCallbackAdapter,
    build_oidc_login_url,
)
from govuk.views import oidc_login


def _add_session(request):
    middleware = SessionMiddleware(lambda req: None)
    middleware.process_request(request)
    request.session.save()
    return request


class OIDCLoginRedirectViewTests(TestCase):
    def test_login_redirect_preserves_safe_relative_next_url(self):
        response = self.client.get(
            reverse("account_login"),
            data={"next": "/admin/pages/123/edit/"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"],
            build_oidc_login_url("/admin/pages/123/edit/"),
        )

    def test_login_redirect_ignores_external_next_url(self):
        response = self.client.get(
            reverse("account_login"),
            data={"next": "https://evil.example/phish"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], build_oidc_login_url())

    def test_login_redirect_falls_back_to_safe_session_next_url(self):
        session = self.client.session
        session[ADMIN_OIDC_NEXT_URL_KEY] = "/admin/"
        session.save()

        response = self.client.get(
            reverse("account_login"),
            data={"next": "https://evil.example/phish"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], build_oidc_login_url("/admin/"))


class OIDCProviderLoginTests(TestCase):
    openid_config = {
        "authorization_endpoint": "https://sso.service.security.gov.uk/auth/oidc",
        "token_endpoint": "https://sso.service.security.gov.uk/token",
        "userinfo_endpoint": "https://sso.service.security.gov.uk/userinfo",
        "jwks_uri": "https://sso.service.security.gov.uk/.well-known/jwks.json",
        "issuer": "https://sso.service.security.gov.uk",
    }
    socialaccount_providers = {
        "openid_connect": {
            "APPS": [
                {
                    "provider_id": "internal-access",
                    "name": "Internal Access",
                    "client_id": "test-client-id",
                    "secret": "test-client-secret",
                    "settings": {
                        "fetch_userinfo": True,
                        "server_url": (
                            "https://sso.service.security.gov.uk/.well-known/"
                            "openid-configuration"
                        ),
                        "token_auth_method": "client_secret_basic",
                        "uid_field": "sub",
                    },
                }
            ],
        }
    }

    def test_oidc_login_route_uses_project_view(self):
        match = resolve("/accounts/oidc/internal-access/login/")

        self.assertIs(match.func, oidc_login)

    @override_settings(
        ALLOWED_HOSTS=["gds-digital-001.dev.wagtail.ukps.digital"],
        BASE_URL="https://gds-digital-001.dev.wagtail.ukps.digital",
        SOCIALACCOUNT_PROVIDERS=socialaccount_providers,
    )
    @patch.object(
        SessionOIDCCallbackAdapter,
        "openid_config",
        new_callable=PropertyMock,
    )
    def test_authorize_redirect_uses_configured_base_url_without_origin_port(
        self, mock_openid_config
    ):
        mock_openid_config.return_value = self.openid_config

        response = self.client.get(
            "/accounts/oidc/internal-access/login/",
            HTTP_HOST="gds-digital-001.dev.wagtail.ukps.digital:8443",
            SERVER_PORT="8443",
        )

        self.assertEqual(response.status_code, 302)
        location = urlsplit(response["Location"])
        params = parse_qs(location.query)
        self.assertEqual(location.scheme, "https")
        self.assertEqual(location.netloc, "sso.service.security.gov.uk")
        expected_redirect_uri = (
            "https://gds-digital-001.dev.wagtail.ukps.digital"
            "/accounts/oidc/internal-access/login/callback/"
        )
        self.assertEqual(
            params["redirect_uri"],
            [expected_redirect_uri],
        )

    @override_settings(
        ALLOWED_HOSTS=["gds-cyber-001.dev.wagtail.ukps.digital"],
        BASE_URL="",
        WAGTAILADMIN_BASE_URL="https://gds-cyber-001.dev.wagtail.ukps.digital",
        SOCIALACCOUNT_PROVIDERS=socialaccount_providers,
    )
    @patch.object(
        SessionOIDCCallbackAdapter,
        "openid_config",
        new_callable=PropertyMock,
    )
    def test_authorize_redirect_falls_back_to_wagtailadmin_base_url(
        self, mock_openid_config
    ):
        mock_openid_config.return_value = self.openid_config

        response = self.client.get(
            "/accounts/oidc/internal-access/login/",
            HTTP_HOST="gds-cyber-001.dev.wagtail.ukps.digital:10443",
            SERVER_PORT="10443",
        )

        params = parse_qs(urlsplit(response["Location"]).query)
        expected_redirect_uri = (
            "https://gds-cyber-001.dev.wagtail.ukps.digital"
            "/accounts/oidc/internal-access/login/callback/"
        )
        self.assertEqual(
            params["redirect_uri"],
            [expected_redirect_uri],
        )


class AccountAdapterTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_get_login_redirect_url_returns_safe_session_next_url(self):
        request = _add_session(self.factory.get("/accounts/profile/"))
        request.session[ADMIN_OIDC_NEXT_URL_KEY] = "/admin/"
        request.session.save()

        redirect_url = AccountAdapter().get_login_redirect_url(request)

        self.assertEqual(redirect_url, "/admin/")

    def test_get_login_redirect_url_ignores_external_session_next_url(self):
        request = _add_session(self.factory.get("/accounts/profile/"))
        request.session[ADMIN_OIDC_NEXT_URL_KEY] = "https://evil.example/phish"
        request.session.save()

        with patch(
            "allauth.account.adapter.DefaultAccountAdapter.get_login_redirect_url",
            return_value="/accounts/profile/",
        ) as mock_super:
            redirect_url = AccountAdapter().get_login_redirect_url(request)

        self.assertEqual(redirect_url, "/accounts/profile/")
        mock_super.assert_called_once_with(request)
