from datetime import date

from django.test import TestCase, override_settings
from wagtail.models import Site

from govuk.models import (
    ContentPage,
    GovukChangelogEntry,
    GovukRole,
    GovukSkill,
    RolePage,
    SkillsAZPage,
    further_resources_group,
    role_navigation_groups,
)


def _feature_flags(*, skills_enabled: bool) -> dict[str, bool]:
    return {
        "SKILLS": skills_enabled,
        "ORGANISATIONS": False,
        "PEOPLE_FINDER": False,
        "FEEDBACK": False,
    }


@override_settings(FEATURE_FLAGS=_feature_flags(skills_enabled=True))
class RolePageLayoutTests(TestCase):
    """The role page follows the DDaT Capability Framework's layout."""

    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root_page = self.site.root_page.specific

        self.data_visualisation = GovukSkill.objects.create(
            title="Data visualisation",
            working_points=[{"type": "point", "value": "Presents data clearly."}],
        )

        self.data_analyst = GovukRole.objects.create(
            title="Data analyst",
            family="Data",
            body="<p>A data analyst collects, manages and shares data.</p>",
            levels=[
                {
                    "type": "level",
                    "value": {
                        "title": "Associate data analyst",
                        "description": "<p>An associate supports data analysis.</p>",
                        "skills": [
                            {"skill": self.data_visualisation.pk, "level": "working"}
                        ],
                    },
                },
                {
                    "type": "level",
                    "value": {
                        "title": "Senior data analyst",
                        "description": "<p>A senior leads data analysis.</p>",
                        "skills": [
                            {
                                "skill": self.data_visualisation.pk,
                                "level": "practitioner",
                            }
                        ],
                    },
                },
            ],
        )
        self.business_architect = GovukRole.objects.create(
            title="Business architect",
            family="Architecture",
            body="<p>A business architect designs business capability.</p>",
            levels=[
                {
                    "type": "level",
                    "value": {
                        "title": "Lead business architect",
                        "skills": [
                            {"skill": self.data_visualisation.pk, "level": "expert"}
                        ],
                    },
                }
            ],
        )

        GovukChangelogEntry.objects.create(
            date=date(2020, 1, 7),
            role=self.data_analyst,
            note="<p>First published.</p>",
        )
        GovukChangelogEntry.objects.create(
            date=date(2025, 8, 29),
            role=self.data_analyst,
            note="<p>The data analyst role has been updated.</p>",
        )

        self.data_analyst_page = self._add_role_page(
            title="Data analyst", slug="data-analyst", role=self.data_analyst
        )
        self.business_architect_page = self._add_role_page(
            title="Business architect",
            slug="business-architect",
            role=self.business_architect,
        )

    def _add_role_page(self, *, title: str, slug: str, role: GovukRole) -> RolePage:
        page = self.root_page.add_child(
            instance=RolePage(
                title=title,
                slug=slug,
                selected_roles=[{"type": "role", "value": role.pk}],
            )
        )
        page.save_revision().publish()
        return page.specific

    def test_headings_carry_the_frameworks_anchors(self):
        response = self.client.get(self.data_analyst_page.url)

        self.assertContains(
            response,
            '<h2 class="govuk-heading-l" id="what-a-data-analyst-does">'
            "What a data analyst does</h2>",
            html=True,
        )
        self.assertContains(
            response,
            '<h2 class="govuk-heading-l" id="role-levels">Data analyst role levels</h2>',
            html=True,
        )
        self.assertContains(
            response,
            '<h3 class="govuk-heading-m role-level-header" id="associate-data-analyst">'
            "1. Associate data analyst</h3>",
            html=True,
        )
        self.assertContains(
            response,
            '<h2 class="govuk-heading-m" id="update-history">Updates</h2>',
            html=True,
        )

    def test_the_title_uses_the_standard_heading_rather_than_the_site_hero(self):
        response = self.client.get(self.data_analyst_page.url)

        self.assertContains(
            response, '<h1 class="govuk-heading-xl">Data analyst</h1>', html=True
        )
        self.assertNotContains(response, "hero__title")

    def test_contents_list_links_to_every_section_and_role_level(self):
        response = self.client.get(self.data_analyst_page.url)

        self.assertContains(response, 'href="#what-a-data-analyst-does"')
        self.assertContains(response, 'href="#role-levels"')
        self.assertContains(response, "1. Associate data analyst</a>")
        self.assertContains(response, "2. Senior data analyst</a>")
        self.assertContains(response, 'href="#related-roles"')
        # The framework offers Updates as a jump link, not a contents entry.
        self.assertContains(response, "See all updates")

    def test_side_navigation_groups_every_role_by_family(self):
        groups = role_navigation_groups(current_page_id=self.data_analyst_page.pk)

        self.assertEqual(
            [group["title"] for group in groups],
            ["Architecture roles", "Data roles"],
        )
        self.assertEqual(
            groups[1]["items"],
            [
                {
                    "title": "Data analyst",
                    "url": self.data_analyst_page.url,
                    "is_current": True,
                }
            ],
        )
        self.assertFalse(groups[0]["items"][0]["is_current"])

    def test_side_navigation_renders_with_the_current_role_marked(self):
        response = self.client.get(self.data_analyst_page.url)

        self.assertContains(response, 'aria-label="Data roles"')
        self.assertContains(response, "role-nav__item--active")
        self.assertContains(response, 'aria-current="page"')

    def test_the_navigation_closes_with_the_pages_about_the_framework(self):
        """The live service lists these after the role families, on every page."""
        skills = self.root_page.add_child(
            instance=SkillsAZPage(title="Skills A to Z", slug="skills")
        )
        skills.save_revision().publish()

        groups = role_navigation_groups(current_page_id=self.data_analyst_page.pk)

        self.assertEqual(groups[-1]["title"], "Further resources")
        self.assertEqual(
            groups[-1]["items"],
            [{"title": "Skills A to Z", "url": skills.url, "is_current": False}],
        )

    def test_further_resources_keeps_the_order_the_editors_chose(self):
        for title, slug in (("Roadmap", "roadmap"), ("Job grades", "job-grades")):
            page = self.root_page.add_child(
                instance=ContentPage(title=title, slug=slug)
            )
            page.save_revision().publish()

        group = further_resources_group()

        self.assertEqual([item["title"] for item in group["items"]], ["Roadmap", "Job grades"])

    def test_further_resources_marks_the_page_being_looked_at(self):
        page = self.root_page.add_child(
            instance=ContentPage(title="Roadmap", slug="roadmap")
        )
        page.save_revision().publish()

        group = further_resources_group(current_page_id=page.pk)

        self.assertTrue(group["items"][0]["is_current"])

    def test_further_resources_is_left_out_when_there_is_nothing_in_it(self):
        self.assertIsNone(further_resources_group())
        self.assertEqual(
            [group["title"] for group in role_navigation_groups()],
            ["Architecture roles", "Data roles"],
        )

    def test_draft_and_role_pages_stay_out_of_further_resources(self):
        draft = self.root_page.add_child(
            instance=ContentPage(title="Not ready", slug="not-ready", live=False)
        )
        draft.save()

        self.assertIsNone(further_resources_group())

    def test_roles_without_a_family_are_left_out_of_the_navigation(self):
        unfamilied = GovukRole.objects.create(title="Unfamilied role")
        self._add_role_page(
            title="Unfamilied role", slug="unfamilied-role", role=unfamilied
        )

        titles = [
            item["title"]
            for group in role_navigation_groups()
            for item in group["items"]
        ]

        self.assertNotIn("Unfamilied role", titles)

    def test_a_role_page_shows_no_breadcrumbs(self):
        response = self.client.get(self.data_analyst_page.url)

        self.assertNotContains(response, "govuk-breadcrumbs")

    def test_anchors_stay_unique_when_a_page_renders_several_roles(self):
        page = self.root_page.add_child(
            instance=RolePage(
                title="Data roles",
                slug="data-roles",
                selected_roles=[
                    {"type": "role", "value": self.data_analyst.pk},
                    {"type": "role", "value": self.business_architect.pk},
                ],
            )
        )
        page.save_revision().publish()

        response = self.client.get(page.specific.url)

        self.assertContains(response, 'id="role-levels-data-analyst"')
        self.assertContains(response, 'id="role-levels-business-architect"')
        self.assertContains(response, 'id="associate-data-analyst-data-analyst"')
