"""What Import / Export lets an account do that the rest of the admin does not.

Every view here is behind ``require_admin_access`` and nothing else, so the
only permission any of it asked for was the one that opens the admin at all.
The pages in a file were checked one by one; nothing else was. An account with
no permission beyond access to the admin could rewrite every skill and role in
the framework by uploading a file, and download the shared password of a page
it may not open.
"""

import json

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from wagtail.models import (
    GroupPagePermission,
    PageViewRestriction,
    Site,
)

from govuk.models import (
    ContentPage,
    GovukChangelogEntry,
    GovukRole,
    GovukSkill,
    GovukTag,
    SectionPage,
)
from govuk.page_import_export import PAGE_EXPORT_FORMAT


def _feature_flags(*, skills_enabled: bool = True) -> dict[str, bool]:
    return {
        "SKILLS": skills_enabled,
        "ORGANISATIONS": False,
        "PEOPLE_FINDER": False,
        "FEEDBACK": False,
    }


@override_settings(FEATURE_FLAGS=_feature_flags())
class ImportExportPermissionTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root_page = self.site.root_page.specific
        self.section_page = self.root_page.add_child(
            instance=SectionPage(title="Benefits", slug="benefits")
        )
        self.content_page = self.section_page.add_child(
            instance=ContentPage(title="Apply", slug="apply", body="<p>Body</p>")
        )

        self.skill = GovukSkill.objects.create(slug="accessibility", title="Accessibility")
        self.role = GovukRole.objects.create(slug="data-analyst", title="Data analyst")

        self.index_url = reverse("govuk_pages_import_export")
        self.export_url = reverse("govuk_pages_export")
        self.import_url = reverse("govuk_pages_import")

    # -- users ----------------------------------------------------------------

    def _user(self, username: str, *, codenames=(), page_permissions=()):
        """A staff account with the Wagtail admin open to it and nothing else,
        unless this test names something else."""
        group = Group.objects.create(name=f"group-{username}")
        group.permissions.set(
            Permission.objects.filter(
                codename__in=["access_admin", *codenames]
            )
        )
        for page, permission_type in page_permissions:
            GroupPagePermission.objects.create(
                group=group,
                page=page,
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

    def _superuser(self):
        return get_user_model().objects.create_superuser(
            username="admin-user",
            email="admin@example.gov.uk",
            password="unused-password",
        )

    # -- helpers --------------------------------------------------------------

    def _import(self, payload: dict):
        return self.client.post(
            self.import_url,
            {
                "json_file": SimpleUploadedFile(
                    "export.json",
                    json.dumps(payload).encode(),
                    content_type="application/json",
                ),
                "action": "import",
            },
            follow=True,
        )

    def _export(self, **selections):
        return self.client.post(
            f"{self.export_url}?site={self.site.pk}",
            {"action": "export", **selections},
        )

    def _messages(self, response) -> str:
        return " ".join(str(message) for message in response.context["messages"])

    # -- import: snippets -----------------------------------------------------

    def test_a_role_is_not_rewritten_by_an_account_that_may_only_reach_the_admin(self):
        self.client.force_login(self._user("nobody"))

        response = self._import(
            {"roles": [{"slug": "data-analyst", "title": "Renamed by nobody"}]}
        )

        self.role.refresh_from_db()
        self.assertEqual(self.role.title, "Data analyst")
        self.assertIn("govuk.change_govukrole", self._messages(response))

    def test_a_skill_is_not_rewritten_by_that_account_either(self):
        self.client.force_login(self._user("nobody"))

        response = self._import(
            {"skills": [{"slug": "accessibility", "title": "Renamed by nobody"}]}
        )

        self.skill.refresh_from_db()
        self.assertEqual(self.skill.title, "Accessibility")
        self.assertIn("govuk.change_govukskill", self._messages(response))

    def test_a_role_that_does_not_exist_yet_is_not_created_by_that_account(self):
        self.client.force_login(self._user("nobody"))

        self._import({"roles": [{"slug": "planted", "title": "Planted"}]})

        self.assertFalse(GovukRole.objects.filter(slug="planted").exists())

    def test_permission_to_change_a_role_is_not_permission_to_add_one(self):
        """The two are separate in the snippet admin, so they are separate here.

        Someone trusted to correct the framework's roles is not necessarily
        trusted to invent new ones, and a file is a quiet way to do the second
        under cover of the first.
        """
        self.client.force_login(self._user("editor", codenames=["change_govukrole"]))

        response = self._import(
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
        self.assertIn("govuk.add_govukrole", self._messages(response))

    def test_the_changelog_is_not_emptied_by_an_account_that_may_not_delete_it(self):
        GovukChangelogEntry.objects.create(date="2026-01-01", note="Framework published")
        self.client.force_login(self._user("nobody"))

        response = self._import({"changelog": [], "tags": [{"slug": "a", "name": "A"}]})

        self.assertEqual(GovukChangelogEntry.objects.count(), 1)
        self.assertIn("govuk.delete_govukchangelogentry", self._messages(response))

    def test_a_superuser_still_imports_everything(self):
        """The check has to be invisible to the account that runs a cutover."""
        self.client.force_login(self._superuser())

        self._import(
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
        """The 'tags' list at the top of a file is an edit to the dictionary and
        nothing else, which is what the tag snippet menu is for."""
        self.client.force_login(self._user("nobody"))

        response = self._import({"tags": [{"slug": "invented-tag", "name": "Invented"}]})

        self.assertFalse(GovukTag.objects.filter(slug="invented-tag").exists())
        self.assertIn("govuk.add_govuktag", self._messages(response))

    def test_a_tag_on_a_page_the_account_may_edit_still_arrives(self):
        """Not a regression dressed as a fix.

        The page editor's tag field is free-tagging and taggit asks nothing, so
        anyone who may edit the page may already coin the tag by typing it.
        Requiring add_govuktag here would make an import stricter than the
        editor it is standing in for.
        """
        self.client.force_login(
            self._user(
                "editor",
                page_permissions=[(self.root_page, "add_page"), (self.root_page, "publish_page")],
            )
        )

        self._import(
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
        """A tag that already exists is not being created by anybody, so the
        message would be noise on a file that changed no dictionary."""
        GovukTag.objects.create(slug="already-here", name="Already here")
        self.client.force_login(self._user("nobody"))

        response = self._import({"tags": [{"slug": "already-here", "name": "Already here"}]})

        self.assertNotIn("govuk.add_govuktag", self._messages(response))
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

        response = self._export(page_ids=[str(self.section_page.pk)])

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self._exported_privacy(response, "apply"),
            [{"type": "password", "password": "correct-horse-battery-staple"}],
        )

    def test_the_export_withholds_it_from_someone_who_may_not(self):
        """Wagtail's own privacy dialog is shut to this account. The download
        was the way round it: the whole file, in cleartext, on one POST."""
        self._protect(self.content_page)
        self.client.force_login(
            self._user(
                "editor",
                page_permissions=[(self.root_page, "change_page")],
            )
        )

        response = self._export(page_ids=[str(self.section_page.pk)])

        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertNotIn("correct-horse-battery-staple", body)
        # The restriction is still named, so what the page's privacy is stays
        # legible and the file still says the page is not public.
        self.assertEqual(
            self._exported_privacy(response, "apply"), [{"type": "password"}]
        )



    # -- export and listing: pages this account may not edit ------------------

    def test_a_page_this_account_may_not_edit_is_not_offered_for_export(self):
        self.client.force_login(
            self._user(
                "editor",
                page_permissions=[(self.content_page, "change_page")],
            )
        )

        response = self.client.get(self.index_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Apply")
        self.assertNotContains(response, "Benefits")

    def test_nor_exported_when_its_id_is_asked_for_directly(self):
        self.client.force_login(
            self._user(
                "editor",
                page_permissions=[(self.content_page, "change_page")],
            )
        )

        response = self._export(
            page_ids=[str(self.section_page.pk), str(self.content_page.pk)]
        )

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content.decode())
        self.assertEqual(
            [node["settings"]["slug"] for node in payload["pages"]], ["apply"]
        )

    def test_snippets_are_not_offered_to_an_account_that_may_not_manage_them(self):
        self.client.force_login(
            self._user("nobody", page_permissions=[(self.root_page, "change_page")])
        )

        response = self.client.get(self.index_url)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Data analyst")
        self.assertNotContains(response, "Accessibility")

    def test_nor_exported_when_their_ids_are_asked_for_directly(self):
        self.client.force_login(
            self._user("nobody", page_permissions=[(self.root_page, "change_page")])
        )

        response = self._export(
            page_ids=[str(self.section_page.pk)],
            role_ids=[str(self.role.pk)],
            skill_ids=[str(self.skill.pk)],
        )

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content.decode())
        self.assertEqual(payload["roles"], [])
        self.assertEqual(payload["skills"], [])

@override_settings(FEATURE_FLAGS=_feature_flags())
class ImportedPasswordRestrictionTests(TestCase):
    """A password restriction that arrives without its password.

    Which is what an export taken by someone who may not read passwords now
    carries, so the import has to have an answer that is not "the password is
    the empty string".
    """

    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root_page = self.site.root_page.specific
        self.page = self.root_page.add_child(
            instance=ContentPage(title="Apply", slug="apply", body="<p>Body</p>")
        )
        self.user = get_user_model().objects.create_superuser(
            username="admin-user",
            email="admin@example.gov.uk",
            password="unused-password",
        )
        self.client.force_login(self.user)

    def _import_privacy(self, privacy):
        return self.client.post(
            reverse("govuk_pages_import"),
            {
                "json_file": SimpleUploadedFile(
                    "export.json",
                    json.dumps(
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
                    ).encode(),
                    content_type="application/json",
                ),
                "action": "import",
            },
            follow=True,
        )

    def test_the_password_already_on_the_page_stands(self):
        PageViewRestriction.objects.create(
            page=self.page,
            restriction_type=PageViewRestriction.PASSWORD,
            password="kept",
        )

        self._import_privacy([{"type": "password"}])

        restriction = PageViewRestriction.objects.get(page=self.page)
        self.assertEqual(restriction.password, "kept")

    def test_a_page_with_no_password_of_its_own_is_not_given_an_empty_one(self):
        response = self._import_privacy([{"type": "password"}])

        self.assertFalse(PageViewRestriction.objects.filter(page=self.page).exists())
        self.assertIn(
            "gives no password for it",
            " ".join(str(message) for message in response.context["messages"]),
        )

    def test_a_password_the_file_does_name_is_still_applied(self):
        self._import_privacy([{"type": "password", "password": "from-the-file"}])

        restriction = PageViewRestriction.objects.get(page=self.page)
        self.assertEqual(restriction.password, "from-the-file")
