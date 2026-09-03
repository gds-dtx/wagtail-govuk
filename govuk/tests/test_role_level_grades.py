from django.test import TestCase, override_settings
from wagtail.models import Site

from govuk.models import GovukRole, GovukSkill, RolePage


def _feature_flags(*, skills_enabled: bool = True) -> dict[str, bool]:
    return {
        "SKILLS": skills_enabled,
        "ORGANISATIONS": False,
        "PEOPLE_FINDER": False,
        "FEEDBACK": False,
    }


@override_settings(FEATURE_FLAGS=_feature_flags())
class RoleLevelGradeTests(TestCase):
    """Indicative Civil Service job grades shown against each role level."""

    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root_page = self.site.root_page.specific

        self.skill = GovukSkill.objects.create(
            title="Data modelling",
            working_points=[{"type": "point", "value": "produce data models"}],
        )
        self.role = GovukRole.objects.create(
            title="Data analyst",
            family="Data",
            levels=[
                {
                    "type": "level",
                    "value": {
                        "title": "Associate data analyst",
                        "description": "",
                        "grades": ["eo", "heo"],
                        "skills": [{"skill": self.skill.pk, "level": "working"}],
                    },
                },
                {
                    "type": "level",
                    "value": {
                        "title": "Lead data analyst - management",
                        "description": "",
                        "grades": [],
                        "skills": [],
                    },
                },
            ],
        )

        page = self.root_page.add_child(
            instance=RolePage(
                title="Data analyst",
                slug="data-analyst",
                selected_roles=[{"type": "role", "value": self.role.pk}],
            )
        )
        page.save_revision().publish()
        self.role_page = page.specific

    def test_level_grades_render_as_labels(self):
        levels = self.role.get_levels_with_skills()

        self.assertEqual(
            levels[0]["grades"],
            ["EO (Executive Officer)", "HEO (Higher Executive Officer)"],
        )

    def test_level_grades_are_ordered_by_seniority_not_authoring_order(self):
        self.role.levels = [
            {
                "type": "level",
                "value": {
                    "title": "Associate data analyst",
                    "description": "",
                    "grades": ["heo", "eo"],
                    "skills": [],
                },
            }
        ]
        self.role.save()

        self.assertEqual(
            self.role.get_levels_with_skills()[0]["grades"],
            ["EO (Executive Officer)", "HEO (Higher Executive Officer)"],
        )

    def test_level_with_no_grades_is_empty(self):
        self.assertEqual(self.role.get_levels_with_skills()[1]["grades"], [])

    def test_role_page_renders_the_grade_sentence_for_graded_levels_only(self):
        response = self.client.get(self.role_page.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "EO (Executive Officer)")
        self.assertContains(response, "HEO (Higher Executive Officer)")
        # One sentence, for the single level that has grades.
        self.assertContains(
            response,
            "This role level is most often performed at the",
            count=1,
        )
