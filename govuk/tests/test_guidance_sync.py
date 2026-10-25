"""Fetching and syncing a guidance repo into a page's snapshot."""

import tarfile
from unittest import mock

from django.test import SimpleTestCase, TestCase, override_settings
from wagtail.models import Site

from govuk.guidance import fetch
from govuk.guidance.bundle import GuidanceSyncError
from govuk.guidance.render import RENDER_VERSION
from govuk.guidance.sync import sync_guidance_page
from govuk.models import GuidanceCollectionPage, GuidanceDocument
from govuk.tests.guidance_helpers import (
    COMMIT,
    OTHER_COMMIT,
    fake_fetch,
    guidance_flags,
    manifest_files,
    tarball,
    tech_docs_files,
)


def make_guidance_page(parent, **kwargs) -> GuidanceCollectionPage:
    values = {
        "title": "The Example Way",
        "slug": "example-way",
        "repository_url": "https://github.com/example/example-way",
        "branch": "main",
        **kwargs,
    }
    page = parent.add_child(instance=GuidanceCollectionPage(**values))
    page.save_revision().publish()
    return GuidanceCollectionPage.objects.get(pk=page.pk)


class ReadTarballTests(SimpleTestCase):
    def test_strips_the_top_folder_and_reads_text_files(self):
        files = fetch.read_tarball(tarball(tech_docs_files()))
        self.assertIn("config/tech-docs.yml", files)
        self.assertIsInstance(files["source/index.html.md.erb"], bytes)
        # Not a text file: recorded by name only.
        self.assertIsNone(files["source/images/logo.png"])

    def test_limits_to_the_subdirectory(self):
        nested = {f"guidance/one/{name}": data for name, data in manifest_files().items()}
        files = fetch.read_tarball(tarball(nested), subdirectory="guidance/one")
        self.assertIn("guidance.yml", files)
        self.assertNotIn("guidance/one/guidance.yml", files)

    def test_unsafe_members_are_ignored(self):
        link = tarfile.TarInfo("top/source/link.md")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        escape = tarfile.TarInfo("top/../../escape.md")
        escape.size = 4
        files = fetch.read_tarball(
            tarball({"guidance.yml": b"x"}, top="top", extra=[(link, None), (escape, b"oops")])
        )
        self.assertEqual(set(files), {"guidance.yml"})

    def test_an_empty_directory_is_an_error(self):
        with self.assertRaises(GuidanceSyncError):
            fetch.read_tarball(tarball({"a.md": b"x"}), subdirectory="nothing-here")

    def test_a_bad_archive_is_an_error(self):
        with self.assertRaises(GuidanceSyncError):
            fetch.read_tarball(b"not a tarball")

    def test_downloads_are_capped(self):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b"x" * 11
        with (
            mock.patch.object(fetch, "_validate_source_fetch_url"),
            mock.patch.object(fetch, "_validate_source_fetch_dns_resolution"),
            mock.patch.object(fetch, "_open_source_request", return_value=response),
            self.assertRaisesMessage(GuidanceSyncError, "larger than"),
        ):
            fetch._get("https://codeload.github.com/x", accept="*/*", timeout=1, max_bytes=10)


