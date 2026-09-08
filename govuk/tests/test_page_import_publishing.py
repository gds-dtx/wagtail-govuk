"""What the page importer is allowed to publish, and what it writes down.

The importer used to call ``page.save()`` and stop. Two consequences, both
found by running a real import as a real editor against a copy of the database:

  - Publish permission was never consulted. An account with edit but not
    publish changed 67 live pages by uploading a file, which the same account
    could not have done through the page editor.
  - No revision and no log entry were written, so the page history showed
    nothing at all. There was no author, no timestamp and nothing to revert to
    -- for the one write path that can change the entire site in one go.

Both branches are tested here: the publisher's import goes live, the editor's
waits in a draft with the live page untouched.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase, override_settings
from wagtail.models import GroupPagePermission, Page, PageLogEntry, Site

from govuk.models import ContentPage, SectionPage
from govuk.page_import_export import PAGE_EXPORT_FORMAT, import_pages_from_payload


def _feature_flags() -> dict[str, bool]:
    return {
        "SKILLS": True,
        "ORGANISATIONS": False,
        "PEOPLE_FINDER": False,
        "FEEDBACK": False,
    }


@override_settings(FEATURE_FLAGS=_feature_flags())
class PageImportPublishingTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root_page = self.site.root_page.specific
        self.section = self.root_page.add_child(
            instance=SectionPage(title="Benefits", slug="benefits")
        )
        self.page = self.section.add_child(
            instance=ContentPage(
                title="Apply", slug="apply", body="<p>The wording as published.</p>"
            )
        )
        self.page.save_revision().publish()
        self.page.refresh_from_db()

    # -- accounts -------------------------------------------------------------

    def _account(self, username, *, page_permissions):
        group = Group.objects.create(name=f"group-{username}")
        group.permissions.set(Permission.objects.filter(codename="access_admin"))
        for permission_type in page_permissions:
            GroupPagePermission.objects.create(
                group=group,
                page=Page.objects.get(pk=1),
                permission=Permission.objects.get(
                    content_type__app_label="wagtailcore", codename=permission_type
                ),
            )
        user = get_user_model().objects.create_user(
            username=username,
            email=f"{username}@example.gov.uk",
            password="unused-password",
            is_staff=True,
        )
        user.groups.add(group)
        return user

    def _editor(self):
        """Can change pages, cannot publish them."""
        return self._account("editor", page_permissions=["add_page", "change_page"])

    def _publisher(self):
        return self._account(
            "publisher", page_permissions=["add_page", "change_page", "publish_page"]
        )

    # -- payloads -------------------------------------------------------------

    def _payload(self, *, slug="apply", body="<p>The wording from the file.</p>"):
        return {
            "format": PAGE_EXPORT_FORMAT,
            "pages": [
                {
                    "model": "govuk.SectionPage",
                    "settings": {"slug": "benefits", "title": "Benefits"},
                    "fields": {},
                    "children": [
                        {
                            "model": "govuk.ContentPage",
                            "settings": {"slug": slug, "title": "Apply"},
                            "fields": {"body": body},
                        }
                    ],
                }
            ],
        }

    def _import(self, user, payload=None):
        return import_pages_from_payload(
            payload=payload or self._payload(), site=self.site, user=user
        )

    # -- the publisher's import -----------------------------------------------

    def test_a_publisher_import_goes_live(self):
        self._import(self._publisher())

        self.page.refresh_from_db()
        self.assertTrue(self.page.live)
        self.assertFalse(self.page.has_unpublished_changes)
        self.assertIn("from the file", self.page.specific.body)

    def test_a_publisher_import_is_recorded_in_the_page_history(self):
        publisher = self._publisher()
        self._import(publisher)

        actions = set(
            PageLogEntry.objects.filter(page=self.page, user=publisher).values_list(
                "action", flat=True
            )
        )
        self.assertIn("wagtail.edit", actions)
        self.assertIn("wagtail.publish", actions)

    def test_a_publisher_import_leaves_a_revision_to_revert_to(self):
        publisher = self._publisher()
        self._import(publisher)

        self.page.refresh_from_db()
        latest = self.page.get_latest_revision()
        self.assertIsNotNone(latest)
        self.assertEqual(latest.user, publisher)

    # -- the editor's import --------------------------------------------------

    def test_an_editor_import_does_not_change_the_live_page(self):
        result = self._import(self._editor())

        self.page.refresh_from_db()
        self.assertIn("as published", self.page.specific.body)
        self.assertNotIn("from the file", self.page.specific.body)
        self.assertEqual(result.skipped, 0)

    def test_an_editor_import_waits_in_a_draft(self):
        editor = self._editor()
        self._import(editor)

        self.page.refresh_from_db()
        self.assertTrue(self.page.has_unpublished_changes)
        latest = self.page.get_latest_revision()
        self.assertEqual(latest.user, editor)
        self.assertIn("from the file", latest.as_object().body)

    def test_an_editor_import_says_so(self):
        result = self._import(self._editor())

        # Both pages in the file: the section and the page under it.
        self.assertEqual(result.drafted, 2)
        self.assertTrue(
            any(
                "do not have permission to publish" in error
                for error in result.errors
            ),
            result.errors,
        )

    def test_an_editor_import_is_recorded_in_the_page_history(self):
        editor = self._editor()
        self._import(editor)

        entries = PageLogEntry.objects.filter(page=self.page, user=editor)
        self.assertTrue(entries.exists())
        self.assertNotIn(
            "wagtail.publish", set(entries.values_list("action", flat=True))
        )

    # -- new pages ------------------------------------------------------------

    def test_a_page_an_editor_creates_is_not_published(self):
        self._import(self._editor(), self._payload(slug="apply-later"))

        created = Page.objects.get(slug="apply-later")
        self.assertFalse(created.live)
        self.assertTrue(created.has_unpublished_changes)

    def test_a_page_a_publisher_creates_is_published(self):
        self._import(self._publisher(), self._payload(slug="apply-later"))

        created = Page.objects.get(slug="apply-later")
        self.assertTrue(created.live)
        self.assertIn("from the file", created.specific.body)

    def test_creating_a_page_is_logged_as_a_creation(self):
        publisher = self._publisher()
        self._import(publisher, self._payload(slug="apply-later"))

        created = Page.objects.get(slug="apply-later")
        actions = set(
            PageLogEntry.objects.filter(page=created).values_list("action", flat=True)
        )
        self.assertIn("wagtail.create", actions)
