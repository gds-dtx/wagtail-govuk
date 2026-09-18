from django.test import TestCase, override_settings
from wagtail.models import Site

from govuk.models import FrameworkSkillsPage, GovukRole, GovukSkill
from govuk.tests.framework_helpers import make_framework_main_page, role_url


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
class SkillRolesTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root_page = self.site.root_page.specific

        self.modelling = GovukSkill.objects.create(title="Data modelling")
        self.unused_skill = GovukSkill.objects.create(title="Unused skill")

        self.data_engineer = GovukRole.objects.create(
            title="Data engineer",
            levels=[
                _level("Data engineer", [(self.modelling, "working")]),
                # a second level requiring the same skill must not duplicate the role
                _level("Senior data engineer", [(self.modelling, "practitioner")]),
            ],
        )
        self.data_architect = GovukRole.objects.create(
            title="Data architect",
            levels=[_level("Data architect", [(self.modelling, "expert")])],
        )

        self.main_page = make_framework_main_page(self.root_page)
        # Every role is now served on a route under the framework main page.
        self.engineer_url = role_url(self.main_page, self.data_engineer)
        self.architect_url = role_url(self.main_page, self.data_architect)

        skills_page = self.root_page.add_child(
            instance=FrameworkSkillsPage(title="Skills A to Z", slug="skills")
        )
        skills_page.save_revision().publish()
        self.skills_page = skills_page.specific

    def test_roles_requiring_skill_sorted_by_title_without_duplicates(self):
        self.assertEqual(
            self.modelling.get_roles_requiring_skill(),
            [self.data_architect, self.data_engineer],
        )

    def test_skill_with_no_roles_returns_empty_list(self):
        self.assertEqual(self.unused_skill.get_roles_requiring_skill(), [])

    def test_roles_by_skill_id_indexes_every_skill(self):
        index = GovukRole.roles_by_skill_id()

        self.assertEqual(
            index[self.modelling.pk], [self.data_architect, self.data_engineer]
        )
        self.assertNotIn(self.unused_skill.pk, index)

    def test_skills_page_lists_roles_and_links_to_role_pages(self):
        response = self.client.get(self.skills_page.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Roles that require this skill")
        # Every role now has a route under the framework main page, so both
        # roles requiring the skill link to their own page.
        self.assertContains(response, self.engineer_url)
        self.assertContains(response, self.architect_url)
        self.assertContains(response, "Data architect")

    def test_skills_page_omits_heading_for_skills_without_roles(self):
        GovukRole.objects.all().delete()

        response = self.client.get(self.skills_page.url)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Roles that require this skill")
