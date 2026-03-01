from unittest.mock import patch

from django.test import TestCase
from wagtail.models import Site

from govuk.content_discovery import (
    FeedEntry,
    parse_atom_feed,
    parse_github_org_repositories,
    sync_content_discovery_source,
)
from govuk.models import (
    ContentDiscoverySettings,
    ContentDiscoverySource,
    ExternalContentItem,
    GovukTag,
)


class ContentDiscoveryTagParsingTests(TestCase):
    def test_parse_atom_feed_extracts_category_terms(self):
        atom_feed = b"""
            <feed xmlns="http://www.w3.org/2005/Atom">
              <entry>
                <id>tag-entry</id>
                <title>Tagged entry</title>
                <link rel="alternate" href="https://example.gov.uk/tagged-entry" />
                <category scheme="https://technology.blog.gov.uk" term="Architecture" />
                <category term="Service Design" />
                <category scheme="https://example.gov.uk" />
              </entry>
            </feed>
        """

        entries = parse_atom_feed(atom_feed)

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].tags, ["Architecture", "Service Design"])

    def test_parse_github_org_repositories_extracts_topics(self):
        payload = b"""
            [
              {
                "html_url": "https://github.com/org/service",
                "name": "service",
                "description": "Service repository",
                "topics": ["architecture", "service-design"]
              }
            ]
        """

        entries = parse_github_org_repositories(payload)

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].tags, ["architecture", "service-design"])


class ContentDiscoveryConsumeTagsSyncTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.discovery_settings = ContentDiscoverySettings.for_site(self.site)

    @staticmethod
    def _entry(*, url: str, tags: list[str]) -> FeedEntry:
        return FeedEntry(
            format="atom",
            url=url,
            title="Tagged content",
            summary="",
            created_at=None,
            updated_at=None,
            entry_id=url,
            author_names=[],
            published_raw="",
            updated_raw="",
            tags=tags,
        )

    def test_sync_creates_and_applies_consumed_tags_with_default_tags(self):
        source = ContentDiscoverySource.objects.create(
            settings=self.discovery_settings,
            url="https://example.gov.uk/feed.xml",
            consume_tags=True,
        )
        default_tag = GovukTag.objects.create(slug="default-tag", name="Default Tag")
        source.default_tags = [{"type": "tag", "value": default_tag.pk}]
        source.save(update_fields=["default_tags"])

        entry_url = "https://example.gov.uk/content/one"
        entry = self._entry(
            url=entry_url,
            tags=[" Architecture ", "service design", "service design"],
        )

        with patch("govuk.content_discovery.fetch_source_content") as mock_fetch:
            mock_fetch.return_value = b"<feed/>"
            with patch("govuk.content_discovery.parse_feed", return_value=[entry]):
                sync_content_discovery_source(source)

        item = ExternalContentItem.objects.get(url=entry_url)
        self.assertEqual(
            set(item.tags.values_list("slug", flat=True)),
            {"default-tag", "architecture", "service-design"},
        )
        self.assertEqual(GovukTag.objects.get(slug="architecture").name, "Architecture")
        self.assertEqual(
            GovukTag.objects.get(slug="service-design").name,
            "Service Design",
        )

    def test_sync_does_not_apply_consumed_tags_when_flag_disabled(self):
        source = ContentDiscoverySource.objects.create(
            settings=self.discovery_settings,
            url="https://example.gov.uk/feed.xml",
            consume_tags=False,
        )
        default_tag = GovukTag.objects.create(slug="default-tag", name="Default Tag")
        source.default_tags = [{"type": "tag", "value": default_tag.pk}]
        source.save(update_fields=["default_tags"])

        entry_url = "https://example.gov.uk/content/two"
        entry = self._entry(url=entry_url, tags=["architecture"])

        with patch("govuk.content_discovery.fetch_source_content") as mock_fetch:
            mock_fetch.return_value = b"<feed/>"
            with patch("govuk.content_discovery.parse_feed", return_value=[entry]):
                sync_content_discovery_source(source)

        item = ExternalContentItem.objects.get(url=entry_url)
        self.assertEqual(set(item.tags.values_list("slug", flat=True)), {"default-tag"})
        self.assertFalse(GovukTag.objects.filter(slug="architecture").exists())

    def test_sync_continues_when_tag_creation_fails(self):
        source = ContentDiscoverySource.objects.create(
            settings=self.discovery_settings,
            url="https://example.gov.uk/feed.xml",
            consume_tags=True,
        )
        default_tag = GovukTag.objects.create(slug="default-tag", name="Default Tag")
        source.default_tags = [{"type": "tag", "value": default_tag.pk}]
        source.save(update_fields=["default_tags"])

        entry_url = "https://example.gov.uk/content/three"
        entry = self._entry(url=entry_url, tags=["broken tag", "good tag"])

        original_create = GovukTag.objects.create

        def create_side_effect(*args, **kwargs):
            if kwargs.get("slug") == "broken-tag":
                raise RuntimeError("boom")
            return original_create(*args, **kwargs)

        with patch("govuk.content_discovery.fetch_source_content") as mock_fetch:
            mock_fetch.return_value = b"<feed/>"
            with patch("govuk.content_discovery.parse_feed", return_value=[entry]):
                with patch(
                    "govuk.content_discovery.GovukTag.objects.create",
                    side_effect=create_side_effect,
                ):
                    sync_content_discovery_source(source)

        item = ExternalContentItem.objects.get(url=entry_url)
        self.assertEqual(
            set(item.tags.values_list("slug", flat=True)),
            {"default-tag", "good-tag"},
        )
        self.assertFalse(GovukTag.objects.filter(slug="broken-tag").exists())

