from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from wagtail.models import Site

from govuk.models import (
    GovukChangelogEntry,
    GovukRole,
    GovukSkill,
    RolePage,
    SkillsAZPage,
)


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
        self.assertContains(response, "app-breadcrumbs--mobile-only")

    def test_the_skills_index_breadcrumb_reads_the_way_a_roles_does(self):
        """Both stand in for the same navigation, so both start at Home."""
        response = self.client.get(self.skills_page.url)

        self.assertContains(
            response,
            '<a class="govuk-breadcrumbs__link" href="/">Home</a>',
            html=True,
        )
        self.assertContains(
            response,
            '<li class="govuk-breadcrumbs__list-item" aria-current="page">Skills A-Z</li>',
            html=True,
        )
        # Not the site name, which is long enough to wrap on a narrow screen.
        self.assertNotContains(response, "Capability Framework</a>")

    @override_settings(FEATURE_FLAGS=_feature_flags(skills_enabled=False))
    def test_skills_az_page_is_not_creatable_when_skills_feature_is_disabled(self):
        self.assertFalse(SkillsAZPage.can_create_at(self.root_page))

    @override_settings(FEATURE_FLAGS=_feature_flags(skills_enabled=False))
    def test_the_a_to_z_does_not_serve_without_the_framework(self):
        """Creatability is checked in the admin; the page import is not the admin."""
        response = self.client.get(self.skills_page.url)

        self.assertEqual(response.status_code, 404)


@override_settings(FEATURE_FLAGS=_feature_flags(skills_enabled=True))
class SkillsAZUpdatesTests(TestCase):
    """A skill's change notes are readable where the skill is.

    Role entries show on the role's page and site-wide entries on the home
    page, but a skill's were stored and shown nowhere -- an editor could
    attach one in the CMS and never find it again on the front end.
    """

    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root_page = self.site.root_page.specific
        self.noted_skill = GovukSkill.objects.create(
            title="Noted skill", body="<p>Has history.</p>"
        )
        self.quiet_skill = GovukSkill.objects.create(
            title="Quiet skill", body="<p>No history.</p>"
        )
        GovukChangelogEntry.objects.create(
            date="2026-07-14",
            note="<p>Guidance rewritten for clarity.</p>",
            skill=self.noted_skill,
        )
        self.skills_page = self.root_page.add_child(
            instance=SkillsAZPage(title="Skills A-Z", slug="skills")
        )
        self.skills_page.save_revision().publish()

    def test_a_skills_entries_show_in_its_section(self):
        response = self.client.get(self.skills_page.url)

        self.assertContains(response, "Guidance rewritten for clarity.")
        self.assertContains(response, "14 July 2026")

    def test_a_skill_with_no_entries_gets_no_updates_heading(self):
        response = self.client.get(self.skills_page.url)

        # One skill has history, so the heading appears exactly once.
        self.assertContains(response, ">Updates</h3>", count=1)

    def test_the_entries_cost_one_query_however_many_skills_have_them(self):
        for index in range(6):
            skill = GovukSkill.objects.create(
                title=f"Costed skill {index}", body="<p>Body.</p>"
            )
            GovukChangelogEntry.objects.create(
                date="2026-07-01", note="<p>Changed.</p>", skill=skill
            )

        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.skills_page.url)
        self.assertEqual(response.status_code, 200)

        changelog_queries = [
            entry
            for entry in queries.captured_queries
            if "govukchangelogentry" in entry["sql"].lower()
        ]
        self.assertEqual(len(changelog_queries), 1, changelog_queries)
