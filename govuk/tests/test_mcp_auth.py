"""MCP behind the site's SSO sign-in: discovery, registration, consent, code for token, and refusals."""

import base64
import hashlib
import secrets
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from govuk.mcp import MCP_PATH
from govuk.mcp.auth import CODE_LIFETIME, mcp_base_url, resource_url
from govuk.models import (
    MCPAccessToken,
    MCPAuthorisationCode,
    MCPAuthorisationRequest,
    MCPClient,
    MCPConsent,
)
from govuk.tests.helpers import mcp_token
from govuk.tests.test_mcp_server import FLAGS, LIST_TOOLS, MCP_HEADERS, service_requests

REDIRECT = "http://127.0.0.1:33418/callback"


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


@override_settings(FEATURE_FLAGS=FLAGS, MCP_RATE_LIMIT_PER_MINUTE=0)
class MCPSignInTests(TransactionTestCase):
    """A transaction test: the service reads and writes on worker threads."""

    serialized_rollback = True

    def setUp(self):
        cache.clear()
        self.reader = get_user_model().objects.create_user(username="reader", password="unused")

    # -- the steps a client takes ---------------------------------------------------------

    def _register(self, redirect_uris=(REDIRECT,)) -> dict:
        (response,) = service_requests(
            (
                "POST",
                "/mcp/oauth/register",
                {
                    "json": {
                        "redirect_uris": list(redirect_uris),
                        "client_name": "Test app",
                        "token_endpoint_auth_method": "none",
                        "grant_types": ["authorization_code"],
                        "response_types": ["code"],
                    }
                },
            )
        )
        return {"status": response.status_code, **response.json()}

    def _authorise(self, client_id: str, challenge: str, **extra):
        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": REDIRECT,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "the-state",
            "resource": resource_url(),
            **extra,
        }
        (response,) = service_requests(("GET", "/mcp/oauth/authorize", {"params": params}))
        return response

    def _consent_page(self, client_id: str, challenge: str) -> str:
        response = self._authorise(client_id, challenge)
        self.assertEqual(response.status_code, 302)
        return urlsplit(response.headers["location"]).path

    def _token(self, client_id: str, code: str, verifier: str):
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT,
            "client_id": client_id,
            "code_verifier": verifier,
        }
        (response,) = service_requests(("POST", "/mcp/oauth/token", {"data": data}))
        return response

    def _code_from(self, response) -> str:
        self.assertEqual(response.status_code, 302)
        location = response["Location"]
        self.assertTrue(location.startswith(REDIRECT))
        query = parse_qs(urlsplit(location).query)
        self.assertEqual(query["state"], ["the-state"])
        return query["code"][0]

    def _call(self, token: str):
        headers = {**MCP_HEADERS, "Authorization": f"Bearer {token}"}
        (response,) = service_requests(("POST", MCP_PATH, {"json": LIST_TOOLS, "headers": headers}))
        return response

    def _signed_in_flow(self) -> tuple[str, str, str]:
        """A registered client's id, its code for a signed-in reader who agrees, and the verifier."""
        client_id = self._register()["client_id"]
        verifier, challenge = pkce()
        page = self._consent_page(client_id, challenge)
        self.client.force_login(self.reader)
        code = self._code_from(self.client.post(page, {"decision": "allow"}))
        return client_id, code, verifier

    # -- discovery ------------------------------------------------------------------------

    def test_without_a_token_the_client_is_told_where_to_sign_in(self):
        (response,) = service_requests(("POST", MCP_PATH, {"json": LIST_TOOLS, "headers": MCP_HEADERS}))
        self.assertEqual(response.status_code, 401)
        self.assertIn(
            f'resource_metadata="{mcp_base_url()}/.well-known/oauth-protected-resource/mcp"',
            response.headers["www-authenticate"],
        )

    def test_in_development_clients_are_sent_to_the_service_not_the_site(self):
        self.assertEqual(mcp_base_url(), "http://localhost:8001")
        server = self.client.get("/.well-known/oauth-authorization-server/mcp/oauth").json()
        self.assertEqual(server["registration_endpoint"], "http://localhost:8001/mcp/oauth/register")
        # The sign-in page itself is the site's.
        client_id = self._register()["client_id"]
        _, challenge = pkce()
        location = self._authorise(client_id, challenge).headers["location"]
        self.assertTrue(location.startswith("http://localhost:8000/accounts/mcp/authorise/"))

    def test_the_discovery_documents(self):
        base = mcp_base_url()
        resource = self.client.get("/.well-known/oauth-protected-resource/mcp").json()
        self.assertEqual(resource["resource"], f"{base}/mcp")
        self.assertEqual(resource["authorization_servers"], [f"{base}/mcp/oauth"])
        server = self.client.get("/.well-known/oauth-authorization-server/mcp/oauth").json()
        self.assertEqual(server["issuer"], f"{base}/mcp/oauth")
        self.assertEqual(server["authorization_endpoint"], f"{base}/mcp/oauth/authorize")
        self.assertEqual(server["token_endpoint"], f"{base}/mcp/oauth/token")
        self.assertEqual(server["registration_endpoint"], f"{base}/mcp/oauth/register")
        self.assertEqual(server["code_challenge_methods_supported"], ["S256"])
        self.assertEqual(server["scopes_supported"], ["mcp"])
        self.assertEqual(server["grant_types_supported"], ["authorization_code"])
        # The same document from the service, for a client that reaches it directly.
        (direct,) = service_requests(("GET", "/.well-known/oauth-authorization-server/mcp/oauth", {}))
        self.assertEqual(direct.json(), server)

    # -- the flow -------------------------------------------------------------------------

    def test_sign_in_agree_and_use_the_token_for_an_hour(self):
        client_id = self._register()["client_id"]
        verifier, challenge = pkce()
        page = self._consent_page(client_id, challenge)

        # Signed out, the page sends the reader to the site's SSO sign-in first.
        signed_out = self.client.get(page)
        self.assertEqual(signed_out.status_code, 302)
        self.assertTrue(signed_out["Location"].startswith("/login/?next="))

        self.client.force_login(self.reader)
        consent = self.client.get(page)
        self.assertContains(consent, "Allow Test app to search this site as you?")
        self.assertContains(consent, "<strong>127.0.0.1:33418</strong>", html=True)
        self.assertIn("form-action 'self' http://127.0.0.1:33418", consent["Content-Security-Policy"])

        code = self._code_from(self.client.post(page, {"decision": "allow"}))
        response = self._token(client_id, code, verifier)
        self.assertEqual(response.status_code, 200)
        token = response.json()
        self.assertEqual((token["token_type"].lower(), token["expires_in"], token["scope"]), ("bearer", 3600, "mcp"))
        self.assertNotIn("refresh_token", token)
        self.assertEqual(self._call(token["access_token"]).status_code, 200)

        stored = MCPAccessToken.objects.get()
        self.assertEqual(stored.user, self.reader)
        self.assertNotEqual(stored.token_hash, token["access_token"])  # only its hash is kept

    def test_an_app_already_allowed_is_sent_straight_back(self):
        client_id, code, verifier = self._signed_in_flow()
        self.assertEqual(self._token(client_id, code, verifier).status_code, 200)
        _, challenge = pkce()
        page = self._consent_page(client_id, challenge)
        self._code_from(self.client.get(page))  # no page this time

    def test_not_allowing_sends_the_app_an_error(self):
        client_id = self._register()["client_id"]
        _, challenge = pkce()
        page = self._consent_page(client_id, challenge)
        self.client.force_login(self.reader)
        response = self.client.post(page, {"decision": "deny"})
        query = parse_qs(urlsplit(response["Location"]).query)
        self.assertEqual((query["error"], query["state"]), (["access_denied"], ["the-state"]))
        self.assertFalse(MCPConsent.objects.exists())

    # -- refusals -------------------------------------------------------------------------

    def test_a_code_needs_its_own_verifier_and_works_once(self):
        client_id, code, verifier = self._signed_in_flow()
        wrong, _ = pkce()
        self.assertEqual(self._token(client_id, code, wrong).status_code, 400)
        self.assertEqual(self._token(client_id, code, verifier).status_code, 200)
        reused = self._token(client_id, code, verifier)
        self.assertEqual((reused.status_code, reused.json()["error"]), (400, "invalid_grant"))

    def test_an_expired_code_is_refused(self):
        client_id, code, verifier = self._signed_in_flow()
        MCPAuthorisationCode.objects.update(expires_at=timezone.now() - CODE_LIFETIME)
        self.assertEqual(self._token(client_id, code, verifier).status_code, 400)

    def test_a_token_stops_working_when_expired_revoked_or_the_account_is_closed(self):
        expired = mcp_token(self.reader, lifetime=timedelta(seconds=-1))
        self.assertEqual(self._call(expired).status_code, 401)

        closed_user = get_user_model().objects.create_user(username="left", password="unused")
        closed = mcp_token(closed_user)
        closed_user.is_active = False
        closed_user.save()
        self.assertEqual(self._call(closed).status_code, 401)

        client_id, code, verifier = self._signed_in_flow()
        token = self._token(client_id, code, verifier).json()["access_token"]
        self.assertEqual(self._call(token).status_code, 200)
        apps = self.client.get("/accounts/mcp/apps/")
        self.assertContains(apps, "Test app")
        consent = MCPConsent.objects.get(user=self.reader)
        self.client.post("/accounts/mcp/apps/", {"client": consent.client.pk})
        self.assertEqual(self._call(token).status_code, 401)
        self.assertFalse(MCPConsent.objects.filter(user=self.reader).exists())

    def test_a_token_for_another_resource_is_refused(self):
        token = mcp_token(self.reader)
        MCPAccessToken.objects.update(resource="https://elsewhere.example/mcp")
        self.assertEqual(self._call(token).status_code, 401)

    def test_an_app_cannot_ask_for_a_token_meant_for_somewhere_else(self):
        client_id = self._register()["client_id"]
        _, challenge = pkce()
        response = self._authorise(client_id, challenge, resource="https://elsewhere.example/mcp")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(parse_qs(urlsplit(response.headers["location"]).query)["error"], ["invalid_target"])
        self.assertFalse(MCPAuthorisationRequest.objects.exists())

    def test_only_https_or_this_machine_can_be_sent_back_to(self):
        for uri, allowed in (
            ("https://claude.ai/api/mcp/auth_callback", True),
            ("http://localhost:6274/oauth/callback", True),
            ("http://[::1]:5000/cb", True),
            ("http://evil.example/cb", False),
            ("javascript:alert(1)", False),
        ):
            with self.subTest(uri=uri):
                registered = self._register([uri])
                self.assertEqual(registered["status"], 201 if allowed else 400)
                if not allowed:
                    self.assertEqual(registered["error"], "invalid_redirect_uri")

    def test_an_old_sign_in_link_has_expired(self):
        client_id = self._register()["client_id"]
        _, challenge = pkce()
        page = self._consent_page(client_id, challenge)
        MCPAuthorisationRequest.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.client.force_login(self.reader)
        self.assertContains(self.client.get(page), "This link has expired", status_code=404)

    @override_settings(FEATURE_FLAGS={**FLAGS, "AI": False})
    def test_nothing_here_without_the_ai_features(self):
        self.assertEqual(self.client.get("/.well-known/oauth-protected-resource/mcp").status_code, 404)
        self.client.force_login(self.reader)
        self.assertEqual(self.client.get("/accounts/mcp/apps/").status_code, 404)


