from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from django.test import RequestFactory, SimpleTestCase, override_settings
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.request import Request

from govuk.api import WagtailDocuments, WagtailImages, WagtailPages
from govuk.authentication import InternalAccessJWTAuthentication, TokenUser

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


def _make_token(**overrides) -> str:
    now = datetime.now(timezone.utc)
    claims = {
        "sub": "user-123",
        "aud": "test-audience",
        "iss": "https://sso.example.gov.uk",
        "iat": now,
        "exp": now + timedelta(hours=1),
    }
    claims.update(overrides)
    return jwt.encode(claims, _PRIVATE_KEY, algorithm="RS256")


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

    def test_missing_authorization_header_returns_none(self):
        self.assertIsNone(self._authenticate())
        self.mock_jwks_client.assert_not_called()

    def test_does_not_fall_back_to_query_string(self):
        # A "bearer" query parameter, no Authorization header: not credentials.
        self.assertIsNone(self._authenticate(data={"bearer": "query-token"}))
        self.mock_jwks_client.assert_not_called()

    def test_non_bearer_scheme_is_ignored(self):
        self.assertIsNone(self._authenticate(HTTP_AUTHORIZATION="Basic abc123"))
        self.mock_jwks_client.assert_not_called()

    def test_malformed_header_is_rejected(self):
        with self.assertRaises(AuthenticationFailed):
            self._authenticate(HTTP_AUTHORIZATION="Bearer one two")

    def test_wrong_audience_is_rejected(self):
        with self.assertRaises(AuthenticationFailed):
            self._authenticate(_make_token(aud="someone-else"))

    def test_wrong_issuer_is_rejected(self):
        with self.assertRaises(AuthenticationFailed):
            self._authenticate(_make_token(iss="https://evil.example"))

    def test_expired_token_is_rejected(self):
        stale = datetime.now(timezone.utc) - timedelta(hours=2)
        with self.assertRaises(AuthenticationFailed):
            self._authenticate(_make_token(iat=stale, exp=stale + timedelta(hours=1)))

    def test_token_missing_iat_is_rejected(self):
        token = _make_token()
        # Re-sign without an iat claim.
        token = jwt.encode(
            {
                "sub": "user-123",
                "aud": "test-audience",
                "iss": "https://sso.example.gov.uk",
                "exp": datetime.now(timezone.utc) + timedelta(hours=1),
            },
            _PRIVATE_KEY,
            algorithm="RS256",
        )
        with self.assertRaises(AuthenticationFailed):
            self._authenticate(token)

    def test_token_older_than_max_age_is_rejected(self):
        # Still unexpired, but issued longer ago than MAX_ID_TOKEN_AGE_SECONDS.
        old_iat = datetime.now(timezone.utc) - timedelta(hours=13)
        with self.assertRaises(AuthenticationFailed):
            self._authenticate(
                _make_token(iat=old_iat, exp=datetime.now(timezone.utc) + timedelta(hours=1))
            )

    def test_token_issued_in_the_future_is_rejected(self):
        future = datetime.now(timezone.utc) + timedelta(minutes=30)
        with self.assertRaises(AuthenticationFailed):
            self._authenticate(_make_token(iat=future, exp=future + timedelta(hours=1)))

    def test_signature_from_unknown_key_is_rejected(self):
        other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        token = jwt.encode(
            {
                "sub": "user-123",
                "aud": "test-audience",
                "iss": "https://sso.example.gov.uk",
                "iat": datetime.now(timezone.utc),
                "exp": datetime.now(timezone.utc) + timedelta(hours=1),
            },
            other_key,
            algorithm="RS256",
        )
        with self.assertRaises(AuthenticationFailed):
            self._authenticate(token)


class InternalAccessJWTAuthenticationApiSurfaceTests(SimpleTestCase):
    def test_api_viewsets_do_not_whitelist_bearer_query_parameter(self):
        for viewset in (WagtailPages, WagtailImages, WagtailDocuments):
            self.assertNotIn("bearer", viewset.known_query_parameters)
