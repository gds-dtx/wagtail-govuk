from django.test import TestCase, override_settings
from wagtail.models import Site

from govuk.models import ContentPage, FrameworkMainPage, GovukRole, GovukSkill, GovukTag
from govuk.tests.framework_helpers import make_framework_main_page, role_url


def _feature_flags(*, skills_enabled: bool) -> dict[str, bool]:
    return {
        "SKILLS": skills_enabled,
        "ORGANISATIONS": False,
        "PEOPLE_FINDER": False,
        "FEEDBACK": False,
    }


@override_settings(FEATURE_FLAGS=_feature_flags(skills_enabled=True))
class RolePageTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root_page = self.site.root_page.specific

        self.forensics = GovukSkill.objects.create(
            title="Forensics",
            body="<p>Forensics refers to the capture, analysis and reporting of evidence.</p>",
            working_points=[
                {
                    "type": "point",
                    "value": "Analyses digital evidence and investigates incidents.",
                },
                {
                    "type": "point",
                    "value": "Undertakes real-time analysis on live systems.",
                },
            ],
            practitioner_points=[
                {
                    "type": "point",
                    "value": "Leads forensic investigations and advises wider teams.",
                }
            ],
        )
        self.protective_security = GovukSkill.objects.create(
            title="Protective security",
            body="<p>Protective security reduces risk across people, places and systems.</p>",
            awareness_points=[
                {
                    "type": "point",
                    "value": "Maintains an understanding of security fundamentals.",
                }
            ],
        )

        self.digital_forensics_role = GovukRole.objects.create(
            title="Digital forensics analyst",
            body="<p>A digital forensics analyst acquires and analyses forensic evidence.</p>",
            levels=[
                {
                    "type": "level",
                    "value": {
                        "title": "Associate digital forensics analyst",
                        "description": (
                            "<p>An associate supports digital aspects of investigations.</p>"
                        ),
                        "skills": [
                            {
                                "skill": self.protective_security.pk,
                                "level": "awareness",
                            },
                            {"skill": self.forensics.pk, "level": "working"},
                        ],
                    },
                },
                {
                    "type": "level",
                    "value": {
                        "title": "Lead digital forensics analyst",
                        "description": (
                            "<p>A lead drives complex forensic investigations.</p>"
                        ),
                        "skills": [
                            {"skill": self.forensics.pk, "level": "practitioner"},
                        ],
                    },
                },
            ],
        )

        self.main_page = make_framework_main_page(self.root_page)
        self.role_url = role_url(self.main_page, self.digital_forensics_role)

    def test_skill_points_for_level_accepts_choice_value_or_label(self):
        self.assertEqual(
            self.forensics.points_for_level("working"),
            [
                "Analyses digital evidence and investigates incidents.",
                "Undertakes real-time analysis on live systems.",
            ],
        )
        self.assertEqual(
            self.forensics.points_for_level("Working"),
            [
                "Analyses digital evidence and investigates incidents.",
                "Undertakes real-time analysis on live systems.",
            ],
        )

    def test_role_get_levels_with_skills_returns_skill_rows(self):
        role_levels = self.digital_forensics_role.get_levels_with_skills()

        self.assertEqual(len(role_levels), 2)
        self.assertEqual(role_levels[0]["title"], "Associate digital forensics analyst")
        self.assertEqual(role_levels[0]["skills"][0]["skill"], self.forensics)
        self.assertEqual(
            role_levels[0]["skills"][1]["skill"],
            self.protective_security,
        )
        self.assertEqual(role_levels[0]["skills"][0]["required_level"], "working")
        self.assertEqual(role_levels[0]["skills"][0]["required_level_label"], "Working")
        self.assertIn(
            "Analyses digital evidence and investigates incidents.",
            role_levels[0]["skills"][0]["points"],
        )

    def test_role_page_renders_selected_roles_levels_and_skill_points(self):
        response = self.client.get(self.role_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "digital forensics analyst")
        self.assertContains(response, "Associate digital forensics analyst")
        self.assertContains(response, "Lead digital forensics analyst")
        self.assertContains(response, "Forensics")
        self.assertContains(response, "Protective security")
        self.assertContains(response, "Level: Working")
        self.assertContains(response, "Level: Awareness")
        self.assertContains(
            response,
            "Analyses digital evidence and investigates incidents.",
        )

    def test_skill_points_are_introduced_by_the_framework_wording(self):
        """The points are written as the ends of "You can:", so it is printed."""
        response = self.client.get(self.role_url)

        self.assertContains(response, "You can:")

    def test_the_sidebar_home_link_names_the_framework_and_is_not_current(self):
        """On a role's page the top link still names the framework and points at
        it, but the role is the current thing, not the home."""
        response = self.client.get(self.role_url)

        home = response.context["framework_home"]
        self.assertEqual(home["title"], "Home")
        self.assertEqual(home["url"], self.main_page.url)
        self.assertFalse(home["is_current"])

    def test_a_delegated_grade_role_has_none_of_the_senior_civil_service_wording(self):
        response = self.client.get(self.role_url)

        self.assertNotContains(response, "A specific digital forensics analyst job")
        self.assertNotContains(response, "will need to use digital and data skills to")

    def test_framework_main_page_supports_tags(self):
        policy_tag = GovukTag.objects.create(slug="policy", name="Policy")
        security_tag = GovukTag.objects.create(slug="security", name="Security")

        self.main_page.tags.set([security_tag, policy_tag])

        self.assertEqual(
            list(self.main_page.tags.order_by("slug").values_list("slug", flat=True)),
            ["policy", "security"],
        )

    @override_settings(FEATURE_FLAGS=_feature_flags(skills_enabled=False))
    def test_framework_main_page_is_not_creatable_when_skills_feature_is_disabled(self):
        self.assertFalse(FrameworkMainPage.can_create_at(self.root_page))

    @override_settings(FEATURE_FLAGS=_feature_flags(skills_enabled=False))
    def test_a_role_route_does_not_serve_without_the_framework(self):
        """Not creatable is not the same as not public.

        A route cannot be guarded by ``can_exist_under``, and the page import
        does not go through the admin, so a Capability Framework export applied
        to another site could leave a framework main page routing roles on it.
        The route 404s without the framework flag, as the role pages did.
        """
        response = self.client.get(self.role_url)

        self.assertEqual(response.status_code, 404)


@override_settings(FEATURE_FLAGS=_feature_flags(skills_enabled=True))
class FrameworkMainPagePlacementTests(TestCase):
    """The framework main page can sit anywhere, but only one per site."""

    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root_page = self.site.root_page.specific

    def test_it_can_be_created_below_another_page(self):
        content = self.root_page.add_child(
            instance=ContentPage(title="Guidance", slug="guidance")
        )
        content.save_revision().publish()

        self.assertTrue(FrameworkMainPage.can_create_at(content))

    def test_only_one_is_allowed_per_site(self):
        make_framework_main_page(self.root_page)

        # max_count reached, so no second one anywhere.
        content = self.root_page.add_child(
            instance=ContentPage(title="Guidance", slug="guidance")
        )
        content.save_revision().publish()

        self.assertFalse(FrameworkMainPage.can_create_at(self.root_page))
        self.assertFalse(FrameworkMainPage.can_create_at(content))
