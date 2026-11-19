from datetime import timedelta
from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives import serialization
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase, override_settings
from django.urls import reverse
from wagtail.models import Site

from govuk.jwt_tokens import generate_site_jwt
from govuk.models import EdDSAKeyPair, EdDSAKeySettings, JWTGenerationError

ISSUER = "https://admin.example.gov.uk"
SEARCH_URL = "https://api.example.gov.uk/search"


def _decode(token, key_pair, audience, require=("iss", "iat", "nbf", "exp", "jti")):
    """Verify a token against the key pair's public key, as a recipient would."""
    public_key = serialization.load_pem_public_key(key_pair.public_key.encode("utf-8"))
    return jwt.decode(
        token,
        key=public_key,
        algorithms=[key_pair.algorithm],
        audience=audience,
        issuer=ISSUER,
        options={"require": list(require)},
    )


class KeySettingsTestCase(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.key_settings = EdDSAKeySettings.for_site(self.site)

    def generate_key_pair(self, **kwargs):
        return EdDSAKeyPair.generate_for_settings(settings_obj=self.key_settings, **kwargs)


class EdDSAKeyPairModelTests(KeySettingsTestCase):
    def test_first_key_pair_becomes_primary_automatically(self):
        first_key_pair = self.generate_key_pair()
        second_key_pair = self.generate_key_pair()

        first_key_pair.refresh_from_db()
        second_key_pair.refresh_from_db()

        self.assertTrue(first_key_pair.is_primary)
        self.assertFalse(second_key_pair.is_primary)

    def test_deleting_primary_key_promotes_another_key(self):
        first_key_pair = self.generate_key_pair()
        second_key_pair = self.generate_key_pair()

        first_key_pair.delete()
        second_key_pair.refresh_from_db()

        self.assertTrue(second_key_pair.is_primary)

    def test_generate_for_settings_supports_es256(self):
        key_pair = self.generate_key_pair(algorithm=EdDSAKeyPair.Algorithm.ES256)
        self.assertEqual(key_pair.algorithm, EdDSAKeyPair.Algorithm.ES256)


@override_settings(WAGTAILADMIN_BASE_URL=ISSUER)
class EdDSAJWTGenerationTests(KeySettingsTestCase):
    def setUp(self):
        super().setUp()
        self.key_pair = self.generate_key_pair()

    def test_generate_jwt_uses_primary_key_and_expected_claims(self):
        token = self.key_settings.generate_jwt(htu=SEARCH_URL, htm="post", add_jti=True)
        payload = _decode(token, self.key_pair, SEARCH_URL)
        header = jwt.get_unverified_header(token)

        self.assertEqual(payload["iss"], ISSUER)
        self.assertEqual(payload["htu"], SEARCH_URL)
        self.assertEqual(payload["htm"], "POST")
        self.assertLessEqual(payload["exp"] - payload["iat"], 301)
        self.assertGreaterEqual(payload["exp"] - payload["iat"], 299)
        self.assertTrue(payload["jti"])
        self.assertEqual(header["alg"], self.key_pair.algorithm)
        self.assertEqual(header["kid"], self.key_pair.key_id)
        self.assertEqual(header["typ"], "JWT")

    def test_generate_jwt_uses_primary_key_algorithm_in_header_and_signature(self):
        es256_key_pair = self.generate_key_pair(algorithm=EdDSAKeyPair.Algorithm.ES256)
        es256_key_pair.mark_as_primary()

        token = self.key_settings.generate_jwt(htu=SEARCH_URL, htm="get", add_jti=True)
        header = jwt.get_unverified_header(token)
        self.assertEqual(header["alg"], EdDSAKeyPair.Algorithm.ES256)
        self.assertEqual(header["kid"], es256_key_pair.key_id)

        payload = _decode(token, es256_key_pair, SEARCH_URL)
        self.assertEqual(payload["htm"], "GET")
        self.assertEqual(payload["htu"], SEARCH_URL)

    def test_generate_jwt_rejects_invalid_arguments(self):
        # htu and htm are only accepted together.
        for kwargs in (
            {"lifetime": timedelta(seconds=0)},
            {"htu": SEARCH_URL},
            {"htm": "GET"},
        ):
            with self.subTest(**kwargs), self.assertRaises(JWTGenerationError):
                self.key_settings.generate_jwt(**kwargs)

    def test_generate_site_jwt_wrapper_works_for_other_modules(self):
        token = generate_site_jwt(
            site=self.site,
            htu="https://api.example.gov.uk/x",
            htm="GET",
            add_jti=True,
        )
        payload = _decode(token, self.key_pair, "https://api.example.gov.uk/x")
        self.assertEqual(payload["htm"], "GET")
        self.assertEqual(payload["htu"], "https://api.example.gov.uk/x")


class EdDSAKeyAdminViewTests(KeySettingsTestCase):
    def setUp(self):
        super().setUp()
        self.admin_user = get_user_model().objects.create_superuser(
            username="admin-user",
            email="admin@example.gov.uk",
            password="unused-password",
        )
        self.client.force_login(self.admin_user)

    def test_generate_view_creates_key_pair(self):
        response = self.client.post(
            reverse("govuk_eddsa_generate_site_key", args=[self.site.pk]),
            data={"next": "/admin/"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/admin/")
        self.assertEqual(
            EdDSAKeyPair.objects.filter(settings=self.key_settings).count(),
            1,
        )
        generated = EdDSAKeyPair.objects.get(settings=self.key_settings)
        self.assertEqual(generated.algorithm, EdDSAKeyPair.Algorithm.EDDSA)

    def test_generate_view_creates_es256_key_pair_when_requested(self):
        response = self.client.post(
            reverse("govuk_eddsa_generate_site_key", args=[self.site.pk]),
            data={"next": "/admin/", "algorithm": EdDSAKeyPair.Algorithm.ES256},
        )

        self.assertEqual(response.status_code, 302)
        generated = EdDSAKeyPair.objects.get(settings=self.key_settings)
        self.assertEqual(generated.algorithm, EdDSAKeyPair.Algorithm.ES256)

    def test_set_primary_view_updates_primary_key_pair(self):
        first_key_pair = self.generate_key_pair()
        second_key_pair = self.generate_key_pair()

        response = self.client.post(
            reverse("govuk_eddsa_set_primary_key", args=[second_key_pair.pk]),
            data={"next": "/admin/"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/admin/")

        first_key_pair.refresh_from_db()
        second_key_pair.refresh_from_db()
        self.assertFalse(first_key_pair.is_primary)
        self.assertTrue(second_key_pair.is_primary)

    def test_delete_view_removes_key_pair(self):
        key_pair = self.generate_key_pair()

        response = self.client.post(
            reverse("govuk_eddsa_delete_key", args=[key_pair.pk]),
            data={"next": "/admin/"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/admin/")
        self.assertFalse(EdDSAKeyPair.objects.filter(pk=key_pair.pk).exists())

    def test_private_key_value_is_not_shown_on_settings_page(self):
        key_pair = self.generate_key_pair()
        response = self.client.get(
            reverse(
                "wagtailsettings:edit",
                args=(
                    EdDSAKeySettings._meta.app_label,
                    "eddsakeysettings",
                    self.site.pk,
                ),
            )
        )

        self.assertEqual(response.status_code, 200)
        sensitive_private_key_line = key_pair.private_key.splitlines()[1]
        self.assertNotContains(response, sensitive_private_key_line)

    @override_settings(WAGTAILADMIN_BASE_URL=ISSUER)
    def test_generate_jwt_view_returns_bearer_token_json(self):
        key_pair = self.generate_key_pair()

        response = self.client.post(
            reverse("govuk_eddsa_generate_site_jwt", args=[self.site.pk]),
            data={"htu": SEARCH_URL, "htm": "get", "lifetime_seconds": "120"},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["token_type"], "Bearer")
        self.assertEqual(body["expires_in"], 120)
        self.assertEqual(body["issuer"], ISSUER)
        self.assertEqual(body["kid"], key_pair.key_id)
        self.assertEqual(body["alg"], key_pair.algorithm)
        self.assertEqual(body["htm"], "GET")
        self.assertEqual(body["htu"], SEARCH_URL)
        self.assertTrue(body["access_token"])

        payload = _decode(
            body["access_token"], key_pair, SEARCH_URL, require=("iss", "iat", "nbf", "exp")
        )
        self.assertEqual(payload["htu"], SEARCH_URL)
        self.assertEqual(payload["htm"], "GET")

    @override_settings(WAGTAILADMIN_BASE_URL=ISSUER)
    def test_generate_jwt_view_rejects_invalid_lifetime(self):
        self.generate_key_pair()

        response = self.client.post(
            reverse("govuk_eddsa_generate_site_jwt", args=[self.site.pk]),
            data={"lifetime_seconds": "0"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn(
            "lifetime_seconds must be between 1 and 86400.",
            response.content.decode("utf-8"),
        )

    def test_generate_jwt_view_shows_the_generation_error_only_with_debug_on(self):
        cases = [
            (False, JWTGenerationError, "Unable to generate JWT."),
            (True, ImproperlyConfigured, "detailed generation failure"),
        ]
        for debug, error, expected in cases:
            with (
                self.subTest(debug=debug),
                override_settings(DEBUG=debug),
                patch.object(
                    EdDSAKeySettings,
                    "generate_jwt",
                    side_effect=error("detailed generation failure"),
                ),
            ):
                response = self.client.post(
                    reverse("govuk_eddsa_generate_site_jwt", args=[self.site.pk]),
                    data={"lifetime_seconds": "120"},
                )

                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.content.decode("utf-8"), expected)


class JwksViewTests(KeySettingsTestCase):
    def setUp(self):
        super().setUp()
        self.site.hostname = "testserver"
        self.site.port = 80
        self.site.save(update_fields=["hostname", "port"])

    def test_returns_404_when_no_key_pairs_are_configured(self):
        response = self.client.get(reverse("govuk_jwks"))

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response["Access-Control-Allow-Origin"], "*")

    def test_returns_all_public_keys_with_primary_key_first(self):
        first_key_pair = self.generate_key_pair()
        second_key_pair = self.generate_key_pair()
        second_key_pair.mark_as_primary()

        first_key_pair.refresh_from_db()
        second_key_pair.refresh_from_db()
        self.assertFalse(first_key_pair.is_primary)
        self.assertTrue(second_key_pair.is_primary)

        response = self.client.get(reverse("govuk_jwks"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Access-Control-Allow-Origin"], "*")
        data = response.json()
        self.assertIn("keys", data)
        self.assertEqual(len(data["keys"]), 2)
        self.assertEqual(data["keys"][0]["kid"], second_key_pair.key_id)
        self.assertEqual(data["keys"][1]["kid"], first_key_pair.key_id)

        first_jwk = data["keys"][0]
        self.assertEqual(first_jwk["kty"], "OKP")
        self.assertEqual(first_jwk["alg"], "EdDSA")
        self.assertEqual(first_jwk["crv"], "Ed25519")
        self.assertEqual(first_jwk["use"], "sig")
        self.assertNotIn("private_key", first_jwk)

    def test_returns_es256_jwk_for_es256_primary_key(self):
        key_pair = self.generate_key_pair(algorithm=EdDSAKeyPair.Algorithm.ES256)

        response = self.client.get(reverse("govuk_jwks"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Access-Control-Allow-Origin"], "*")
        data = response.json()
        self.assertEqual(len(data["keys"]), 1)
        first_jwk = data["keys"][0]
        self.assertEqual(first_jwk["kid"], key_pair.key_id)
        self.assertEqual(first_jwk["kty"], "EC")
        self.assertEqual(first_jwk["alg"], "ES256")
        self.assertEqual(first_jwk["crv"], "P-256")
        self.assertEqual(first_jwk["use"], "sig")
        self.assertIn("x", first_jwk)
        self.assertIn("y", first_jwk)
        self.assertNotIn("private_key", first_jwk)
