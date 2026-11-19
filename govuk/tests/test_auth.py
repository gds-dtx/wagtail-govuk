from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import PropertyMock, patch
from urllib.parse import parse_qs, urlsplit

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import resolve, reverse
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.request import Request
from wagtail.models import Site

from govuk.adapters import AccountAdapter
from govuk.api import WagtailDocuments, WagtailImages, WagtailPages
from govuk.authentication import InternalAccessJWTAuthentication, TokenUser
from govuk.middleware import AuthenticatedUserRedirectMiddleware
from govuk.models import AuthenticatedRedirectRule, AuthenticatedRedirectSettings
from govuk.oidc import (
    ADMIN_OIDC_NEXT_URL_KEY,
    SessionOIDCCallbackAdapter,
    build_oidc_login_url,
)
from govuk.views import oidc_login

_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)

_OIDC_TOKEN_AUTH = {
    "ALGORITHM": "RS256",
    "JWKS_URL": "https://sso.example.gov.uk/.well-known/jwks.json",
    "ISSUER": "https://sso.example.gov.uk",
    "AUDIENCE": "test-audience",
    "AUTH_HEADER_TYPE": "Bearer",
    "USER_ID_CLAIM": "sub",
    "MAX_ID_TOKEN_AGE_SECONDS": 12 * 60 * 60,
    "LEEWAY_SECONDS": 0,
}

_OMIT = object()


def _make_token(key=_PRIVATE_KEY, **overrides) -> str:
    """A signed token with valid claims; an override of ``_OMIT`` leaves that claim out."""
    now = datetime.now(timezone.utc)
    claims = {
        "sub": "user-123",
        "aud": "test-audience",
        "iss": "https://sso.example.gov.uk",
        "iat": now,
        "exp": now + timedelta(hours=1),
        **overrides,
    }
    claims = {name: value for name, value in claims.items() if value is not _OMIT}
    return jwt.encode(claims, key, algorithm="RS256")


def _add_session(request):
    middleware = SessionMiddleware(lambda req: None)
    middleware.process_request(request)
    request.session.save()
    return request


@override_settings(OIDC_TOKEN_AUTH=_OIDC_TOKEN_AUTH)
class InternalAccessJWTAuthenticationTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.authentication = InternalAccessJWTAuthentication()
        # The JWKS fetch is the only network call; hand back our public key so
        # signature verification runs for real against a token we signed.
        signing_key = SimpleNamespace(key=_PRIVATE_KEY.public_key())
        patcher = patch(
            "govuk.authentication._jwks_client",
            return_value=SimpleNamespace(
                get_signing_key_from_jwt=lambda token: signing_key
            ),
        )
        self.mock_jwks_client = patcher.start()
        self.addCleanup(patcher.stop)

    def _authenticate(self, token=None, **request_kwargs):
        if token is not None:
            request_kwargs["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        return self.authentication.authenticate(
            Request(self.factory.get("/", **request_kwargs))
        )

    def test_valid_bearer_token_authenticates_as_token_user(self):
        user, auth = self._authenticate(_make_token())

        self.assertIsInstance(user, TokenUser)
        self.assertEqual(user.id, "user-123")
        self.assertTrue(user.is_authenticated)
        self.assertEqual(auth["sub"], "user-123")

    def test_a_request_without_bearer_credentials_is_not_authenticated(self):
        cases = [
            ("no Authorization header", {}),
            # A "bearer" query parameter is not credentials.
            ("bearer in the query string", {"data": {"bearer": "query-token"}}),
            ("another scheme", {"HTTP_AUTHORIZATION": "Basic abc123"}),
        ]
        for name, request_kwargs in cases:
            with self.subTest(name):
                self.assertIsNone(self._authenticate(**request_kwargs))
                self.mock_jwks_client.assert_not_called()

    def test_an_invalid_token_is_rejected(self):
        now = datetime.now(timezone.utc)
        stale = now - timedelta(hours=2)
        future = now + timedelta(minutes=30)
        other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cases = [
            ("malformed header", {"HTTP_AUTHORIZATION": "Bearer one two"}),
            ("wrong audience", {"token": _make_token(aud="someone-else")}),
            ("wrong issuer", {"token": _make_token(iss="https://evil.example")}),
            ("expired", {"token": _make_token(iat=stale, exp=stale + timedelta(hours=1))}),
            ("missing iat", {"token": _make_token(iat=_OMIT)}),
            # Still unexpired, but issued longer ago than MAX_ID_TOKEN_AGE_SECONDS.
            (
                "older than the max age",
                {"token": _make_token(iat=now - timedelta(hours=13), exp=now + timedelta(hours=1))},
            ),
            (
                "issued in the future",
                {"token": _make_token(iat=future, exp=future + timedelta(hours=1))},
            ),
            ("missing id claim", {"token": _make_token(sub=None)}),
            ("signed by an unknown key", {"token": _make_token(key=other_key)}),
        ]
        for name, kwargs in cases:
            with self.subTest(name), self.assertRaises(AuthenticationFailed):
                self._authenticate(**kwargs)

    def test_a_missing_configured_audience_or_issuer_fails_closed(self):
        for setting in ("AUDIENCE", "ISSUER"):
            with (
                self.subTest(setting),
                override_settings(OIDC_TOKEN_AUTH={**_OIDC_TOKEN_AUTH, setting: None}),
                self.assertRaises(ImproperlyConfigured),
            ):
                self._authenticate(_make_token())


class InternalAccessJWTAuthenticationApiSurfaceTests(SimpleTestCase):
    def test_api_viewsets_do_not_whitelist_bearer_query_parameter(self):
        for viewset in (WagtailPages, WagtailImages, WagtailDocuments):
            self.assertNotIn("bearer", viewset.known_query_parameters)


class AuthenticatedUserRedirectMiddlewareTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.middleware = AuthenticatedUserRedirectMiddleware(
            lambda request: HttpResponse("ok")
        )
        self.site = Site.objects.get(is_default_site=True)
        self.user = get_user_model().objects.create_user(
            username="redirect-user",
            password="unused-password",
        )
        AuthenticatedRedirectRule.objects.create(
            settings=AuthenticatedRedirectSettings.for_site(self.site),
            source_path="/",
            destination_path="/dashboard",
        )
        patcher = patch("govuk.middleware.Site.find_for_request", return_value=self.site)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_authenticated_get_with_matching_rule_redirects(self):
        request = self.factory.get("/")
        request.user = self.user

        response = self.middleware(request)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/dashboard")

    def test_anonymous_users_and_posts_are_not_redirected(self):
        for name, method, user in (
            ("anonymous get", "get", AnonymousUser()),
            ("authenticated post", "post", self.user),
        ):
            with self.subTest(name):
                request = getattr(self.factory, method)("/")
                request.user = user

                response = self.middleware(request)

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.content, b"ok")


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
                            "https://sso.service.security.gov.uk/oauth2/"
                            ".well-known/openid-configuration"
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
