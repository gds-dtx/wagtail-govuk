"""Syncing a content main page from its catalogue repo."""

from django.test import TestCase, override_settings
from wagtail.models import Site

from govuk.catalogue.bundle import GuidanceSyncError
from govuk.catalogue.render import RENDER_VERSION
from govuk.catalogue.sync import sync_catalogue_page
from govuk.models import CatalogueDocument, CatalogueEntry, ContentMainPage
from govuk.tests.catalogue_helpers import (
    CATALOGUE_REPOSITORY,
    COMMIT,
    GUIDE_COMMIT,
    OTHER_COMMIT,
    FakeGitHub,
    guidance_flags,
)


def make_content_main_page(parent, **kwargs) -> ContentMainPage:
    values = {
        "title": "Content",
        "slug": "content",
        "catalogue_repository": CATALOGUE_REPOSITORY,
        **kwargs,
    }
    page = parent.add_child(instance=ContentMainPage(**values))
    page.save_revision().publish()
    return ContentMainPage.objects.get(pk=page.pk)


@override_settings(FEATURE_FLAGS=guidance_flags())
class SyncTests(TestCase):
    def setUp(self):
        root = Site.objects.get(is_default_site=True).root_page.specific
        self.page = make_content_main_page(root)
        self.github = FakeGitHub()

    def sync(self, **kwargs):
        return sync_catalogue_page(self.page, fetch=self.github, **kwargs)

    def entry(self, entry_id):
        return self.page.snapshot.entries.get(entry_id=entry_id)

    def test_a_sync_stores_every_listed_entry_and_its_pages(self):
        result = self.sync()

        snapshot = self.page.snapshot
        self.assertEqual(snapshot.commit_sha, COMMIT)
        self.assertEqual(result.entries, 7)
        self.assertEqual(snapshot.entries.count(), 7)
        # 3 + 1 + 4 hosted pages, and the guide repo's 3.
        self.assertEqual(result.pages, 11)
        self.assertEqual(snapshot.site["vocabularies"]["groups"]["guidance"]["label"], "Guidance")
        self.assertEqual(snapshot.site["render_version"], RENDER_VERSION)
        self.assertTrue(any("broken-entry" in warning for warning in snapshot.warnings))

    def test_hosted_entries_come_from_the_catalogue_download(self):
        self.sync()
        # The catalogue is asked for its commit, then downloaded; no hosted
        # entry is fetched on its own. Only the repo entry is.
        self.assertEqual(
            self.github.calls,
            [CATALOGUE_REPOSITORY, CATALOGUE_REPOSITORY, "example/guide-repo"],
        )
        standard = self.entry("hosted-standard")
        self.assertEqual(standard.content_commit, COMMIT)
        self.assertEqual(
            sorted(standard.documents.values_list("path", flat=True)),
            ["", "first-point", "second-point"],
        )
        self.assertEqual(standard.structure["sequence"], ["first-point", "second-point"])

    def test_a_repo_entry_is_fetched_from_its_own_repo(self):
        self.sync()
        guide = self.entry("repo-guide")
        self.assertEqual(guide.content_commit, GUIDE_COMMIT)
        self.assertEqual(
            sorted(guide.documents.values_list("path", flat=True)),
            ["", "topics/alerting", "topics/alerting/escalation"],
        )
        self.assertEqual(guide.structure["navigation"][0]["items"][0]["path"], "topics/alerting")

    def test_external_entries_have_no_pages(self):
        self.sync()
        policy = self.entry("external-policy")
        self.assertFalse(policy.has_pages)
        self.assertEqual(policy.external_url, "https://www.gov.uk/guidance/external-policy")
        self.assertFalse(policy.documents.exists())
        tool = self.entry("linked-tool")
        self.assertTrue(tool.has_pages)
        self.assertFalse(tool.documents.exists())

    def test_nothing_is_fetched_again_when_nothing_has_moved(self):
        self.sync()
        self.github.calls.clear()
        result = self.sync()
        self.assertTrue(result.unchanged)
        # Only the two commits are asked for: the catalogue's and the repo entry's.
        self.assertEqual(self.github.calls, [CATALOGUE_REPOSITORY, "example/guide-repo"])

    def test_a_new_catalogue_commit_reuses_an_unmoved_repo_entry(self):
        self.sync()
        guide_document_ids = set(self.entry("repo-guide").documents.values_list("title", flat=True))
        self.github.commit = OTHER_COMMIT
        self.github.calls.clear()
        self.sync()
        # The repo entry is only asked for its commit, and not downloaded again.
        self.assertEqual(self.github.calls.count("example/guide-repo"), 1)
        self.assertEqual(
            set(self.entry("repo-guide").documents.values_list("title", flat=True)),
            guide_document_ids,
        )
        snapshot = self.page.snapshot
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.commit_sha, OTHER_COMMIT)

    def test_a_moved_repo_entry_triggers_a_sync(self):
        self.sync()
        self.github.guide_commit = OTHER_COMMIT
        result = self.sync()
        self.assertFalse(result.unchanged)
        self.assertEqual(self.entry("repo-guide").content_commit, OTHER_COMMIT)

    def test_a_failed_entry_keeps_its_last_good_pages(self):
        self.sync()
        self.github.failing.add("example/guide-repo")
        result = self.sync(force=True)
        self.assertEqual(result.failed, ["repo-guide"])
        guide = self.entry("repo-guide")
        self.assertIn("unavailable", guide.content_error)
        self.assertTrue(guide.has_pages)
        self.assertEqual(guide.documents.count(), 3)

    def test_a_failed_entry_with_no_pages_links_to_its_repo(self):
        self.github.failing.add("example/guide-repo")
        self.sync()
        guide = self.entry("repo-guide")
        self.assertFalse(guide.has_pages)
        self.assertEqual(guide.external_url, "https://github.com/example/guide-repo")

    def test_a_failed_catalogue_keeps_the_last_good_copy(self):
        self.sync()
        self.github.commit = OTHER_COMMIT
        self.github.catalogue = {"README.md": b"not a catalogue"}
        with self.assertRaises(GuidanceSyncError):
            self.sync()
        snapshot = self.page.snapshot
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.commit_sha, COMMIT)
        self.assertEqual(snapshot.entries.count(), 7)
        self.assertIn("catalogue.yml", snapshot.last_error)

    def test_a_copy_made_by_older_code_is_rendered_again(self):
        self.sync()
        snapshot = self.page.snapshot
        snapshot.site = {**snapshot.site, "render_version": RENDER_VERSION - 1}
        snapshot.save()
        self.assertFalse(self.sync().unchanged)

    def test_changing_the_source_settings_needs_a_sync(self):
        self.sync()
        self.assertFalse(self.page.needs_sync())
        self.page.catalogue_branch = "other"
        self.assertTrue(self.page.needs_sync())

    def test_deleting_the_page_deletes_its_copy(self):
        self.sync()
        self.page.delete()
        self.assertFalse(CatalogueEntry.objects.exists())
        self.assertFalse(CatalogueDocument.objects.exists())
