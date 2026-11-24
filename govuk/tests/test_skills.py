import importlib
from unittest.mock import patch

from django.db import connection
from django.test import SimpleTestCase, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from wagtail.models import Site

from govuk.models import (
    FrameworkSkillsPage,
    GovukChangelogEntry,
    GovukRole,
    GovukSkill,
)
from govuk.tests.helpers import feature_flags


def _reload_hooks():
    import govuk.wagtail_hooks as hooks_module

    return importlib.reload(hooks_module)


@override_settings(FEATURE_FLAGS=feature_flags(skills=True))
class FrameworkSkillsPageTests(TestCase):
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
            instance=FrameworkSkillsPage(
                title="Skills A-Z",
                slug="skills-az",
                body="<p>Skill definitions in alphabetical order.</p>",
            )
        )
        skills_page.save_revision().publish()
        self.skills_page = skills_page.specific

    def test_each_skill_has_an_id_a_role_page_can_link_to(self):
        html = self.client.get(self.skills_page.url).content.decode()

        for skill in (self.alpha_skill, self.beta_skill, self.charlie_skill):
            self.assertIn(f'id="{skill.slug}"', html)

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

    def test_a_level_description_reads_on_from_the_same_sentence_as_a_role_page(self):
        """It is one skill at one level in both places, so it reads the same."""
        response = self.client.get(self.skills_page.url)
        response_text = response.content.decode("utf-8")

        intro_index = response_text.find(
            '<p class="govuk-body">You can:</p>',
        )
        self.assertNotEqual(intro_index, -1)
        points_index = response_text.find("Alpha awareness point.")
        self.assertLess(intro_index, points_index)

    def test_a_level_with_nothing_written_for_it_gets_no_lead_in(self):
        """A lead-in with no points under it is a sentence that stops dead."""
        GovukSkill.objects.all().delete()
        GovukSkill.objects.create(title="Empty skill", body="<p>Empty body.</p>")

        response = self.client.get(self.skills_page.url)

        self.assertNotContains(response, '<p class="govuk-body">You can:</p>')

    def test_a_level_marked_not_defined_is_printed_as_the_bare_sentence(self):
        """The framework's "not defined" sentence is not the end of "You can:",
        so it is printed on its own in either of its two wordings. A level with
        nothing written at all still gets the editors' message."""
        GovukSkill.objects.all().delete()
        GovukSkill.objects.create(
            title="Undefined skill",
            body="<p>Undefined body.</p>",
            awareness_points=[
                {"type": "point", "value": "This skill level is currently not defined."}
            ],
            working_points=[
                {"type": "point", "value": "This skill level is not defined"}
            ],
            practitioner_points=[
                {"type": "point", "value": "Real practitioner point."}
            ],
        )

        response = self.client.get(self.skills_page.url)
        text = response.content.decode("utf-8")

        self.assertEqual(text.count('<p class="govuk-body">You can:</p>'), 1)
        self.assertNotIn('<li class="govuk-body">This skill level', text)
        self.assertContains(
            response,
            '<p class="govuk-body govuk-!-margin-bottom-0">'
            "This skill level is currently not defined.</p>",
        )
        self.assertContains(
            response,
            '<p class="govuk-body govuk-!-margin-bottom-0">This skill level is not defined</p>',
        )
        self.assertContains(response, "No description provided.")

    def test_a_senior_civil_service_skill_reads_as_it_does_on_the_live_service(self):
        """With no levels table, the entry is headed "Description, including
        examples of leadership", the examples are introduced by a sentence, and
        a rule stands where the table would end."""
        GovukSkill.objects.all().delete()
        scs = GovukSkill.objects.create(
            title="Capability building",
            body="<p>You can:</p><ul><li>guide the organisation.</li></ul>",
            is_senior_civil_service=True,
            leadership_points=[
                {"type": "point", "value": "prioritising capability needs."}
            ],
        )
        GovukRole.objects.create(
            title="Chief data officer",
            family="Chief digital and data",
            is_senior_civil_service=True,
            scs_skills=[{"type": "skill", "value": scs.pk}],
        )

        response = self.client.get(self.skills_page.url)
        text = response.content.decode("utf-8")

        heading = text.find("Description, including examples of leadership</h3>")
        body = text.find("guide the organisation.")
        lead_in = text.find(
            '<p class="govuk-body govuk-!-margin-bottom-1">'
            "Examples of leadership using this skill:</p>"
        )
        rule = text.find(
            '<hr class="govuk-section-break govuk-section-break--m '
            'govuk-section-break--visible">'
        )
        roles = text.find("Roles that require this skill</h3>")

        self.assertTrue(
            0 < heading < body < lead_in < rule < roles,
            (heading, body, lead_in, rule, roles),
        )
        self.assertNotIn("Examples of leadership using this skill</h3>", text)

    def test_a_skill_with_levels_draws_no_extra_rule_above_its_roles(self):
        """The rule is for Senior Civil Service skills only; the others have the levels table's edge."""
        GovukRole.objects.create(
            title="Data analyst",
            family="Data",
            levels=[
                {
                    "type": "level",
                    "value": {
                        "title": "Data analyst",
                        "description": "<p>Analyses.</p>",
                        "skills": [
                            {"skill": self.alpha_skill.pk, "level": "awareness"}
                        ],
                    },
                }
            ],
        )

        response = self.client.get(self.skills_page.url)

        self.assertContains(response, "Roles that require this skill")
        self.assertNotContains(response, "govuk-section-break--visible")

    def test_the_skills_index_carries_the_frameworks_side_navigation(self):
        """It sits alongside the roles, so it is navigated the same way."""
        GovukRole.objects.create(title="Data analyst", family="Data")

        response = self.client.get(self.skills_page.url)

        self.assertContains(response, 'aria-label="Data roles"')
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

    @override_settings(FEATURE_FLAGS=feature_flags())
    def test_skills_az_page_is_not_creatable_when_skills_feature_is_disabled(self):
        self.assertFalse(FrameworkSkillsPage.can_create_at(self.root_page))

    def test_only_one_skills_page_is_allowed_per_site(self):
        """Like the framework main page: one skills index, so the search
        results and role links never have to guess which one they mean."""
        self.assertEqual(FrameworkSkillsPage.max_count, 1)
        # setUp has already created one, so a second cannot be added.
        self.assertFalse(FrameworkSkillsPage.can_create_at(self.root_page))

    @override_settings(FEATURE_FLAGS=feature_flags())
    def test_the_a_to_z_does_not_serve_without_the_framework(self):
        """Creatability is checked in the admin; the page import is not the admin."""
        response = self.client.get(self.skills_page.url)

        self.assertEqual(response.status_code, 404)


