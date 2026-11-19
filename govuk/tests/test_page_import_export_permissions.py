"""What Import / Export lets an account do, and what an import publishes and records.

Every view is behind ``require_admin_access`` and nothing else, so each write
the importer makes checks the permission the rest of the admin would ask for.
"""

import json

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from wagtail.models import GroupPagePermission, Page, PageLogEntry, PageViewRestriction

from govuk.models import (
    CapabilityFrameworkWordingSettings,
    ContentPage,
    FrameworkMainPage,
    GovukChangelogEntry,
    GovukRole,
    GovukSkill,
    GovukTag,
)
from govuk.page_import_export import PAGE_EXPORT_FORMAT
from govuk.tests.helpers import body_html, text_body
from govuk.tests.test_page_import_export import (
    ImportExportTestCase,
    framework_home_node,
)


def _staff_user(username: str, *, codenames=(), page_permissions=()):
    """A staff account with the Wagtail admin open to it and only what is named.

    ``page_permissions`` is a list of ``(page, codename)`` pairs.
    """
    group = Group.objects.create(name=f"group-{username}")
    group.permissions.set(Permission.objects.filter(codename__in=["access_admin", *codenames]))
    for page, codename in page_permissions:
        GroupPagePermission.objects.create(
            group=group,
            page=page,
            permission=Permission.objects.get(
                content_type__app_label="wagtailcore", codename=codename
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


class PageImportPublishingTests(ImportExportTestCase):
    """The importer once saved pages without consulting publish permission or writing history."""

    def setUp(self):
        super().setUp()
        self._add_benefits_pages("<p>The wording as published.</p>")
        self.content_page.save_revision().publish()
        self.content_page.refresh_from_db()

    def _account(self, username, *codenames):
        tree_root = Page.objects.get(pk=1)
        return _staff_user(
            username, page_permissions=[(tree_root, codename) for codename in codenames]
        )

    def _editor(self):
        """Can change pages, cannot publish them."""
        return self._account("editor", "add_page", "change_page")

    def _publisher(self):
        return self._account("publisher", "add_page", "change_page", "publish_page")

    def _payload(self, *, slug="apply", body="<p>The wording from the file.</p>"):
        return {
            "format": PAGE_EXPORT_FORMAT,
            "pages": [
                {
                    "model": "govuk.ContentPage",
                    "settings": {"slug": "benefits", "title": "Benefits"},
                    "fields": {},
                    "children": [
                        {
                            "model": "govuk.ContentPage",
                            "settings": {"slug": slug, "title": "Apply"},
                            "fields": {"body": text_body(body)},
                        }
                    ],
                }
            ],
        }

    # -- the publisher's import -----------------------------------------------

    def test_a_publisher_import_goes_live(self):
        self._import(self._payload(), user=self._publisher())

        self.content_page.refresh_from_db()
        self.assertTrue(self.content_page.live)
        self.assertFalse(self.content_page.has_unpublished_changes)
        self.assertIn("from the file", body_html(self.content_page.specific))

    def test_a_publisher_import_is_recorded_in_the_page_history(self):
        publisher = self._publisher()
        self._import(self._payload(), user=publisher)

        actions = set(
            PageLogEntry.objects.filter(page=self.content_page, user=publisher).values_list(
                "action", flat=True
            )
        )
        self.assertIn("wagtail.edit", actions)
        self.assertIn("wagtail.publish", actions)

    def test_a_publisher_import_leaves_a_revision_to_revert_to(self):
        publisher = self._publisher()
        self._import(self._payload(), user=publisher)

        self.content_page.refresh_from_db()
        latest = self.content_page.get_latest_revision()
        self.assertIsNotNone(latest)
        self.assertEqual(latest.user, publisher)

    # -- the editor's import --------------------------------------------------

    def test_an_editor_import_does_not_change_the_live_page(self):
        result = self._import(self._payload(), user=self._editor())

        self.content_page.refresh_from_db()
        self.assertIn("as published", body_html(self.content_page.specific))
        self.assertNotIn("from the file", body_html(self.content_page.specific))
        self.assertEqual(result.skipped, 0)

    def test_an_editor_import_waits_in_a_draft(self):
        editor = self._editor()
        self._import(self._payload(), user=editor)

        self.content_page.refresh_from_db()
        self.assertTrue(self.content_page.has_unpublished_changes)
        latest = self.content_page.get_latest_revision()
        self.assertEqual(latest.user, editor)
        self.assertIn("from the file", body_html(latest.as_object()))

    def test_an_editor_import_says_so(self):
        result = self._import(self._payload(), user=self._editor())

        # Both pages in the file: the section and the page under it.
        self.assertEqual(result.drafted, 2)
        self.assertTrue(
            any("do not have permission to publish" in error for error in result.errors),
            result.errors,
        )

    def test_an_editor_import_is_recorded_in_the_page_history(self):
        editor = self._editor()
        self._import(self._payload(), user=editor)

        entries = PageLogEntry.objects.filter(page=self.content_page, user=editor)
        self.assertTrue(entries.exists())
        self.assertNotIn("wagtail.publish", set(entries.values_list("action", flat=True)))

    # -- new pages ------------------------------------------------------------

    def test_a_page_an_editor_creates_is_not_published(self):
        self._import(self._payload(slug="apply-later"), user=self._editor())

        created = Page.objects.get(slug="apply-later")
        self.assertFalse(created.live)
        self.assertTrue(created.has_unpublished_changes)

    def test_a_page_a_publisher_creates_is_published(self):
        self._import(self._payload(slug="apply-later"), user=self._publisher())

        created = Page.objects.get(slug="apply-later")
        self.assertTrue(created.live)
        self.assertIn("from the file", body_html(created.specific))

    def test_creating_a_page_is_logged_as_a_creation(self):
        self._import(self._payload(slug="apply-later"), user=self._publisher())

        created = Page.objects.get(slug="apply-later")
        actions = set(PageLogEntry.objects.filter(page=created).values_list("action", flat=True))
        self.assertIn("wagtail.create", actions)


class ImportExportPermissionTests(ImportExportTestCase):
    def setUp(self):
        super().setUp()
        self._add_benefits_pages()
        self.skill = GovukSkill.objects.create(slug="accessibility", title="Accessibility")
        self.role = GovukRole.objects.create(slug="data-analyst", title="Data analyst")

    def _export(self, **selections):
        return self.client.post(f"{self.export_url}?site={self.site.pk}", selections)

    # -- import: snippets -----------------------------------------------------

    def test_a_snippet_is_not_rewritten_by_an_account_that_may_only_reach_the_admin(self):
        self.client.force_login(_staff_user("nobody"))

        for key, snippet, original_title, permission in (
            ("roles", self.role, "Data analyst", "govuk.change_govukrole"),
            ("skills", self.skill, "Accessibility", "govuk.change_govukskill"),
        ):
            with self.subTest(key):
                response = self._post_import(
                    {key: [{"slug": snippet.slug, "title": "Renamed by nobody"}]}
                )

                snippet.refresh_from_db()
                self.assertEqual(snippet.title, original_title)
                self.assertIn(permission, self._message_text(response))

    def test_a_role_that_does_not_exist_yet_is_not_created_by_that_account(self):
        self.client.force_login(_staff_user("nobody"))

        self._post_import({"roles": [{"slug": "planted", "title": "Planted"}]})

        self.assertFalse(GovukRole.objects.filter(slug="planted").exists())

    def test_permission_to_change_a_role_is_not_permission_to_add_one(self):
        """Separate in the snippet admin, so separate here: a file must not invent roles under cover of corrections."""
        self.client.force_login(_staff_user("editor", codenames=["change_govukrole"]))

        response = self._post_import(
            {
                "roles": [
                    {"slug": "data-analyst", "title": "Corrected"},
                    {"slug": "invented", "title": "Invented"},
                ]
            }
        )

        self.role.refresh_from_db()
        self.assertEqual(self.role.title, "Corrected")
        self.assertFalse(GovukRole.objects.filter(slug="invented").exists())
        self.assertIn("govuk.add_govukrole", self._message_text(response))

    def test_the_changelog_is_not_emptied_by_an_account_that_may_not_delete_it(self):
        GovukChangelogEntry.objects.create(date="2026-01-01", note="Framework published")
        self.client.force_login(_staff_user("nobody"))

        response = self._post_import({"changelog": [], "tags": [{"slug": "a", "name": "A"}]})

        self.assertEqual(GovukChangelogEntry.objects.count(), 1)
        self.assertIn("govuk.delete_govukchangelogentry", self._message_text(response))

    def test_a_superuser_still_imports_everything(self):
        """The check has to be invisible to the account that runs a cutover."""
        self.client.force_login(self._superuser())

        self._post_import(
            {
                "skills": [{"slug": "accessibility", "title": "Renamed"}],
                "roles": [{"slug": "data-analyst", "title": "Renamed too"}],
            }
        )

        self.skill.refresh_from_db()
        self.role.refresh_from_db()
        self.assertEqual(self.skill.title, "Renamed")
        self.assertEqual(self.role.title, "Renamed too")

    # -- import: the tag dictionary -------------------------------------------

    def test_a_tag_named_only_in_the_tag_list_is_not_coined_by_that_account(self):
        """The top-level 'tags' list edits the dictionary, which is what the tag snippet menu is for."""
        self.client.force_login(_staff_user("nobody"))

        response = self._post_import({"tags": [{"slug": "invented-tag", "name": "Invented"}]})

        self.assertFalse(GovukTag.objects.filter(slug="invented-tag").exists())
        self.assertIn("govuk.add_govuktag", self._message_text(response))

    def test_a_tag_on_a_page_the_account_may_edit_still_arrives(self):
        """The page editor's free-tagging field already lets a page editor coin a tag."""
        self.client.force_login(
            _staff_user(
                "editor",
                page_permissions=[(self.root_page, "add_page"), (self.root_page, "publish_page")],
            )
        )

        self._post_import(
            {
                "pages": [
                    {
                        "model": "govuk.ContentPage",
                        "settings": {"slug": "tagged", "title": "Tagged"},
                        "tags": [{"slug": "from-a-page", "name": "From a page"}],
                    }
                ]
            }
        )

        self.assertTrue(GovukTag.objects.filter(slug="from-a-page").exists())
        # And it reached the page, rather than merely being created beside it.
        page = ContentPage.objects.get(slug="tagged")
        self.assertEqual([tag.slug for tag in page.tags.all()], ["from-a-page"])

    def test_nothing_is_reported_when_the_tag_list_would_coin_nothing(self):
        GovukTag.objects.create(slug="already-here", name="Already here")
        self.client.force_login(_staff_user("nobody"))

        response = self._post_import({"tags": [{"slug": "already-here", "name": "Already here"}]})

        self.assertNotIn("govuk.add_govuktag", self._message_text(response))

    # -- export: the shared password ------------------------------------------

    def _protect(self, page, password="correct-horse-battery-staple"):
        PageViewRestriction.objects.create(
            page=page,
            restriction_type=PageViewRestriction.PASSWORD,
            password=password,
        )

    def _exported_privacy(self, response, slug: str):
        payload = json.loads(response.content.decode())

        def walk(node):
            yield node
            for child in node.get("children") or []:
                yield from walk(child)

        for node in walk(payload["pages"][0]):
            if node["settings"].get("slug") == slug:
                return node["privacy"]
        raise AssertionError(f"'{slug}' was not in the export")

    def test_the_export_carries_a_password_for_someone_who_may_set_it(self):
        self._protect(self.content_page)
        self.client.force_login(self._superuser())

        response = self._export(page_ids=[str(self.benefits_page.pk)])

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self._exported_privacy(response, "apply"),
            [{"type": "password", "password": "correct-horse-battery-staple"}],
        )

    def test_the_export_withholds_it_from_someone_who_may_not(self):
        """Wagtail's privacy dialog is shut to this account, so the download must not be a way round it."""
        self._protect(self.content_page)
        self.client.force_login(
            _staff_user("editor", page_permissions=[(self.root_page, "change_page")])
        )

        response = self._export(page_ids=[str(self.benefits_page.pk)])

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("correct-horse-battery-staple", response.content.decode())
        # The restriction is still named, so the file still says the page is not public.
        self.assertEqual(self._exported_privacy(response, "apply"), [{"type": "password"}])

    # -- export and listing: pages and snippets this account may not edit -----

    def test_a_page_this_account_may_not_edit_is_not_offered_for_export(self):
        self.client.force_login(
            _staff_user("editor", page_permissions=[(self.content_page, "change_page")])
        )

        response = self.client.get(self.index_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Apply")
        self.assertNotContains(response, "Benefits")

    def test_nor_exported_when_its_id_is_asked_for_directly(self):
        self.client.force_login(
            _staff_user("editor", page_permissions=[(self.content_page, "change_page")])
        )

        response = self._export(page_ids=[str(self.benefits_page.pk), str(self.content_page.pk)])

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content.decode())
        self.assertEqual([node["settings"]["slug"] for node in payload["pages"]], ["apply"])

    def test_snippets_are_not_offered_to_an_account_that_may_not_manage_them(self):
        self.client.force_login(
            _staff_user("nobody", page_permissions=[(self.root_page, "change_page")])
        )

        response = self.client.get(self.index_url)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Data analyst")
        self.assertNotContains(response, "Accessibility")

    def test_nor_exported_when_their_ids_are_asked_for_directly(self):
        self.client.force_login(
            _staff_user("nobody", page_permissions=[(self.root_page, "change_page")])
        )

        response = self._export(
            page_ids=[str(self.benefits_page.pk)],
            role_ids=[str(self.role.pk)],
            skill_ids=[str(self.skill.pk)],
        )

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content.decode())
        self.assertEqual(payload["roles"], [])
        self.assertEqual(payload["skills"], [])


