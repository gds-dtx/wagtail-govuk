import shutil
import tempfile
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from wagtail.models import Site

from govuk.models import ContentDiscoverySettings, ContentDiscoverySource, GovukTag


class ImportContentDiscoverySourcesCommandTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)

    def _write_csv(self, content: str) -> str:
        temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, temp_dir, ignore_errors=True)
        csv_path = Path(temp_dir) / "content-discovery-sources.csv"
        csv_path.write_text(content, encoding="utf-8")
        return str(csv_path)

    def test_imports_new_sources_with_site_fallback_and_tags(self):
        policy_tag = GovukTag.objects.create(slug="policy", name="Policy")
        news_tag = GovukTag.objects.create(slug="news", name="News")
        csv_path = self._write_csv(
            "\n".join(
                [
                    "url,name,disable_tls_verification,send_signed_bearer_jwt,default_tags",
                    "https://example.com/feed.xml,Example feed,false,true,policy|news",
                    "https://example.org/rss.xml,,true,false,",
                ]
            )
        )

        stdout = StringIO()
        call_command(
            "import_content_discovery_sources",
            csv_path,
            "--site-id",
            str(self.site.pk),
            stdout=stdout,
        )

        settings = ContentDiscoverySettings.for_site(self.site)
        sources = list(
            ContentDiscoverySource.objects.filter(settings=settings).order_by("sort_order")
        )
        self.assertEqual(len(sources), 2)

        self.assertEqual(sources[0].name, "Example feed")
        self.assertFalse(sources[0].disable_tls_verification)
        self.assertTrue(sources[0].send_signed_bearer_jwt)
        self.assertEqual(
            sources[0].get_default_tag_ids(),
            [policy_tag.pk, news_tag.pk],
        )

        self.assertEqual(sources[1].name, "")
        self.assertTrue(sources[1].disable_tls_verification)
        self.assertFalse(sources[1].send_signed_bearer_jwt)
        self.assertEqual(sources[1].get_default_tag_ids(), [])

        self.assertIn("created 2", stdout.getvalue())

    def test_updates_existing_source_matched_by_site_and_url(self):
        old_tag = GovukTag.objects.create(slug="old", name="Old")
        new_tag = GovukTag.objects.create(slug="new", name="New")
        settings = ContentDiscoverySettings.for_site(self.site)
        source = ContentDiscoverySource.objects.create(
            settings=settings,
            sort_order=0,
            name="Old name",
            url="https://example.com/feed.xml",
            disable_tls_verification=False,
            default_tags=[{"type": "tag", "value": old_tag.pk}],
        )
        csv_path = self._write_csv(
            "\n".join(
                [
                    "url,name,disable_tls_verification,send_signed_bearer_jwt,default_tags",
                    "https://example.com/feed.xml,Updated name,true,true,new",
                ]
            )
        )

        stdout = StringIO()
        call_command(
            "import_content_discovery_sources",
            csv_path,
            "--site-id",
            str(self.site.pk),
            stdout=stdout,
        )

        source.refresh_from_db()
        self.assertEqual(source.name, "Updated name")
        self.assertTrue(source.disable_tls_verification)
        self.assertTrue(source.send_signed_bearer_jwt)
        self.assertEqual(source.get_default_tag_ids(), [new_tag.pk])
        self.assertEqual(
            ContentDiscoverySource.objects.filter(
                settings=settings, url="https://example.com/feed.xml"
            ).count(),
            1,
        )
        self.assertIn("updated 1", stdout.getvalue())

    def test_requires_site_id_when_csv_does_not_define_it(self):
        csv_path = self._write_csv("url,name\nhttps://example.com/feed.xml,Name\n")

        with self.assertRaisesMessage(
            CommandError,
            "Provide a 'site_id' column or pass --site-id for all rows.",
        ):
            call_command("import_content_discovery_sources", csv_path)

    def test_rolls_back_when_unknown_tag_key_is_present(self):
        GovukTag.objects.create(slug="known", name="Known")
        csv_path = self._write_csv(
            "\n".join(
                [
                    "url,default_tags",
                    "https://example.com/feed.xml,known",
                    "https://example.org/feed.xml,missing-tag",
                ]
            )
        )

        with self.assertRaisesMessage(CommandError, "unknown tag key(s): missing-tag"):
            call_command(
                "import_content_discovery_sources",
                csv_path,
                "--site-id",
                str(self.site.pk),
            )

        settings = ContentDiscoverySettings.for_site(self.site)
        self.assertEqual(
            ContentDiscoverySource.objects.filter(settings=settings).count(),
            0,
        )