@override_settings(FEATURE_FLAGS=guidance_flags())
class SyncTests(TestCase):
    def setUp(self):
        root = Site.objects.get(is_default_site=True).root_page.specific
        self.page = make_guidance_page(root)

    def test_a_sync_stores_the_documents_and_site(self):
        result = sync_guidance_page(self.page, fetch=fake_fetch(tech_docs_files()))

        snapshot = self.page.snapshot
        self.assertEqual(result.created, 7)
        self.assertEqual(snapshot.commit_sha, COMMIT)
        self.assertEqual(snapshot.source_key, "example/example-way@main:")
        self.assertEqual(snapshot.site["title"], "The Example Way")
        self.assertTrue(snapshot.warnings)
        self.assertEqual(snapshot.documents.count(), 7)

    def test_a_resync_updates_and_removes(self):
        sync_guidance_page(self.page, fetch=fake_fetch(tech_docs_files()))
        files = tech_docs_files()
        del files["source/accessibility.html.md.erb"]
        files["source/standards/monitoring.html.md.erb"] = b"---\ntitle: Monitoring, renamed\n---\n# x\n"

        result = sync_guidance_page(self.page, fetch=fake_fetch(files, OTHER_COMMIT))

        self.assertEqual((result.created, result.updated, result.removed), (0, 1, 1))
        self.assertEqual(
            self.page.snapshot.documents.get(path="standards/monitoring").title,
            "Monitoring, renamed",
        )

    def test_a_failed_sync_keeps_the_last_good_copy(self):
        sync_guidance_page(self.page, fetch=fake_fetch(tech_docs_files()))

        with self.assertRaises(GuidanceSyncError):
            sync_guidance_page(self.page, fetch=fake_fetch({"nothing.txt": b""}, OTHER_COMMIT))

        snapshot = self.page.snapshot
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.commit_sha, COMMIT)
        self.assertEqual(snapshot.documents.count(), 7)
        self.assertIn("neither a guidance.yml", snapshot.last_error)

    def test_an_unchanged_branch_is_not_fetched_again(self):
        sync_guidance_page(self.page, fetch=fake_fetch(tech_docs_files()))
        with (
            mock.patch("govuk.guidance.sync.resolve_commit", return_value=COMMIT),
            mock.patch("govuk.guidance.sync.fetch_files") as fetch_files,
        ):
            result = sync_guidance_page(self.page)
        self.assertTrue(result.unchanged)
        fetch_files.assert_not_called()

    def test_a_copy_made_by_older_code_is_rendered_again(self):
        """Same commit, but the renderer has changed since: re-render it."""
        sync_guidance_page(self.page, fetch=fake_fetch(tech_docs_files()))
        snapshot = self.page.snapshot
        snapshot.site = {**snapshot.site, "render_version": RENDER_VERSION - 1}
        snapshot.save()
        with (
            mock.patch("govuk.guidance.sync.resolve_commit", return_value=COMMIT),
            mock.patch("govuk.guidance.sync.fetch_files", return_value=tech_docs_files()) as fetch_files,
        ):
            result = sync_guidance_page(self.page)
        self.assertFalse(result.unchanged)
        fetch_files.assert_called_once()
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.site["render_version"], RENDER_VERSION)

    def test_changed_source_settings_force_a_fetch(self):
        sync_guidance_page(self.page, fetch=fake_fetch(tech_docs_files()))
        self.page.subdirectory = "docs"
        self.page.save()
        self.assertTrue(self.page.needs_sync())
        with (
            mock.patch("govuk.guidance.sync.resolve_commit", return_value=COMMIT),
            mock.patch("govuk.guidance.sync.fetch_files", return_value=manifest_files()) as fetch_files,
        ):
            sync_guidance_page(self.page)
        fetch_files.assert_called_once()

    def test_deleting_the_page_deletes_its_copy(self):
        sync_guidance_page(self.page, fetch=fake_fetch(tech_docs_files()))
        self.page.delete()
        self.assertFalse(GuidanceDocument.objects.exists())


class FetchSourceGuardTests(SimpleTestCase):
    def test_requests_go_through_the_content_discovery_guards(self):
        with self.assertRaisesMessage(GuidanceSyncError, "localhost hosts are not allowed"):
            fetch._get("https://localhost/x", accept="*/*", timeout=1, max_bytes=10)

    @override_settings(GUIDANCE_GITHUB_TOKEN="token-value")
    def test_the_token_is_only_sent_to_the_github_api(self):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = COMMIT.encode()
        with (
            mock.patch.object(fetch, "_validate_source_fetch_url"),
            mock.patch.object(fetch, "_validate_source_fetch_dns_resolution"),
            mock.patch.object(fetch, "_open_source_request", return_value=response) as opener,
        ):
            fetch._get("https://api.github.com/repos/a/b/commits/main", accept="*/*", timeout=1, max_bytes=100)
            fetch._get("https://codeload.github.com/a/b/tar.gz/x", accept="*/*", timeout=1, max_bytes=100)
        api_request, codeload_request = (call.args[0] for call in opener.call_args_list)
        self.assertEqual(api_request.get_header("Authorization"), "Bearer token-value")
        self.assertIsNone(codeload_request.get_header("Authorization"))
