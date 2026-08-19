"""What Import / Export lets an account do that the rest of the admin does not.

Every view here is behind ``require_admin_access`` and nothing else, so the
only permission any of it asked for was the one that opens the admin at all.
The pages in a file were checked one by one; nothing else was. An account with
no permission beyond access to the admin could rewrite every skill and role in
the framework by uploading a file.
"""

import json

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from wagtail.models import (
    GroupPagePermission,
    Site,
)

from govuk.models import (
    ContentPage,
    GovukChangelogEntry,
    GovukRole,
    GovukSkill,
    SectionPage,
)


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