@override_settings(FEATURE_FLAGS=FLAGS)
class ProfileTests(TestCase):
    """The profile page is where a reader finds their connected apps."""

    def setUp(self):
        self.reader = get_user_model().objects.create_user(username="reader", password="unused")
        self.client.force_login(self.reader)

    def _app(self, name: str) -> MCPClient:
        return MCPClient.objects.create(client_id=name, name=name, information={"client_id": name, "redirect_uris": []})

    def test_it_counts_the_readers_apps_and_links_to_them(self):
        for name in ("One", "Two"):
            MCPConsent.objects.create(user=self.reader, client=self._app(name))
        other = get_user_model().objects.create_user(username="other", password="unused")
        MCPConsent.objects.create(user=other, client=self._app("Theirs"))
        response = self.client.get("/accounts/profile/")
        self.assertContains(response, "<h2 class=\"govuk-heading-m\">Connected apps</h2>", html=True)
        self.assertContains(response, "2 apps can search and read this site as you")
        self.assertContains(response, 'href="/accounts/mcp/apps/"')

    def test_with_none_it_says_so_without_a_link(self):
        response = self.client.get("/accounts/profile/")
        self.assertContains(response, "No apps are connected to this site's MCP server.")
        self.assertNotContains(response, 'href="/accounts/mcp/apps/"')

    @override_settings(FEATURE_FLAGS={**FLAGS, "AI": False})
    def test_not_there_without_the_ai_features(self):
        self.assertNotContains(self.client.get("/accounts/profile/"), "Connected apps")
