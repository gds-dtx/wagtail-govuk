"""The live service's /skill/<slug> addresses resolve to the A to Z.

These are the addresses every inbound link to a skill arrives on, because the
old domain redirects path for path. Until 24 September 2026 a redirect row per
skill was seeded by the content import; when that was removed, all 185 skill
addresses answered 404 on production. The view under test answers them again.
"""

from django.test import TestCase, override_settings
from wagtail.models import Site

from govuk.models import FrameworkSkillsPage, GovukSkill


def _feature_flags(*, skills_enabled: bool) -> dict[str, bool]:
    return {
        "SKILLS": skills_enabled,
        "ORGANISATIONS": False,
        "PEOPLE_FINDER": False,
        "FEEDBACK": False,
    }


@override_settings(FEATURE_FLAGS=_feature_flags(skills_enabled=True))
class LegacySkillUrlTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root_page = self.site.root_page.specific
        self.skill = GovukSkill.objects.create(
            title="Accessibility",
            body="<p>Accessibility body.</p>",
            awareness_points=[{"type": "point", "value": "An awareness point."}],
        )
        skills_page = self.root_page.add_child(
            instance=FrameworkSkillsPage(
                title="Skills A to Z",
                slug="skills",
                body="<p>Skill definitions in alphabetical order.</p>",
            )
        )
        skills_page.save_revision().publish()
        self.skills_page = skills_page.specific

    def _expected_target(self) -> str:
        return f"{self.skills_page.url}#accessibility"

    def test_live_skill_address_with_slash_redirects_to_its_anchor(self):
        response = self.client.get("/skill/accessibility/")

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], self._expected_target())

    def test_live_skill_address_without_slash_redirects_in_one_hop(self):
        response = self.client.get("/skill/accessibility")

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], self._expected_target())

    def test_head_is_answered_the_same_way(self):
        response = self.client.head("/skill/accessibility/")

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], self._expected_target())

    def test_slug_case_does_not_matter_but_the_anchor_is_the_stored_slug(self):
        response = self.client.get("/skill/Accessibility/")

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], self._expected_target())

    def test_anchor_is_present_on_the_a_to_z_it_points_at(self):
        response = self.client.get(self.skills_page.url)

        self.assertContains(response, 'id="accessibility"')

    def test_unknown_skill_is_a_404_not_a_redirect(self):
        response = self.client.get("/skill/not-a-skill/")

        self.assertEqual(response.status_code, 404)

    def test_unpublished_skill_is_not_redirected_to(self):
        self.skill.live = False
        self.skill.save()

        response = self.client.get("/skill/accessibility/")

        self.assertEqual(response.status_code, 404)

    def test_no_skills_page_means_404(self):
        self.skills_page.delete()

        response = self.client.get("/skill/accessibility/")

        self.assertEqual(response.status_code, 404)

    def test_is_cheap(self):
        # One request first, so the per-site settings rows the middleware
        # creates on a cold database are not counted against the view.
        self.client.get("/skill/accessibility/")

        with self.assertNumQueries(4):  # site, maintenance setting, skill, A to Z
            self.client.get("/skill/accessibility/")


@override_settings(FEATURE_FLAGS=_feature_flags(skills_enabled=False))
class LegacySkillUrlWithoutFrameworkTests(TestCase):
    def test_site_without_the_framework_falls_through_to_wagtail(self):
        GovukSkill.objects.create(
            title="Accessibility",
            body="<p>Accessibility body.</p>",
            awareness_points=[{"type": "point", "value": "An awareness point."}],
        )

        response = self.client.get("/skill/accessibility/")

        self.assertEqual(response.status_code, 404)
