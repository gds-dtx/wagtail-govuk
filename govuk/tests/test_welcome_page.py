import re
from datetime import date

from django.test import TestCase
from django.utils.text import slugify
from wagtail.models import Site

from govuk.page_import_export import _serialise_page_fields
from govuk.models import (
    ContentPage,
    GovukChangelogEntry,
    GovukRole,
    RolePage,
    SkillsAZPage,
    site_wide_changelog,
)


class ContentPageRoleNavigationTests(TestCase):
    """A content page can carry the role navigation, as the welcome page does.

    Every other wagtail-govuk site has content pages that know nothing about
    roles, so it is off unless asked for.
    """

    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root_page = self.site.root_page.specific

        self.role = GovukRole.objects.create(title="Data analyst", family="Data")
        role_page = self.root_page.add_child(
            instance=RolePage(
                title="Data analyst",
                slug="data-analyst",
                selected_roles=[{"type": "role", "value": self.role.pk}],
            )
        )
        role_page.save_revision().publish()

        self.page = ContentPage(
            title="Welcome", slug="welcome", body="<p>Some words.</p>"
        )
        self.root_page.add_child(instance=self.page)
        self.page.save_revision().publish()

    def test_a_content_page_has_no_role_navigation_by_default(self):
        response = self.client.get(self.page.url)

        self.assertNotContains(response, "role-nav__list")
        self.assertContains(response, "govuk-grid-column-full")

    def test_the_role_navigation_can_be_switched_on(self):
        self.page.show_role_navigation = True
        self.page.save()

        response = self.client.get(self.page.url)

        self.assertContains(response, 'aria-label="Data roles"')
        self.assertContains(response, "Data analyst")
        self.assertContains(response, "govuk-grid-column-one-quarter")
        self.assertContains(response, "govuk-grid-column-three-quarters")

    def test_the_heading_navigation_is_untouched_on_its_own(self):
        self.page.enable_free_text_heading_navigation = True
        self.page.save()

        response = self.client.get(self.page.url)

        self.assertContains(response, "free-text-heading-nav")
        self.assertContains(response, "data-auto-heading-source")
        self.assertContains(response, "govuk-grid-column-two-thirds")
        self.assertNotContains(response, "role-nav__list")

    def test_only_one_side_column_is_ever_drawn(self):
        """Two side columns would not fit, so the role navigation wins."""
        self.page.show_role_navigation = True
        self.page.enable_free_text_heading_navigation = True
        self.page.save()

        response = self.client.get(self.page.url)

        self.assertContains(response, "role-nav__list")
        self.assertNotContains(response, "free-text-heading-nav")
        self.assertNotContains(response, "govuk-grid-column-one-third")

    def test_the_heading_moves_beside_the_navigation(self):
        """With the navigation alongside, the hero would leave it stranded."""
        self.assertContains(self.client.get(self.page.url), "hero__title")

        self.page.show_role_navigation = True
        self.page.save()

        response = self.client.get(self.page.url)

        self.assertNotContains(response, "hero__title")
        self.assertContains(response, '<h1 class="govuk-heading-xl">Welcome</h1>')


class FrameworkUpdatesTests(TestCase):
    """Changelog entries with no role or skill belong to the framework itself."""

    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.page = ContentPage(
            title="Welcome", slug="welcome", body="<p>Some words.</p>"
        )
        self.site.root_page.specific.add_child(instance=self.page)
        self.page.save_revision().publish()

        self.role = GovukRole.objects.create(title="Data analyst", family="Data")
        GovukChangelogEntry.objects.create(
            date=date(2017, 3, 23), note="<p>The framework was published.</p>"
        )
        GovukChangelogEntry.objects.create(
            date=date(2026, 5, 29), note="<p>Content designer skills changed.</p>"
        )
        GovukChangelogEntry.objects.create(
            date=date(2025, 1, 1),
            role=self.role,
            note="<p>Data analyst skills changed.</p>",
        )

    def test_only_entries_without_a_role_or_skill_count_as_site_wide(self):
        changelog = site_wide_changelog()

        self.assertEqual(len(changelog["entries"]), 2)
        self.assertEqual(changelog["published_date"], date(2017, 3, 23))
        self.assertEqual(changelog["last_updated_date"], date(2026, 5, 29))

    def test_the_updates_are_hidden_unless_asked_for(self):
        response = self.client.get(self.page.url)

        self.assertNotContains(response, "See all updates")
        self.assertNotContains(response, "The framework was published.")

    def test_the_updates_show_with_a_jump_link_above_the_content(self):
        self.page.show_framework_updates = True
        self.page.save()

        response = self.client.get(self.page.url)

        self.assertContains(response, "Last updated 29 May 2026")
        self.assertContains(response, 'href="#update-history"')
        self.assertContains(response, 'id="update-history"')
        self.assertContains(response, "Published 23 March 2017")
        self.assertContains(response, "The framework was published.")
        # Entries about a single role stay on that role's page.
        self.assertNotContains(response, "Data analyst skills changed.")


