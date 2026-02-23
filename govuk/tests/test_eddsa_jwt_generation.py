from datetime import timedelta

import jwt
from cryptography.hazmat.primitives import serialization
from django.test import TestCase, override_settings
from wagtail.models import Site

from govuk.jwt_tokens import generate_site_jwt
from govuk.models import EdDSAKeyPair, EdDSAKeySettings, JWTGenerationError


class EdDSAJWTGenerationTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.key_settings = EdDSAKeySettings.for_site(self.site)
        self.key_pair = EdDSAKeyPair.generate_for_settings(settings_obj=self.key_settings)

    @override_settings(WAGTAILADMIN_BASE_URL="https://admin.example.gov.uk")
    def test_generate_jwt_uses_primary_key_and_expected_claims(self):
        token = self.key_settings.generate_jwt(
            htu="https://api.example.gov.uk/search",
            htm="post",
            add_jti=True,
        )
        public_key = serialization.load_pem_public_key(
            self.key_pair.public_key.encode("utf-8")
        )
        payload = jwt.decode(
            token,
            key=public_key,
            algorithms=[self.key_pair.algorithm],
            audience="https://api.example.gov.uk/search",
            issuer="https://admin.example.gov.uk",
            options={"require": ["iss", "iat", "nbf", "exp", "jti"]},
        )
        header = jwt.get_unverified_header(token)

        self.assertEqual(payload["iss"], "https://admin.example.gov.uk")
        self.assertEqual(payload["htu"], "https://api.example.gov.uk/search")
        self.assertEqual(payload["htm"], "POST")
        self.assertLessEqual(payload["exp"] - payload["iat"], 301)
        self.assertGreaterEqual(payload["exp"] - payload["iat"], 299)
        self.assertTrue(payload["jti"])
        self.assertEqual(header["alg"], self.key_pair.algorithm)
        self.assertEqual(header["kid"], self.key_pair.key_id)
        self.assertEqual(header["typ"], "JWT")

    @override_settings(WAGTAILADMIN_BASE_URL="https://admin.example.gov.uk")
    def test_generate_jwt_uses_primary_key_algorithm_in_header_and_signature(self):
        es256_key_pair = EdDSAKeyPair.generate_for_settings(
            settings_obj=self.key_settings,
            algorithm=EdDSAKeyPair.Algorithm.ES256,
        )
        es256_key_pair.mark_as_primary()

        token = self.key_settings.generate_jwt(
            htu="https://api.example.gov.uk/search",
            htm="get",
            add_jti=True,
        )
        header = jwt.get_unverified_header(token)
        self.assertEqual(header["alg"], EdDSAKeyPair.Algorithm.ES256)
        self.assertEqual(header["kid"], es256_key_pair.key_id)

        public_key = serialization.load_pem_public_key(
            es256_key_pair.public_key.encode("utf-8")
        )
        payload = jwt.decode(
            token,
            key=public_key,
            algorithms=[EdDSAKeyPair.Algorithm.ES256],
            audience="https://api.example.gov.uk/search",
            issuer="https://admin.example.gov.uk",
            options={"require": ["iss", "iat", "nbf", "exp", "jti"]},
        )
        self.assertEqual(payload["htm"], "GET")
        self.assertEqual(payload["htu"], "https://api.example.gov.uk/search")

    @override_settings(WAGTAILADMIN_BASE_URL="https://admin.example.gov.uk")
    def test_generate_jwt_rejects_non_positive_lifetime(self):
        with self.assertRaises(JWTGenerationError):
            self.key_settings.generate_jwt(lifetime=timedelta(seconds=0))

    @override_settings(WAGTAILADMIN_BASE_URL="https://admin.example.gov.uk")
    def test_generate_jwt_requires_htu_and_htm_together(self):
        with self.assertRaises(JWTGenerationError):
            self.key_settings.generate_jwt(htu="https://api.example.gov.uk/search")

        with self.assertRaises(JWTGenerationError):
            self.key_settings.generate_jwt(htm="GET")

    @override_settings(WAGTAILADMIN_BASE_URL="https://admin.example.gov.uk")
    def test_generate_site_jwt_wrapper_works_for_other_modules(self):
        token = generate_site_jwt(
            site=self.site,
            htu="https://api.example.gov.uk/x",
            htm="GET",
            add_jti=True,
        )
        public_key = serialization.load_pem_public_key(
            self.key_pair.public_key.encode("utf-8")
        )
        payload = jwt.decode(
            token,
            key=public_key,
            algorithms=[self.key_pair.algorithm],
            audience="https://api.example.gov.uk/x",
            issuer="https://admin.example.gov.uk",
            options={"require": ["iss", "iat", "nbf", "exp", "jti"]},
        )
        self.assertEqual(payload["htm"], "GET")
        self.assertEqual(payload["htu"], "https://api.example.gov.uk/x")