class ImportedPasswordRestrictionTests(ImportExportTestCase):
    """An export by someone who may not read passwords carries the restriction without one."""

    def setUp(self):
        super().setUp()
        self.page = self.root_page.add_child(
            instance=ContentPage(title="Apply", slug="apply", body=text_body("<p>Body</p>"))
        )
        self.client.force_login(self._superuser())

    def _import_privacy(self, privacy):
        return self._post_import(
            {
                "format": PAGE_EXPORT_FORMAT,
                "pages": [
                    {
                        "model": "govuk.ContentPage",
                        "settings": {"slug": "apply", "title": "Apply"},
                        "privacy": privacy,
                    }
                ],
            }
        )

    def test_the_password_already_on_the_page_stands(self):
        PageViewRestriction.objects.create(
            page=self.page,
            restriction_type=PageViewRestriction.PASSWORD,
            password="kept",
        )

        self._import_privacy([{"type": "password"}])

        self.assertEqual(PageViewRestriction.objects.get(page=self.page).password, "kept")

    def test_a_page_with_no_password_of_its_own_is_not_given_an_empty_one(self):
        response = self._import_privacy([{"type": "password"}])

        self.assertFalse(PageViewRestriction.objects.filter(page=self.page).exists())
        self.assertIn("gives no password for it", self._message_text(response))

    def test_a_password_the_file_does_name_is_still_applied(self):
        self._import_privacy([{"type": "password", "password": "from-the-file"}])

        self.assertEqual(PageViewRestriction.objects.get(page=self.page).password, "from-the-file")


