from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives import serialization
from django.test import TestCase, override_settings
from wagtail.models import Site

from govuk.content_discovery import FeedEntry, sync_content_discovery_source
from govuk.models import (
    ContentDiscoverySettings,
    ContentDiscoverySource,
    EdDSAKeyPair,
    EdDSAKeySettings,
    ExternalContentItem,
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
            algorithms=[key_pair.algorithm],
            audience=source.url,
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


class ContentDiscoverySyncSourceTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.discovery_settings = ContentDiscoverySettings.for_site(self.site)

    @staticmethod
    def _entry(url: str) -> FeedEntry:
        return FeedEntry(
            format="atom",
            url=url,
            title="Synced item",
            summary="",
            created_at=None,
            updated_at=None,
            entry_id=url,
            author_names=[],
            published_raw="",
            updated_raw="",
        )

    def test_sync_source_hides_items_missing_from_feed_when_enabled(self):
        source = ContentDiscoverySource.objects.create(
            settings=self.discovery_settings,
            url="https://example.gov.uk/feed.xml",
            sync_source=True,
        )
        kept_item = ExternalContentItem.objects.create(
            source=source,
            url="https://example.gov.uk/articles/kept",
            title="Kept item",
            hidden=False,
        )
        missing_item = ExternalContentItem.objects.create(
            source=source,
            url="https://example.gov.uk/articles/missing",
            title="Missing item",
            hidden=False,
        )
        other_source = ContentDiscoverySource.objects.create(
            settings=self.discovery_settings,
            url="https://example.gov.uk/other-feed.xml",
            sync_source=True,
        )
        other_item = ExternalContentItem.objects.create(
            source=other_source,
            url="https://example.gov.uk/articles/other",
            title="Other source item",
            hidden=False,
        )

        with patch("govuk.content_discovery.fetch_source_content") as mock_fetch:
            mock_fetch.return_value = b"<feed/>"
            with patch(
                "govuk.content_discovery.parse_feed",
                return_value=[self._entry(kept_item.url)],
            ):
                sync_content_discovery_source(source)

        kept_item.refresh_from_db()
        missing_item.refresh_from_db()
        other_item.refresh_from_db()

        self.assertFalse(kept_item.hidden)
        self.assertTrue(missing_item.hidden)
        self.assertFalse(other_item.hidden)

    def test_sync_source_does_not_hide_missing_items_when_disabled(self):
        source = ContentDiscoverySource.objects.create(
            settings=self.discovery_settings,
            url="https://example.gov.uk/feed.xml",
            sync_source=False,
        )
        missing_item = ExternalContentItem.objects.create(
            source=source,
            url="https://example.gov.uk/articles/missing",
            title="Missing item",
            hidden=False,
        )

        with patch("govuk.content_discovery.fetch_source_content") as mock_fetch:
            mock_fetch.return_value = b"<feed/>"
            with patch("govuk.content_discovery.parse_feed", return_value=[]):
                sync_content_discovery_source(source)

        missing_item.refresh_from_db()
        self.assertFalse(missing_item.hidden)
