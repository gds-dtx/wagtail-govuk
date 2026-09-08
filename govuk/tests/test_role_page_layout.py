from datetime import date

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from wagtail.models import Site

from govuk.models import (
    ContentPage,
    FrameworkContentPage,
    FrameworkSkillsPage,
    GovukChangelogEntry,
    GovukRole,
    GovukSkill,
    further_resources_group,
    role_navigation_groups,
)
from govuk.tests.framework_helpers import make_framework_main_page, role_url


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

        self.main_page = make_framework_main_page(self.root_page)
        self.data_analyst_url = role_url(self.main_page, self.data_analyst)
        self.business_architect_url = role_url(
            self.main_page, self.business_architect
        )

    def test_headings_carry_the_frameworks_anchors(self):
        response = self.client.get(self.data_analyst_url)

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

    def test_the_role_levels_heading_is_followed_by_the_frameworks_intro(self):
        response = self.client.get(self.data_analyst_url)

        self.assertContains(
            response,
            "There are 2 data analyst role levels, from associate data analyst "
            "to senior data analyst.",
        )
        self.assertContains(
            response,
            "The typical responsibilities and skills for each role level are "
            "described in the sections below.",
        )

    def test_the_intro_reads_as_one_where_there_is_a_single_role_level(self):
        response = self.client.get(self.business_architect_url)

        self.assertContains(response, "There is one business architect role level.")
        self.assertContains(
            response,
            "The typical responsibilities and skills for this role level are "
            "described below.",
        )

    def test_a_role_levels_skills_link_to_their_definitions(self):
        skills = self.root_page.add_child(
            instance=FrameworkSkillsPage(title="Skills A to Z", slug="skills")
        )
        skills.save_revision().publish()

        response = self.client.get(self.data_analyst_url)

        self.assertContains(
            response,
            f'<a href="{skills.url}#data-visualisation" class="govuk-link skill-name">'
            "Data visualisation</a>",
            html=True,
        )

    def test_a_skill_name_stays_plain_text_without_a_skills_page_to_link_to(self):
        response = self.client.get(self.data_analyst_url)

        self.assertNotContains(response, "skill-name")
        self.assertContains(response, "Data visualisation")

    def test_the_title_uses_the_standard_heading_rather_than_the_site_hero(self):
        response = self.client.get(self.data_analyst_url)

        self.assertContains(
            response, '<h1 class="govuk-heading-xl">Data analyst</h1>', html=True
        )
        self.assertNotContains(response, "hero__title")

    def test_contents_list_links_to_every_section_and_role_level(self):
        response = self.client.get(self.data_analyst_url)

        self.assertContains(response, 'href="#what-a-data-analyst-does"')
        self.assertContains(response, 'href="#role-levels"')
        self.assertContains(response, "1. Associate data analyst</a>")
        self.assertContains(response, "2. Senior data analyst</a>")
        self.assertContains(response, 'href="#related-roles"')
        # The framework offers Updates as a jump link, not a contents entry.
        self.assertContains(response, "See all updates")

    def test_side_navigation_groups_every_role_by_family(self):
        groups = role_navigation_groups(current_role_slug=self.data_analyst.slug)

        self.assertEqual(
            [group["title"] for group in groups],
            ["Architecture roles", "Data roles"],
        )
        self.assertEqual(
            groups[1]["items"],
            [
                {
                    "title": "Data analyst",
                    "url": self.data_analyst_url,
                    "is_current": True,
                }
            ],
        )
        self.assertFalse(groups[0]["items"][0]["is_current"])

    def test_side_navigation_renders_with_the_current_role_marked(self):
        response = self.client.get(self.data_analyst_url)

        self.assertContains(response, 'aria-label="Data roles"')
        self.assertContains(response, "role-nav__item--active")
        self.assertContains(response, 'aria-current="page"')

    def test_the_navigation_closes_with_the_pages_about_the_framework(self):
        """The live service lists these after the role families, on every page.

        They are the framework main page's own children now, so the skills
        index sits under it rather than at the site root."""
        skills = self.main_page.add_child(
            instance=FrameworkSkillsPage(title="Skills A to Z", slug="skills")
        )
        skills.save_revision().publish()

        groups = role_navigation_groups(current_role_slug=self.data_analyst.slug)

        self.assertEqual(groups[-1]["title"], "Further resources")
        self.assertEqual(
            groups[-1]["items"],
            [{"title": "Skills A to Z", "url": skills.url, "is_current": False}],
        )

    def test_further_resources_keeps_the_order_the_editors_chose(self):
        for title, slug in (("Roadmap", "roadmap"), ("Job grades", "job-grades")):
            page = self.main_page.add_child(
                instance=ContentPage(title=title, slug=slug)
            )
            page.save_revision().publish()

        group = further_resources_group()

        self.assertEqual([item["title"] for item in group["items"]], ["Roadmap", "Job grades"])

    def test_further_resources_marks_the_page_being_looked_at(self):
        page = self.main_page.add_child(
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

    def test_further_resources_does_not_cost_a_query_per_page(self):
        """It runs on every page the navigation appears on. The framework
        content pages beside the roles are the main page's own children."""
        for index in range(6):
            page = self.main_page.add_child(
                instance=ContentPage(title=f"Page {index}", slug=f"page-{index}")
            )
            page.save_revision().publish()

        # A fixed cost whatever the number of pages: finding the framework main
        # page, one query for its children, two for the ids of the framework
        # pages hidden from the navigation, and the default site and its
        # wording, which names the group headings.
        with self.assertNumQueries(6):
            further_resources_group()

    def test_the_navigation_costs_the_same_whatever_the_number_of_roles(self):
        """It runs on every role page of the framework, listing every role, and
        the roles come from the snippets in one query however many there are."""
        with CaptureQueriesContext(connection) as with_two_roles:
            role_navigation_groups()

        for index in range(6):
            GovukRole.objects.create(title=f"Extra role {index}", family="Data")

        with CaptureQueriesContext(connection) as with_eight_roles:
            role_navigation_groups()

        self.assertEqual(len(with_eight_roles), len(with_two_roles))

    def test_draft_pages_stay_out_of_further_resources(self):
        draft = self.main_page.add_child(
            instance=ContentPage(title="Not ready", slug="not-ready", live=False)
        )
        draft.save()

        self.assertIsNone(further_resources_group())

    def test_a_framework_content_page_lists_itself_in_the_sidebar_by_default(self):
        page = self.main_page.add_child(
            instance=FrameworkContentPage(title="Guidance", slug="guidance", body="")
        )
        page.save_revision().publish()

        self.assertTrue(page.specific.show_in_framework_navigation)
        group = further_resources_group()
        self.assertEqual([item["title"] for item in group["items"]], ["Guidance"])

    def test_a_page_switched_off_is_left_out_of_the_sidebar(self):
        shown = self.main_page.add_child(
            instance=FrameworkContentPage(title="Shown", slug="shown", body="")
        )
        shown.save_revision().publish()
        hidden = self.main_page.add_child(
            instance=FrameworkContentPage(
                title="Hidden",
                slug="hidden",
                body="",
                show_in_framework_navigation=False,
            )
        )
        hidden.save_revision().publish()

        group = further_resources_group()

        self.assertEqual([item["title"] for item in group["items"]], ["Shown"])

    def test_a_skills_page_switched_off_is_left_out_of_the_sidebar(self):
        skills = self.main_page.add_child(
            instance=FrameworkSkillsPage(
                title="Skills A to Z",
                slug="skills",
                show_in_framework_navigation=False,
            )
        )
        skills.save_revision().publish()

        self.assertIsNone(further_resources_group())

    def test_roles_without_a_family_are_left_out_of_the_navigation(self):
        GovukRole.objects.create(title="Unfamilied role")

        titles = [
            item["title"]
            for group in role_navigation_groups()
            for item in group["items"]
        ]

        self.assertNotIn("Unfamilied role", titles)

    def test_a_role_page_carries_a_breadcrumb_for_narrow_screens(self):
        """It stands in for the side navigation, which a narrow screen hides."""
        response = self.client.get(self.data_analyst_url)

        self.assertContains(response, "app-breadcrumbs--mobile-only")
        self.assertContains(
            response,
            '<a class="govuk-breadcrumbs__link" href="/">Home</a>',
            html=True,
        )
        self.assertContains(
            response,
            '<a class="govuk-breadcrumbs__link" href="/#data-roles">Data roles</a>',
            html=True,
        )
        self.assertContains(
            response,
            '<li class="govuk-breadcrumbs__list-item" aria-current="page">Data analyst</li>',
            html=True,
        )

    def test_the_breadcrumb_sits_outside_the_banner_landmark(self):
        """The Design System puts it between the header and the main content."""
        response = self.client.get(self.data_analyst_url)
        body = response.content.decode()

        self.assertLess(body.index("</header>"), body.index("govuk-breadcrumbs"))
        self.assertLess(body.index("govuk-breadcrumbs"), body.index('id="main-content"'))

    def test_the_breadcrumb_leaves_out_a_family_it_cannot_point_at(self):
        """A role with no family has no family heading to send a reader back to,
        so its breadcrumb drops the middle crumb and still names the role."""
        homeless = GovukRole.objects.create(
            title="Every role",
            body="<p>A role with no family.</p>",
            levels=[
                {
                    "type": "level",
                    "value": {"title": "Every role", "skills": []},
                }
            ],
        )

        response = self.client.get(role_url(self.main_page, homeless))

        self.assertContains(response, "govuk-breadcrumbs")
        self.assertNotContains(response, ">Data roles</a>")
        self.assertContains(
            response,
            '<li class="govuk-breadcrumbs__list-item" aria-current="page">Every role</li>',
            html=True,
        )

    def test_each_role_route_carries_the_frameworks_bare_anchors(self):
        """Each role is served on its own route now, so the anchors are the
        framework's own ids with no per-role suffix; two roles served from the
        same framework carry the same bare ids on their separate pages."""
        analyst = self.client.get(self.data_analyst_url)
        architect = self.client.get(self.business_architect_url)

        for response in (analyst, architect):
            self.assertContains(response, 'id="role-levels"')
            self.assertNotContains(response, 'id="role-levels-')

        self.assertContains(analyst, 'id="what-a-data-analyst-does"')
        self.assertContains(analyst, 'id="associate-data-analyst"')
        self.assertNotContains(analyst, 'id="associate-data-analyst-data-analyst"')
        self.assertContains(
            architect, 'id="what-a-business-architect-does"'
        )


@override_settings(FEATURE_FLAGS=_feature_flags(skills_enabled=True))
class RoleLeadParagraphTests(TestCase):
    """The one sentence the framework prints under a role's heading."""

    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root_page = self.site.root_page.specific
        self.main_page = make_framework_main_page(self.root_page)

    def _lead_for(self, title: str, *, is_scs: bool = False) -> str:
        role = GovukRole.objects.create(
            title=title,
            family="Data",
            body=f"<p>What {title} does.</p>",
            is_senior_civil_service=is_scs,
            levels=(
                []
                if is_scs
                else [
                    {
                        "type": "level",
                        "value": {"title": f"Senior {title.lower()}", "skills": []},
                    }
                ]
            ),
        )
        return self.client.get(role_url(self.main_page, role)).content.decode()

    def test_a_role_is_introduced_by_the_frameworks_sentence(self):
        self.assertIn(
            "Find out what a business architect in government does and the "
            "skills you need to do the role at each level.",
            self._lead_for("Business architect"),
        )

    def test_a_senior_civil_service_role_has_no_levels_to_send_a_reader_to(self):
        self.assertIn(
            "Find out what a chief technology officer in the Senior Civil "
            "Service does and the skills you need to do the role.",
            self._lead_for("Chief technology officer", is_scs=True),
        )

    def test_a_role_beginning_with_a_vowel_takes_an(self):
        self.assertIn("what an IT service manager in government does", self._lead_for("IT service manager"))

    def test_an_acronym_opening_a_title_is_not_lowered_with_the_first_word(self):
        """It sits a line under "What an IT service manager does", where the
        heading keeps the capitals, so lowering them here reads as a typo."""
        content = self._lead_for("IT service manager")

        self.assertNotIn("what an it service manager", content)

    def test_a_u_sounded_as_you_takes_a_rather_than_an(self):
        """"A user researcher", which is how the framework writes it."""
        self.assertIn("what a user researcher in government does", self._lead_for("User researcher"))

    def test_capitals_inside_a_title_survive_the_lowering_of_its_first_word(self):
        self.assertIn(
            "what a development operations (DevOps) engineer in government does",
            self._lead_for("Development operations (DevOps) engineer"),
        )

    def test_the_heading_takes_the_same_article_as_the_sentence_below_it(self):
        """They sit one line apart, so "What a enterprise architect does" above
        "Find out what an enterprise architect" reads as a mistake in both."""
        content = self._lead_for("Enterprise architect")

        self.assertIn("What an enterprise architect does", content)
        self.assertNotIn("What a enterprise architect does", content)

    def test_the_contents_entry_reads_the_way_the_heading_it_points_at_does(self):
        content = self._lead_for("IT service manager")

        self.assertEqual(content.count("What an IT service manager does"), 2)

    def test_the_anchor_keeps_the_frameworks_wording_whatever_article_is_used(self):
        """A link written against the live service still has to land, and there
        the id stays "what-a-" however the heading reads."""
        self.assertIn(
            'id="what-a-it-service-manager-does"',
            self._lead_for("IT service manager"),
        )
