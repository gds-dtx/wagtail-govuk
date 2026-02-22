from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives import serialization
from django.test import TestCase, override_settings
from wagtail.models import Site

from govuk.content_discovery import sync_content_discovery_source
from govuk.models import (
    ContentDiscoverySettings,
    ContentDiscoverySource,
    EdDSAKeyPair,
    EdDSAKeySettings,
)


class ContentDiscoveryJWTHeaderTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.discovery_settings = ContentDiscoverySettings.for_site(self.site)

    @override_settings(WAGTAILADMIN_BASE_URL="https://admin.example.gov.uk")
    def test_sync_adds_signed_bearer_header_when_enabled_and_keys_exist(self):
        source = ContentDiscoverySource.objects.create(
            settings=self.discovery_settings,
            url="https://example.gov.uk/feed.xml",
            send_signed_bearer_jwt=True,
        )
        key_settings = EdDSAKeySettings.for_site(self.site)
        key_pair = EdDSAKeyPair.generate_for_settings(settings_obj=key_settings)

        with patch("govuk.content_discovery.fetch_source_content") as mock_fetch:
            mock_fetch.return_value = b"<feed/>"
            with patch("govuk.content_discovery.parse_feed", return_value=[]):
                sync_content_discovery_source(source)

        self.assertTrue(mock_fetch.called)
        auth_header = mock_fetch.call_args.kwargs["authorization_header"]
        self.assertTrue(auth_header.startswith("Bearer "))

        token = auth_header.removeprefix("Bearer ").strip()
        public_key = serialization.load_pem_public_key(key_pair.public_key.encode("utf-8"))
        payload = jwt.decode(
            token,
            key=public_key,
            algorithms=["EdDSA"],
            issuer="https://admin.example.gov.uk",
            options={"require": ["iss", "iat", "nbf", "exp", "jti"]},
        )
        self.assertEqual(payload["htu"], source.url)
        self.assertEqual(payload["htm"], "GET")
        self.assertLessEqual(payload["exp"] - payload["iat"], 91)
        self.assertGreaterEqual(payload["exp"] - payload["iat"], 89)

    @override_settings(WAGTAILADMIN_BASE_URL="https://admin.example.gov.uk")
    def test_sync_continues_without_header_when_enabled_but_no_keys_exist(self):
        source = ContentDiscoverySource.objects.create(
            settings=self.discovery_settings,
            url="https://example.gov.uk/feed.xml",
            send_signed_bearer_jwt=True,
        )

        with patch("govuk.content_discovery.fetch_source_content") as mock_fetch:
            mock_fetch.return_value = b"<feed/>"
            with patch("govuk.content_discovery.parse_feed", return_value=[]):
                sync_content_discovery_source(source)

        self.assertTrue(mock_fetch.called)
        self.assertIsNone(mock_fetch.call_args.kwargs["authorization_header"])

    @override_settings(WAGTAILADMIN_BASE_URL="https://admin.example.gov.uk")
    def test_sync_does_not_add_header_when_flag_disabled(self):
        source = ContentDiscoverySource.objects.create(
            settings=self.discovery_settings,
            url="https://example.gov.uk/feed.xml",
            send_signed_bearer_jwt=False,
        )
        key_settings = EdDSAKeySettings.for_site(self.site)
        EdDSAKeyPair.generate_for_settings(settings_obj=key_settings)

        with patch("govuk.content_discovery.fetch_source_content") as mock_fetch:
            mock_fetch.return_value = b"<feed/>"
            with patch("govuk.content_discovery.parse_feed", return_value=[]):
                sync_content_discovery_source(source)

        self.assertTrue(mock_fetch.called)
        self.assertIsNone(mock_fetch.call_args.kwargs["authorization_header"])
