from django.test import TestCase, override_settings
from wagtail.models import Site

from govuk.models import GovukRole, GovukSkill, RolePage, SkillsAZPage


def _feature_flags(*, skills_enabled: bool) -> dict[str, bool]:
    return {
        "SKILLS": skills_enabled,
        "ORGANISATIONS": False,
        "PEOPLE_FINDER": False,
        "FEEDBACK": False,
    }


@override_settings(FEATURE_FLAGS=_feature_flags(skills_enabled=True))
class SkillsAZPageTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root_page = self.site.root_page.specific

        self.beta_skill = GovukSkill.objects.create(
            title="Beta skill",
            body="<p>Beta body.</p>",
            awareness_points=[
                {"type": "point", "value": "Beta awareness point."},
            ],
        )
        self.alpha_skill = GovukSkill.objects.create(
            title="Alpha skill",
            body="<p>Alpha body.</p>",
            awareness_points=[
                {"type": "point", "value": "Alpha awareness point."},
            ],
        )
        self.charlie_skill = GovukSkill.objects.create(
            title="Charlie skill",
            body="<p>Charlie body.</p>",
            awareness_points=[
                {"type": "point", "value": "Charlie awareness point."},
            ],
        )

        skills_page = self.root_page.add_child(
            instance=SkillsAZPage(
                title="Skills A-Z",
                slug="skills-az",
                body="<p>Skill definitions in alphabetical order.</p>",
            )
        )
        skills_page.save_revision().publish()
        self.skills_page = skills_page.specific

    def test_get_skill_sections_returns_skills_in_alphabetical_order(self):
        skill_sections = self.skills_page.get_skill_sections()
        skill_titles = [section["skill"].title for section in skill_sections]

        self.assertEqual(
            skill_titles,
            ["Alpha skill", "Beta skill", "Charlie skill"],
        )

    def test_skills_az_page_renders_govuk_accordion_and_sorted_skill_headings(self):
        response = self.client.get(self.skills_page.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="govuk-accordion"')
        self.assertContains(response, "Skill level")
        self.assertContains(response, "Description")

        response_text = response.content.decode("utf-8")
        alpha_index = response_text.find("Alpha skill")
        beta_index = response_text.find("Beta skill")
        charlie_index = response_text.find("Charlie skill")
        self.assertTrue(alpha_index < beta_index < charlie_index)

    def test_the_skills_index_carries_the_frameworks_side_navigation(self):
        """It sits alongside the roles, so it is navigated the same way."""
        role = GovukRole.objects.create(title="Data analyst", family="Data")
        role_page = self.root_page.add_child(
            instance=RolePage(
                title="Data analyst",
                slug="data-analyst",
                selected_roles=[{"type": "role", "value": role.pk}],
            )
        )
        role_page.save_revision().publish()

        response = self.client.get(self.skills_page.url)

        self.assertContains(response, 'aria-label="Data roles"')
        self.assertContains(response, "role-nav__item--active")
        # The heading moves beside the navigation rather than staying in the
        # site hero above it.
        self.assertNotContains(response, "hero__title")
        self.assertContains(
            response, '<h1 class="govuk-heading-xl">Skills A-Z</h1>', html=True
        )
        self.assertContains(response, "govuk-breadcrumbs--mobile-only")

    @override_settings(FEATURE_FLAGS=_feature_flags(skills_enabled=False))
    def test_skills_az_page_is_not_creatable_when_skills_feature_is_disabled(self):
        self.assertFalse(SkillsAZPage.can_create_at(self.root_page))
