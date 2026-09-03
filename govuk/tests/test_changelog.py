from datetime import date

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from wagtail.models import Site

from govuk.management.commands.import_capability_framework import (
    changelog_note_to_html,
)
from govuk.models import (
    GovukChangelogEntry,
    GovukRole,
    GovukSkill,
    RolePage,
)


def _feature_flags(*, skills_enabled: bool) -> dict[str, bool]:
    return {
        "SKILLS": skills_enabled,
        "ORGANISATIONS": False,
        "PEOPLE_FINDER": False,
        "FEEDBACK": False,
    }


@override_settings(FEATURE_FLAGS=_feature_flags(skills_enabled=True))
class ChangelogEntryTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root_page = self.site.root_page.specific

        self.role = GovukRole.objects.create(title="Data engineer")
        self.skill = GovukSkill.objects.create(title="Data modelling")

        self.first_entry = GovukChangelogEntry.objects.create(
            date=date(2020, 1, 7),
            role=self.role,
            note="<p>First published.</p>",
        )
        self.latest_entry = GovukChangelogEntry.objects.create(
            date=date(2026, 5, 29),
            role=self.role,
            note="<p>Skills updated.</p>",
        )
        self.unpublished_entry = GovukChangelogEntry.objects.create(
            date=date(2026, 7, 1),
            role=self.role,
            note="<p>Draft note.</p>",
            live=False,
        )
        self.site_wide_entry = GovukChangelogEntry.objects.create(
            date=date(2026, 5, 29),
            note="<p>A new role was added.</p>",
        )

        page = self.root_page.add_child(
            instance=RolePage(
                title="Data engineer",
                slug="data-engineer",
                selected_roles=[{"type": "role", "value": self.role.pk}],
            )
        )
        page.save_revision().publish()
        self.role_page = page.specific

    def test_role_changelog_returns_published_entries_newest_first(self):
        changelog = self.role.get_changelog()

        self.assertEqual(
            changelog["entries"], [self.latest_entry, self.first_entry]
        )
        self.assertNotIn(self.unpublished_entry, changelog["entries"])
        self.assertNotIn(self.site_wide_entry, changelog["entries"])

    def test_role_changelog_dates_span_first_and_latest_entry(self):
        changelog = self.role.get_changelog()

        self.assertEqual(changelog["published_date"], date(2020, 1, 7))
        self.assertEqual(changelog["last_updated_date"], date(2026, 5, 29))

    def test_changelog_dates_are_none_without_entries(self):
        changelog = self.skill.get_changelog()

        self.assertEqual(changelog["entries"], [])
        self.assertIsNone(changelog["published_date"])
        self.assertIsNone(changelog["last_updated_date"])

    def test_skill_changelog_returns_its_own_entries(self):
        entry = GovukChangelogEntry.objects.create(
            date=date(2025, 3, 1),
            skill=self.skill,
            note="<p>Level descriptions updated.</p>",
        )
        self.assertEqual(self.skill.get_changelog()["entries"], [entry])

    def test_entry_cannot_target_both_a_role_and_a_skill(self):
        entry = GovukChangelogEntry(
            date=date(2026, 1, 1),
            role=self.role,
            skill=self.skill,
            note="<p>Ambiguous.</p>",
        )
        with self.assertRaises(ValidationError):
            entry.full_clean()

    def test_role_page_renders_updates_section(self):
        response = self.client.get(self.role_page.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Updates")
        self.assertContains(response, "7 January 2020")
        self.assertContains(response, "29 May 2026")
        self.assertContains(response, "Skills updated.")
        self.assertNotContains(response, "Draft note.")
        self.assertNotContains(response, "A new role was added.")


class ChangelogNoteConversionTests(TestCase):
    def test_markdown_links_become_anchors(self):
        html = changelog_note_to_html(
            "[Data engineer](/role/data-engineer) has updated skills."
        )
        self.assertEqual(
            html,
            '<p><a href="/role/data-engineer">Data engineer</a> has updated skills.</p>',
        )

    def test_each_line_becomes_a_paragraph(self):
        html = changelog_note_to_html("First change.\n\nSecond change.")
        self.assertEqual(html, "<p>First change.</p><p>Second change.</p>")

    def test_html_in_notes_is_escaped(self):
        html = changelog_note_to_html("<script>alert(1)</script>")
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_empty_note_returns_empty_string(self):
        self.assertEqual(changelog_note_to_html(""), "")
        self.assertEqual(changelog_note_to_html("   \n  "), "")
