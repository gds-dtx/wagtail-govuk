from unittest.mock import patch

from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory, TestCase
from django.urls import reverse

from govuk.adapters import AccountAdapter
from govuk.oidc import ADMIN_OIDC_NEXT_URL_KEY, build_oidc_login_url


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
