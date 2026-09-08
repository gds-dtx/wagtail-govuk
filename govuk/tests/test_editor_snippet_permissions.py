"""Can somebody in the Editors group actually edit the framework?

Roles, skills and changelog entries are snippets. Wagtail's initial data gives
Editors and Moderators access to the admin, images and documents, and nothing
else -- so before migration 0065 an editor signed in, saw the page tree, and
could not open a single role. It went unnoticed on dev because five of the
eight accounts there are superusers, and a superuser passes every check.

The permission rows are asserted directly and then exercised through the admin,
because a granted permission that the snippet viewset does not consult would
still leave the editor locked out.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from wagtail.test.utils.form_data import nested_form_data, rich_text, streamfield

from govuk.models import GovukRole, GovukSkill

SNIPPETS = [
    "govukrole",
    "govukskill",
    "govukchangelogentry",
    "govuktag",
    "externalcontentitem",
]


def _codenames(group_name):
    return set(
        Group.objects.get(name=group_name).permissions.values_list(
            "codename", flat=True
        )
    )


class EditorSnippetPermissionsTests(TestCase):
    """The permission rows migration 0065 grants."""

    def test_editors_can_add_and_change_every_framework_snippet(self):
        granted = _codenames("Editors")
        for model in SNIPPETS:
            self.assertIn(f"add_{model}", granted)
            self.assertIn(f"change_{model}", granted)

    def test_editors_cannot_delete_snippets(self):
        """A deleted role takes its references out of every page that used it."""
        granted = _codenames("Editors")
        for model in SNIPPETS:
            self.assertNotIn(f"delete_{model}", granted)

    def test_editors_cannot_reword_the_whole_site(self):
        self.assertNotIn(
            "change_capabilityframeworkwordingsettings", _codenames("Editors")
        )

    def test_moderators_can_delete_and_reword(self):
        granted = _codenames("Moderators")
        for model in SNIPPETS:
            self.assertIn(f"delete_{model}", granted)
        self.assertIn("change_capabilityframeworkwordingsettings", granted)

    def test_both_groups_can_read_feedback_but_not_write_it(self):
        for group_name in ("Editors", "Moderators"):
            granted = _codenames(group_name)
            self.assertIn("view_feedback", granted)
            self.assertNotIn("change_feedback", granted)
            self.assertNotIn("add_feedback", granted)


class EditorSnippetAdminAccessTests(TestCase):
    """The same permissions, exercised the way an editor would."""

    def setUp(self):
        self.editor = get_user_model().objects.create_user(
            username="editorprobe",
            email="editorprobe@example.gov.uk",
            password="probe-pass-123",
            is_superuser=False,
        )
        self.editor.groups.add(Group.objects.get(name="Editors"))
        self.role = GovukRole.objects.create(
            slug="probe-role",
            title="Probe role",
            body="What this role does.",
            family="Data",
        )
        self.client.force_login(self.editor)

    def test_editor_can_list_roles(self):
        response = self.client.get(reverse("wagtailsnippets_govuk_govukrole:list"))
        self.assertEqual(response.status_code, 200)

    def test_editor_can_open_and_save_a_role(self):
        url = reverse("wagtailsnippets_govuk_govukrole:edit", args=[self.role.pk])
        self.assertEqual(self.client.get(url).status_code, 200)

        response = self.client.post(
            url,
            nested_form_data(
                {
                    "slug": "probe-role",
                    "title": "Probe role, edited",
                    "family": "Data",
                    "body": rich_text("What this role does."),
                    "levels": streamfield([]),
                    "is_senior_civil_service": "",
                    "scs_grades": streamfield([]),
                    "scs_skills": streamfield([]),
                    "roles_that_could_lead_here": streamfield([]),
                }
            ),
        )
        self.assertEqual(response.status_code, 302, self._form_errors(response))
        self.role.refresh_from_db()
        self.assertEqual(self.role.title, "Probe role, edited")

    @staticmethod
    def _form_errors(response):
        """Whatever the admin refused, in the assertion message."""
        form = getattr(response, "context_data", {}).get("form")
        return getattr(form, "errors", "no form errors")

    def test_editor_can_reach_skills_and_changelog(self):
        GovukSkill.objects.create(
            slug="probe-skill", title="Probe skill", body="What this skill is."
        )
        for viewset in ("govukskill", "govukchangelogentry", "govuktag"):
            with self.subTest(viewset=viewset):
                response = self.client.get(
                    reverse(f"wagtailsnippets_govuk_{viewset}:list")
                )
                self.assertEqual(response.status_code, 200)

    def test_editor_is_refused_the_delete_view(self):
        url = reverse("wagtailsnippets_govuk_govukrole:delete", args=[self.role.pk])
        response = self.client.get(url)
        self.assertNotEqual(response.status_code, 200)
        self.assertTrue(GovukRole.objects.filter(pk=self.role.pk).exists())
