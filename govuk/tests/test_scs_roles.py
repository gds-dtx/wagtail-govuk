from django.test import TestCase, override_settings
from wagtail.models import Site

from govuk.capability_framework import split_leadership_examples
from govuk.models import GovukRole, GovukSkill, RolePage, SkillsAZPage


def _feature_flags(*, skills_enabled: bool = True) -> dict[str, bool]:
    return {
        "SKILLS": skills_enabled,
        "ORGANISATIONS": False,
        "PEOPLE_FINDER": False,
        "FEEDBACK": False,
    }


@override_settings(FEATURE_FLAGS=_feature_flags())
class SeniorCivilServiceRoleTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root_page = self.site.root_page.specific

        self.strategic_planning = GovukSkill.objects.create(
            title="Strategic technology planning",
            body="<p>You can:</p><ul><li>determine the right technologies</li></ul>",
            is_senior_civil_service=True,
            leadership_points=[
                {"type": "point", "value": "inspiring people with your vision"},
                {"type": "point", "value": "educating colleagues about benefits"},
            ],
        )
        self.capability_building = GovukSkill.objects.create(
            title="Capability building",
            body="<p>You can:</p><ul><li>guide the organisation</li></ul>",
            is_senior_civil_service=True,
            leadership_points=[{"type": "point", "value": "prioritising capability needs"}],
        )
        self.delegated_skill = GovukSkill.objects.create(
            title="Data modelling",
            working_points=[{"type": "point", "value": "produce data models"}],
        )

        self.cto = GovukRole.objects.create(
            title="Chief technology officer",
            family="Chief digital and data",
            body="<p>A chief technology officer is the technology strategist.</p>",
            is_senior_civil_service=True,
            scs_grades=[{"type": "grade", "value": "scs1"}, {"type": "grade", "value": "scs2"}],
            scs_skills=[
                {"type": "skill", "value": self.strategic_planning.pk},
                {"type": "skill", "value": self.capability_building.pk},
            ],
        )

        page = self.root_page.add_child(
            instance=RolePage(
                title="Chief technology officer",
                slug="chief-technology-officer",
                selected_roles=[{"type": "role", "value": self.cto.pk}],
            )
        )
        page.save_revision().publish()
        self.cto_page = page.specific

    def test_scs_role_exposes_its_skills_with_leadership_examples(self):
        skills = self.cto.get_scs_skills()

        self.assertEqual(
            [row["skill"] for row in skills],
            [self.strategic_planning, self.capability_building],
        )
        self.assertEqual(
            skills[0]["leadership_points"],
            ["inspiring people with your vision", "educating colleagues about benefits"],
        )

    def test_scs_role_has_no_levels(self):
        self.assertEqual(self.cto.get_levels_with_skills(), [])

    def test_scs_grades_render_as_labels(self):
        self.assertEqual(
            self.cto.get_scs_grade_labels(),
            ["SCS 1 (Senior Civil Service 1)", "SCS 2 (Senior Civil Service 2)"],
        )

    def test_scs_skills_count_towards_related_roles(self):
        other = GovukRole.objects.create(
            title="Chief data officer",
            is_senior_civil_service=True,
            scs_skills=[{"type": "skill", "value": self.capability_building.pk}],
        )

        self.assertIn(self.capability_building.pk, self.cto.get_skill_ids())
        self.assertEqual(
            [entry["role"] for entry in self.cto.get_related_roles()], [other]
        )

    def test_scs_skill_lists_the_roles_requiring_it(self):
        self.assertEqual(
            self.capability_building.get_roles_requiring_skill(), [self.cto]
        )

    def test_role_page_renders_scs_skills_and_grades(self):
        response = self.client.get(self.cto_page.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Skills for chief technology officer")
        self.assertContains(response, "Strategic technology planning")
        self.assertContains(response, "Examples of leadership using this skill")
        self.assertContains(response, "inspiring people with your vision")
        self.assertContains(response, "SCS 1")
        self.assertContains(response, "SCS 2")
        # SCS roles have no proficiency levels
        self.assertNotContains(response, "Level: Working")

    def test_skills_index_marks_scs_skills_and_omits_the_level_table(self):
        skills_page = self.root_page.add_child(
            instance=SkillsAZPage(title="Skills A to Z", slug="skills")
        )
        skills_page.save_revision().publish()

        response = self.client.get(skills_page.url)
        body = response.content.decode()

        self.assertIn("Senior Civil Service", body)
        self.assertIn("Examples of leadership using this skill", body)
        # the delegated-grade skill keeps its four-level table
        self.assertIn("produce data models", body)

    def test_delegated_role_is_unaffected(self):
        role = GovukRole.objects.create(
            title="Data engineer",
            levels=[
                {
                    "type": "level",
                    "value": {
                        "title": "Data engineer",
                        "description": "",
                        "skills": [
                            {"skill": self.delegated_skill.pk, "level": "working"}
                        ],
                    },
                }
            ],
        )

        self.assertFalse(role.is_senior_civil_service)
        self.assertEqual(role.get_scs_skills(), [])
        self.assertEqual(len(role.get_levels_with_skills()), 1)


class SplitLeadershipExamplesTests(TestCase):
    def test_splits_description_from_examples(self):
        text = (
            "You can:\n"
            "- guide the organisation\n"
            "Examples of leadership using this skill:\n"
            "- prioritising capability needs\n"
            "- growing communities"
        )
        description, examples = split_leadership_examples(text)

        self.assertEqual(description, "You can:\n- guide the organisation")
        self.assertEqual(
            examples, ["prioritising capability needs", "growing communities"]
        )

    def test_text_without_examples_is_returned_unchanged(self):
        description, examples = split_leadership_examples("You can:\n- do a thing")

        self.assertEqual(description, "You can:\n- do a thing")
        self.assertEqual(examples, [])

    def test_empty_text(self):
        self.assertEqual(split_leadership_examples(""), ("", []))