class PlaceholderHomePageReplacementPermissionTests(ImportExportTestCase):
    """Replacing the placeholder home page repoints the site and deletes the page, descendants and all."""

    def setUp(self):
        super().setUp()
        # Left empty and of the starter type, which is what makes it a replaceable
        # placeholder: a content page since 0089, so the file is rooted at the
        # framework main page to differ from it.
        self.placeholder = self.root_page
        self.assertIsInstance(self.placeholder, ContentPage)
        self.assertFalse(self.placeholder.get_children().exists())

    def _import_framework_home(self):
        return self._post_import({"format": PAGE_EXPORT_FORMAT, "pages": [framework_home_node()]})

    def _assert_refused(self, response, message: str):
        self.site.refresh_from_db()
        self.assertEqual(self.site.root_page.pk, self.placeholder.pk)
        self.assertTrue(Page.objects.filter(pk=self.placeholder.pk).exists())
        self.assertIn(message, self._message_text(response))

    def test_an_account_with_only_admin_access_cannot_replace_the_home_page(self):
        self.client.force_login(_staff_user("nobody"))

        self._assert_refused(self._import_framework_home(), "wagtailcore.change_site")

    def test_full_rights_over_the_home_page_are_not_rights_over_the_site(self):
        """This account may delete the home page; it may not repoint the Site, which makes it a replacement."""
        user = _staff_user(
            "editor",
            page_permissions=[
                (self.placeholder, codename)
                for codename in ("add_page", "change_page", "publish_page", "bulk_delete_page")
            ],
        )
        self.assertTrue(self.placeholder.permissions_for_user(user).can_delete())
        self.client.force_login(user)

        self._assert_refused(self._import_framework_home(), "wagtailcore.change_site")

    def test_rights_over_the_site_are_not_rights_over_the_page(self):
        """The other half: allowed to repoint a Site, not to delete this page."""
        self.client.force_login(_staff_user("site-admin", codenames=["change_site"]))

        self._assert_refused(self._import_framework_home(), "permission to delete")

    def test_an_administrator_still_replaces_it(self):
        """The check must not close the door on the person it was built for."""
        self.client.force_login(self._superuser())

        self._import_framework_home()

        self.site.refresh_from_db()
        new_root = self.site.root_page.specific
        self.assertIsInstance(new_root, FrameworkMainPage)
        self.assertEqual(new_root.slug, "home")
        self.assertFalse(Page.objects.filter(pk=self.placeholder.pk).exists())