class FrameworkWelcomeContentTests(TestCase):
    """The welcome prose lives in the CMS, in the page's own blocks.

    A rich text field would lose the skill level table, the progress bars and
    every anchor the moment somebody opened the page in the admin and saved it,
    so each block keeps its markup in a template and the editor keeps the words.
    """

    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root_page = self.site.root_page.specific

        for title, family in (
            ("Data analyst", "Data"),
            ("Business architect", "Architecture"),
        ):
            role = GovukRole.objects.create(title=title, family=family)
            page = self.root_page.add_child(
                instance=RolePage(
                    title=title,
                    slug=slugify(title),
                    selected_roles=[{"type": "role", "value": role.pk}],
                )
            )
            page.save_revision().publish()

        self.skills_page = self.root_page.add_child(
            instance=SkillsAZPage(title="Skills A to Z", slug="skills")
        )
        self.skills_page.save_revision().publish()

        self.page = ContentPage(title="Welcome", slug="welcome")
        self.root_page.add_child(instance=self.page)
        self.page.save_revision().publish()

    def _welcome(self) -> str:
        self.page.show_framework_welcome = True
        self.page.framework_welcome_body = self._welcome_fixture()
        self.page.save()
        return self.client.get(self.page.url).content.decode()

    def _welcome_fixture(self) -> list:
        """Representative welcome blocks, so the rendering and export tests have
        content to exercise. The real prose lives in the CMS, not in the app.

        Built in the raw ``{"type", "value"}`` stream shape rather than as
        ``(type, value)`` tuples so that the nested skill-level list serialises
        the same way editor-authored content does when it is exported.
        """
        return [
            {
                "type": "section",
                "value": {
                    "heading": "How to use this framework",
                    "level": "h2",
                    "anchor": "",
                    "body": "<p>Anyone can use this framework.</p>",
                },
            },
            {
                "type": "section",
                "value": {
                    "heading": "Skills in this framework",
                    "level": "h2",
                    "anchor": "",
                    "body": (
                        "<p>See the "
                        f'<a href="{self.skills_page.url}">Skills A to Z</a>.</p>'
                    ),
                },
            },
            {
                "type": "skill_level_definitions",
                "value": {
                    "caption": "Skill level definitions",
                    "level_column_heading": "Skill level definitions",
                    "meaning_column_heading": "What the level means",
                    "levels": [
                        {
                            "name": "Awareness",
                            "filled_segments": 1,
                            "description": "<p>Demonstrate knowledge of the skill's tools.</p>",
                        },
                        {
                            "name": "Working",
                            "filled_segments": 2,
                            "description": "<p>Apply the skill with some support.</p>",
                        },
                        {
                            "name": "Practitioner",
                            "filled_segments": 3,
                            "description": "<p>Apply the skill without support.</p>",
                        },
                        {
                            "name": "Expert",
                            "filled_segments": 4,
                            "description": "<p>Recognised as an expert in the skill.</p>",
                        },
                    ],
                },
            },
            {
                "type": "section",
                "value": {
                    "heading": "Job grades in this framework",
                    "level": "h2",
                    "anchor": "",
                    "body": "<p>Roles map to Civil Service job grades.</p>",
                },
            },
            {
                "type": "section",
                "value": {
                    "heading": "Support",
                    "level": "h2",
                    "anchor": "",
                    "body": "<p>Contact us for support.</p>",
                },
            },
        ]

    def test_the_welcome_content_is_off_unless_asked_for(self):
        page = self.client.get(self.page.url).content.decode()

        self.assertNotIn("How to use this framework", page)
        self.assertNotIn("progress-bar__container", page)

    def test_the_welcome_prose_renders_without_anything_in_the_body(self):
        page = self._welcome()

        self.assertEqual(self.page.body, "")
        for heading in (
            "How to use this framework",
            "Skills in this framework",
            "Job grades in this framework",
            "Support",
        ):
            self.assertIn(heading, page)

    def test_the_prose_comes_from_the_page_rather_than_a_template(self):
        """The content team has to be able to reword this without a deploy."""
        self._welcome()

        self.page.framework_welcome_body = [
            (
                "section",
                {
                    "heading": "How the team changed this",
                    "level": "h2",
                    "anchor": "",
                    "body": "<p>Edited in the admin.</p>",
                },
            )
        ]
        self.page.save()
        page = self.client.get(self.page.url).content.decode()

        self.assertIn("How the team changed this", page)
        self.assertIn("Edited in the admin.", page)
        self.assertNotIn("How to use this framework", page)

    def test_the_contents_list_follows_the_headings_the_editor_wrote(self):
        self._welcome()

        self.page.framework_welcome_body = [
            (
                "section",
                {
                    "heading": "Opening",
                    "level": "h2",
                    "anchor": "",
                    "body": "<p>First.</p>",
                },
            ),
            (
                "section",
                {
                    "heading": "A note",
                    "level": "h3",
                    "anchor": "",
                    "body": "<p>Not a main section.</p>",
                },
            ),
            (
                "section",
                {
                    "heading": "Closing",
                    "level": "h2",
                    "anchor": "the-end",
                    "body": "<p>Last.</p>",
                },
            ),
        ]
        self.page.save()
        page = self.client.get(self.page.url).content.decode()

        contents = page[page.index('id="contents"') : page.index("mobile-homepage-roles")]
        self.assertEqual(
            re.findall(r'href="#([^"]+)"', contents),
            ["opening", "architecture-roles", "data-roles", "further-resources", "the-end"],
        )
        # The sub-section still renders, it just stays out of the contents list.
        self.assertIn('id="a-note"', page)

    def test_the_skill_level_table_and_its_bars_survive(self):
        """These are exactly what a rich text field would have thrown away."""
        page = self._welcome()

        self.assertIn('<table class="govuk-table homepage">', page)
        self.assertEqual(page.count("progress-bar__container"), 4)
        self.assertIn("skill's tools", page)

    def test_the_skills_link_points_at_the_imported_skills_page(self):
        page = self._welcome()

        self.assertIn(f'href="{self.skills_page.url}"', page)
        self.assertNotIn("ddat-capability-framework.service.gov.uk", page)

    def test_the_contents_list_has_an_entry_for_every_section(self):
        page = self._welcome()

        contents = page[page.index('id="contents"') : page.index("mobile-homepage-roles")]
        self.assertEqual(
            re.findall(r'href="#([^"]+)"', contents),
            [
                "how-to-use-this-framework",
                "architecture-roles",
                "data-roles",
                "further-resources",
                "skills-in-this-framework",
                "job-grades-in-this-framework",
                "support",
            ],
        )

    def test_every_contents_link_lands_somewhere(self):
        page = self._welcome()

        ids = set(re.findall(r'id="([^"]+)"', page))
        anchors = re.findall(r'href="#([^"]+)"', page)

        self.assertEqual([a for a in anchors if a not in ids], [])

    def test_no_anchor_is_claimed_twice(self):
        """The live service gives three different headings the same id."""
        page = self._welcome()

        ids = re.findall(r'id="([^"]+)"', page)

        self.assertEqual(sorted(ids), sorted(set(ids)))

    def test_the_role_lists_repeat_the_side_navigation_for_narrow_screens(self):
        page = self._welcome()

        lists = page[page.index("mobile-homepage-roles") :]
        self.assertIn('<h2 class="govuk-heading-l" id="data-roles">Data roles</h2>', lists)
        self.assertIn("Business architect", lists)
        self.assertIn("Skills A to Z", lists)

    def test_the_welcome_content_does_not_drag_the_side_navigation_in_with_it(self):
        """The two are separate switches, even though the page uses both."""
        page = self._welcome()

        self.assertNotIn("role-nav__list", page)
        self.assertIn("mobile-homepage-roles", page)

    def test_the_switches_survive_an_export_and_import(self):
        self.page.show_framework_welcome = True
        self.page.show_role_navigation = True
        self.page.show_framework_updates = True
        self.page.save()

        fields = _serialise_page_fields(self.page)

        self.assertTrue(fields["show_framework_welcome"])
        self.assertTrue(fields["show_role_navigation"])
        self.assertTrue(fields["show_framework_updates"])

    def test_the_welcome_content_survives_an_export(self):
        """A site transfer has to carry the prose, now that it is page content."""
        self._welcome()

        blocks = _serialise_page_fields(self.page)["framework_welcome_body"]

        headings = [
            block["value"]["heading"]
            for block in blocks
            if block["type"] == "section"
        ]
        self.assertIn("How to use this framework", headings)

        table = next(
            block for block in blocks if block["type"] == "skill_level_definitions"
        )
        self.assertEqual(
            [level["name"] for level in table["value"]["levels"]],
            ["Awareness", "Working", "Practitioner", "Expert"],
        )
