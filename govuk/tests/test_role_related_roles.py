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


def _level(title: str, skills: list[tuple[GovukSkill, str]]) -> dict:
    return {
        "type": "level",
        "value": {
            "title": title,
            "description": "",
            "skills": [
                {"skill": skill.pk, "level": level} for skill, level in skills
            ],
        },
    }


@override_settings(FEATURE_FLAGS=_feature_flags(skills_enabled=True))
class RelatedRolesTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root_page = self.site.root_page.specific

        self.modelling = GovukSkill.objects.create(title="Data modelling")
        self.communicating = GovukSkill.objects.create(title="Communicating")
        self.problem_management = GovukSkill.objects.create(title="Problem management")

        self.data_engineer = GovukRole.objects.create(
            title="Data engineer",
            levels=[
                _level(
                    "Data engineer",
                    [(self.modelling, "working"), (self.communicating, "awareness")],
                ),
                _level(
                    "Senior data engineer",
                    [
                        (self.modelling, "practitioner"),
                        (self.problem_management, "working"),
                    ],
                ),
            ],
        )
        # shares two skills with data engineer
        self.data_architect = GovukRole.objects.create(
            title="Data architect",
            levels=[
                _level(
                    "Data architect",
                    [(self.modelling, "expert"), (self.communicating, "working")],
                )
            ],
        )
        # shares one skill
        self.incident_manager = GovukRole.objects.create(
            title="Incident manager",
            levels=[_level("Incident manager", [(self.problem_management, "expert")])],
        )
        # shares no skills
        self.unrelated_role = GovukRole.objects.create(
            title="Content designer",
            levels=[],
        )

        role_page = self.root_page.add_child(
            instance=RolePage(
                title="Data engineer",
                slug="data-engineer",
                selected_roles=[{"type": "role", "value": self.data_engineer.pk}],
            )
        )
        role_page.save_revision().publish()
        self.role_page = role_page.specific

        architect_page = self.root_page.add_child(
            instance=RolePage(
                title="Data architect",
                slug="data-architect",
                selected_roles=[{"type": "role", "value": self.data_architect.pk}],
            )
        )
        architect_page.save_revision().publish()
        self.architect_page = architect_page.specific

    def test_get_skill_ids_returns_distinct_skills_across_levels(self):
        self.assertEqual(
            self.data_engineer.get_skill_ids(),
            {self.modelling.pk, self.communicating.pk, self.problem_management.pk},
        )

    def test_related_roles_ordered_by_shared_count_then_title(self):
        related = self.data_engineer.get_related_roles()

        self.assertEqual(
            [entry["role"] for entry in related],
            [self.data_architect, self.incident_manager],
        )
        self.assertEqual(
            [skill.title for skill in related[0]["shared_skills"]],
            ["Communicating", "Data modelling"],
        )
        self.assertEqual(
            [skill.title for skill in related[1]["shared_skills"]],
            ["Problem management"],
        )

    def test_related_roles_excludes_roles_without_shared_skills(self):
        related_roles = [
            entry["role"] for entry in self.data_engineer.get_related_roles()
        ]
        self.assertNotIn(self.unrelated_role, related_roles)

    def test_related_roles_respects_count_cap(self):
        related = self.data_engineer.get_related_roles(count=1)
        self.assertEqual([entry["role"] for entry in related], [self.data_architect])

    def test_role_without_skills_has_no_related_roles(self):
        self.assertEqual(self.unrelated_role.get_related_roles(), [])

    def test_role_page_renders_related_roles_with_link_to_role_page(self):
        response = self.client.get(self.role_page.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Roles that share data engineer skills")
        self.assertContains(response, self.architect_page.url)
        # incident manager has no page, so it renders as plain text
        self.assertContains(response, "Incident manager")

    def test_related_role_heading_preserves_acronyms(self):
        self.assertEqual(
            RolePage._display_role_name("IT service manager"),
            "IT service manager",
        )
        self.assertEqual(
            RolePage._display_role_name("Data engineer"),
            "data engineer",
        )

    def test_shared_skills_link_to_skills_index_when_present(self):
        skills_page = self.root_page.add_child(
            instance=SkillsAZPage(title="Skills A to Z", slug="skills")
        )
        skills_page.save_revision().publish()

        response = self.client.get(self.role_page.url)
        self.assertContains(
            response, f"{skills_page.url}#{self.modelling.slug}"
        )