@override_settings(FEATURE_FLAGS=feature_flags(skills=True))
class SkillsAZUpdatesTests(TestCase):
    """A skill's change notes are readable where the skill is."""

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
            instance=FrameworkSkillsPage(title="Skills A-Z", slug="skills")
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


class SkillsWagtailHooksTests(SimpleTestCase):
    def test_the_framework_admin_is_registered_only_with_the_flag_on(self):
        for skills in (True, False):
            with (
                self.subTest(skills=skills),
                override_settings(FEATURE_FLAGS=feature_flags(skills=skills)),
                patch("wagtail.snippets.models.register_snippet") as register_snippet,
                patch("wagtail.contrib.settings.models.register_setting") as register_setting,
            ):
                hooks = _reload_hooks()
                snippets = [c.args[0] for c in register_snippet.call_args_list]
                settings = [c.args[0] for c in register_setting.call_args_list]

                self.assertEqual(hooks.CapabilityFrameworkViewSetGroup in snippets, skills)
                self.assertIn(hooks.GovukTagViewSet, snippets)
                self.assertIn(hooks.ExternalContentItemViewSet, snippets)
                self.assertNotIn(hooks.GovukSkillViewSet, snippets)
                self.assertEqual(hooks.CapabilityFrameworkWordingSettings in settings, skills)
                self.assertEqual(hooks.SidebarSettings in settings, skills)
        _reload_hooks()

    def test_the_capability_framework_menu_groups_the_snippets_and_wording(self):
        hooks_module = _reload_hooks()

        group = hooks_module.CapabilityFrameworkViewSetGroup
        self.assertEqual(group.menu_label, "Capability framework")
        self.assertEqual(
            group.items,
            (
                hooks_module.GovukSkillViewSet,
                hooks_module.GovukRoleViewSet,
                hooks_module.GovukChangelogEntryViewSet,
            ),
        )
        # The two site settings are added to the group's submenu alongside the
        # three snippets.
        labels = [item.label for item in group().get_submenu_items()]
        self.assertEqual(
            labels,
            [
                "Skills",
                "Roles",
                "Changelog",
                "Sidebar settings",
                "Capability framework wording",
            ],
        )