class WordingImportPermissionTests(ImportExportTestCase):
    """Wagtail will not open the wording form without permission to change it, so neither may a file."""

    def setUp(self):
        super().setUp()
        self.editor = get_user_model().objects.create_user(
            username="wording-editor",
            email="wording-editor@example.gov.uk",
            password="unused-password",
        )
        # The group Wagtail ships: the admin, and pages, and nothing else.
        self.editor.groups.add(Group.objects.get(name="Editors"))
        self.client.force_login(self.editor)
        self.wording = CapabilityFrameworkWordingSettings.for_site(self.site)
        self.wording.updates_heading = "Updates"
        self.wording.save()

    def _reloaded(self) -> CapabilityFrameworkWordingSettings:
        return CapabilityFrameworkWordingSettings.objects.get(pk=self.wording.pk)

    def test_an_editor_who_cannot_open_the_form_cannot_import_the_wording(self):
        self.assertFalse(self.editor.has_perm("govuk.change_capabilityframeworkwordingsettings"))

        response = self._post_import({"wording": {"updates_heading": "Rewritten"}})

        self.assertEqual(self._reloaded().updates_heading, "Updates")
        self.assertContains(response, "you do not have permission")

    def test_the_pages_in_the_same_file_still_arrive(self):
        """Every export carries the wording, so refusing the whole file would block page imports."""
        response = self._post_import(
            {
                "wording": {"updates_heading": "Rewritten"},
                "pages": [
                    {
                        "model": "govuk.ContentPage",
                        "settings": {"title": "Guidance", "slug": "guidance"},
                        "fields": {"body": text_body("<p>Imported.</p>")},
                    }
                ],
            }
        )

        self.assertEqual(self._reloaded().updates_heading, "Updates")
        self.assertTrue(ContentPage.objects.filter(slug="guidance").exists())
        self.assertContains(response, "you do not have permission")

    def test_the_permission_the_form_asks_for_is_the_one_that_lets_it_through(self):
        self.editor.user_permissions.add(
            Permission.objects.get(
                content_type=ContentType.objects.get_for_model(CapabilityFrameworkWordingSettings),
                codename="change_capabilityframeworkwordingsettings",
            )
        )
        self.client.force_login(get_user_model().objects.get(pk=self.editor.pk))

        self._post_import({"wording": {"updates_heading": "Rewritten"}})

        self.assertEqual(self._reloaded().updates_heading, "Rewritten")
