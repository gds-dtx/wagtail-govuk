"""Roles and skills carry Wagtail's editorial workflow.

They are DraftState / Revision / Workflow / Lockable snippets, so an edit is a
draft until it is published, the default "Moderators approval" workflow is bound
to both content types (migration 0081), and only published rows are served to
the public. Content created directly -- as the CSV import does -- stays live, so
existing behaviour is unchanged.
"""

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase, override_settings
from wagtail.models import Site

from govuk.models import FrameworkSkillsPage, GovukRole, GovukSkill
from govuk.search_backend import search_backend
from govuk.tests.framework_helpers import make_framework_main_page, role_url


def _feature_flags(*, skills_enabled: bool) -> dict[str, bool]:
    return {
        "SKILLS": skills_enabled,
        "ORGANISATIONS": False,
        "PEOPLE_FINDER": False,
        "FEEDBACK": False,
    }


@override_settings(FEATURE_FLAGS=_feature_flags(skills_enabled=True))
class RoleSkillWorkflowTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root_page = self.site.root_page.specific
        self.main_page = make_framework_main_page(self.root_page)
        skills_page = self.root_page.add_child(
            instance=FrameworkSkillsPage(title="Skills A-Z", slug="skills-az")
        )
        skills_page.save_revision().publish()
        self.skills_page = skills_page.specific

    def test_the_default_workflow_is_assigned_to_roles_and_skills(self):
        """Migration 0081 binds the default workflow to both content types, so
        the review/approval step is available without any admin setup."""
        role_workflow = GovukRole().get_workflow()
        skill_workflow = GovukSkill().get_workflow()

        self.assertIsNotNone(role_workflow)
        self.assertIsNotNone(skill_workflow)
        self.assertEqual(role_workflow.name, "Moderators approval")
        self.assertEqual(skill_workflow.name, "Moderators approval")

    def test_a_draft_role_is_not_served_until_it_is_published(self):
        role = GovukRole(slug="draft-role", title="Draft role", live=False)
        role.save()

        self.assertEqual(
            self.client.get(role_url(self.main_page, role)).status_code, 404
        )

        role.save_revision().publish()
        role.refresh_from_db()
        self.assertTrue(role.live)

        response = self.client.get(role_url(self.main_page, role))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Draft role")

    def test_a_draft_skill_is_absent_from_the_a_to_z_until_published(self):
        skill = GovukSkill(slug="draft-skill", title="Draft skill", live=False)
        skill.save()

        titles = [
            section["skill"].title for section in self.skills_page.get_skill_sections()
        ]
        self.assertNotIn("Draft skill", titles)

        skill.save_revision().publish()

        titles = [
            section["skill"].title for section in self.skills_page.get_skill_sections()
        ]
        self.assertIn("Draft skill", titles)

    def test_directly_created_content_is_live_and_served(self):
        """The CSV import writes rows directly; they must publish on creation so
        an import produces a working site, exactly as it did before workflow."""
        role = GovukRole.objects.create(slug="imported-role", title="Imported role")

        self.assertTrue(role.live)
        response = self.client.get(role_url(self.main_page, role))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Imported role")

    def test_a_role_previews_through_its_page_template(self):
        role = GovukRole.objects.create(slug="preview-role", title="Preview role")
        request = RequestFactory().get("/")

        self.assertEqual(
            role.get_preview_template(request, role.default_preview_mode),
            "govuk/role_page.html",
        )
        context = role.get_preview_context(request, role.default_preview_mode)
        self.assertEqual(context["page_heading"], "Preview role")

    def test_a_role_can_enter_its_review_workflow(self):
        moderator = get_user_model().objects.create_superuser(
            username="mod", email="mod@example.gov.uk", password="unused-password"
        )
        role = GovukRole.objects.create(slug="review-role", title="Review role")
        role.save_revision()

        role.get_workflow().start(role, moderator)

        state = role.current_workflow_state
        self.assertIsNotNone(state)
        self.assertEqual(state.status, "in_progress")

    def _search_titles(self, query):
        page = search_backend.search(query, filters={"site": self.site}, page=1)
        return {item.title for item in page.object_list}

    def test_a_draft_role_and_skill_are_not_searchable_until_published(self):
        role = GovukRole(slug="hidden-role", title="Hiddenrole", live=False)
        role.save()
        skill = GovukSkill(slug="hidden-skill", title="Hiddenskill", live=False)
        skill.save()

        self.assertNotIn("Hiddenrole", self._search_titles("Hiddenrole"))
        self.assertNotIn("Hiddenskill", self._search_titles("Hiddenskill"))

        role.save_revision().publish()
        skill.save_revision().publish()

        self.assertIn("Hiddenrole", self._search_titles("Hiddenrole"))
        self.assertIn("Hiddenskill", self._search_titles("Hiddenskill"))
