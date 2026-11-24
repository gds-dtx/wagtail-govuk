"""The framework's CSV import and export commands and the conversions behind them."""

import csv
import tempfile
import time
from datetime import date
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from wagtail.models import Site

from govuk.capability_framework import (
    LEVEL_NOT_DEFINED,
    NOT_IN_USE,
    changelog_html_to_note,
    changelog_note_to_html,
    parse_iso_date,
    parse_points,
    points_to_text,
    repair_changelog_html,
    rich_html_to_text,
    text_to_rich_html,
)
from govuk.management.commands.import_capability_framework import SITE_NAME
from govuk.models import (
    FrameworkMainPage,
    FrameworkSkillsPage,
    GovukChangelogEntry,
    GovukRole,
    GovukSkill,
)
from govuk.tests.helpers import feature_flags, role_url

ROLES_CSV = """Role Family,Role,Role Description,Role Level,Role Level Description,Skill Name,Skill Description,Skill Level,Skill Level Description,Role Type
Data,Data engineer,"A data engineer builds data products.

In this role, you will:
- build pipelines",Data engineer,"Delivers designs set by others.",Data modelling,Producing data models.,Working,"You can:
- produce data models
- reverse-engineer models",
Data,Data engineer,"A data engineer builds data products.

In this role, you will:
- build pipelines",Senior data engineer,"Leads implementation.",Data modelling,Producing data models.,Practitioner,"You can:
- produce models across subject areas",
Data,Data architect,"A data architect sets data strategy.",Data architect,"Owns the data architecture.",Data modelling,Producing data models.,Expert,"You can:
- align models across government",
Chief digital and data,Chief data officer,"A chief data officer leads data.",NOT IN USE,NOT IN USE,Strategic data planning,"You can:
- set a data strategy",NOT IN USE,NOT IN USE,Senior Civil Service
"""

SKILLS_CSV = """Skill Name,Skill Description,Awareness,Working,Practitioner,Expert,Roles that require Skill
Data modelling,Producing data models.,"You can:
- explain data modelling","You can:
- produce data models
- reverse-engineer models","You can:
- produce models across subject areas","You can:
- align models across government","Data engineer, Data architect"
Strategic data planning,"You can:
- set a data strategy",,,,,Chief data officer
"""

CHANGELOG_CSV = """Timestamp,Page,Change note
2026-05-29,Homepage,"[Data engineer](/role/data-engineer) has an updated list of skills."
2026-05-29,Data engineer,"The skill 'data modelling' was updated."
2020-01-07,Data engineer,"First published."
2026-05-29,Unknown page,"Should not import."
2026-05-29,Homepage,
"""


def _write_fixtures(directory: Path, *, include_changelog: bool = True):
    (directory / "roles.csv").write_text(ROLES_CSV)
    (directory / "skills.csv").write_text(SKILLS_CSV)
    if include_changelog:
        (directory / "changelog.csv").write_text(CHANGELOG_CSV)


@override_settings(FEATURE_FLAGS=feature_flags(skills=True))
class CapabilityFrameworkCommandTestCase(TestCase):
    """The published exports written to a temporary directory."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data_dir = Path(tmp.name) / "in"
        self.data_dir.mkdir()
        self.out_dir = Path(tmp.name) / "out"
        _write_fixtures(self.data_dir)

    def _import(self) -> str:
        out = StringIO()
        call_command("import_capability_framework", str(self.data_dir), stdout=out)
        return out.getvalue()

    def _export(self):
        call_command("export_capability_framework", str(self.out_dir), stdout=StringIO())

    def _home(self):
        return Site.objects.get(is_default_site=True).root_page.specific


class ImportCapabilityFrameworkTests(CapabilityFrameworkCommandTestCase):
    def test_imports_skills_with_level_points(self):
        self._import()

        skill = GovukSkill.objects.get(slug="data-modelling")
        self.assertEqual(skill.title, "Data modelling")
        self.assertEqual(skill.points_for_level("awareness"), ["explain data modelling"])
        self.assertEqual(
            skill.points_for_level("working"),
            ["produce data models", "reverse-engineer models"],
        )

    def test_imports_roles_with_levels_and_skill_requirements(self):
        self._import()

        role = GovukRole.objects.get(slug="data-engineer")
        levels = role.get_levels_with_skills()
        self.assertEqual(
            [level["title"] for level in levels],
            ["Data engineer", "Senior data engineer"],
        )
        self.assertEqual(levels[0]["skills"][0]["required_level"], "working")
        self.assertEqual(levels[1]["skills"][0]["required_level"], "practitioner")

    def test_creates_a_framework_main_page_and_a_skills_index_but_no_role_pages(self):
        """Roles no longer have a page each: the framework main page serves one
        per role snippet at ``role/<role-slug>/``, so the import creates the main
        page and the A to Z and nothing per role."""
        self._import()

        home = self._home()
        self.assertIsInstance(home, FrameworkMainPage)
        self.assertTrue(home.live)
        self.assertEqual(FrameworkMainPage.objects.live().count(), 1)
        self.assertEqual(FrameworkSkillsPage.objects.live().count(), 1)

        # Every role is reachable as a route under the main page, with no page
        # of its own in the tree.
        role = GovukRole.objects.get(slug="data-engineer")
        self.assertEqual(self.client.get(role_url(home, role)).status_code, 200)

    def test_scs_role_is_flagged_with_flat_skills_and_no_levels(self):
        self._import()

        role = GovukRole.objects.get(slug="chief-data-officer")
        self.assertTrue(role.is_senior_civil_service)
        self.assertEqual(role.get_levels_with_skills(), [])

        scs_skills = role.get_scs_skills()
        self.assertEqual(
            [row["skill"].title for row in scs_skills], ["Strategic data planning"]
        )
        self.assertIn("set a data strategy", scs_skills[0]["skill"].body)

    def test_scs_skills_are_flagged_and_keep_leadership_examples(self):
        (self.data_dir / "skills.csv").write_text(
            SKILLS_CSV.replace(
                '"You can:\n- set a data strategy",,,,,Chief data officer',
                '"You can:\n- set a data strategy\n'
                "Examples of leadership using this skill:\n"
                '- persuading other leaders to invest",,,,,Chief data officer',
            )
        )

        self._import()

        skill = GovukSkill.objects.get(slug="strategic-data-planning")
        self.assertTrue(skill.is_senior_civil_service)
        self.assertEqual(
            skill.get_leadership_points(), ["persuading other leaders to invest"]
        )
        self.assertNotIn("Examples of leadership", skill.body)

    def test_imports_changelog_entries_against_roles_and_site_wide(self):
        self._import()

        role = GovukRole.objects.get(slug="data-engineer")
        changelog = role.get_changelog()
        self.assertEqual(len(changelog["entries"]), 2)
        self.assertEqual(changelog["published_date"], date(2020, 1, 7))
        self.assertEqual(changelog["last_updated_date"], date(2026, 5, 29))

        self.assertEqual(
            GovukChangelogEntry.objects.filter(
                role__isnull=True, skill__isnull=True
            ).count(),
            1,
        )

    def test_changelog_rows_that_cannot_be_matched_are_skipped(self):
        output = self._import()

        self.assertIn("Unknown page", output)
        self.assertEqual(GovukChangelogEntry.objects.count(), 3)

    def test_running_twice_does_not_duplicate_content(self):
        self._import()
        second_run = self._import()

        self.assertEqual(GovukSkill.objects.count(), 2)
        self.assertEqual(GovukRole.objects.count(), 3)
        self.assertEqual(FrameworkMainPage.objects.count(), 1)
        self.assertEqual(GovukChangelogEntry.objects.count(), 3)
        self.assertIn("0 created", second_run)

    def test_duplicate_source_rows_are_not_imported_twice(self):
        # the published export repeats some rows verbatim
        with open(self.data_dir / "roles.csv") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames
            rows = list(reader)
        with open(self.data_dir / "roles.csv", "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows + [rows[0]])

        self._import()

        role = GovukRole.objects.get(slug="data-engineer")
        first_level = role.get_levels_with_skills()[0]
        self.assertEqual(len(first_level["skills"]), 1)

    def test_missing_changelog_file_is_tolerated(self):
        (self.data_dir / "changelog.csv").unlink()

        output = self._import()

        self.assertIn("skipping changelog", output)
        self.assertEqual(GovukChangelogEntry.objects.count(), 0)

    def test_missing_required_file_raises(self):
        (self.data_dir / "skills.csv").unlink()

        with self.assertRaises(Exception):
            self._import()

    def test_roles_record_their_family(self):
        self._import()

        self.assertEqual(GovukRole.objects.get(slug="data-engineer").family, "Data")
        self.assertEqual(
            GovukRole.objects.get(slug="chief-data-officer").family,
            "Chief digital and data",
        )

    def test_placeholder_home_page_is_replaced_and_site_named(self):
        self._import()

        site = Site.objects.get(is_default_site=True)
        home = site.root_page.specific
        self.assertIsInstance(home, FrameworkMainPage)
        self.assertEqual(home.slug, "home")
        self.assertEqual(site.site_name, SITE_NAME)

    def test_home_page_lists_every_role_grouped_by_family(self):
        self._import()

        home = self._home()
        page = self.client.get("/").content.decode()
        self.assertIn("Data roles", page)
        self.assertIn("Chief digital and data roles", page)
        for slug in ("data-engineer", "data-architect", "chief-data-officer"):
            self.assertIn(role_url(home, GovukRole.objects.get(slug=slug)), page)
        self.assertIn("Skills A to Z", page)

    def test_home_page_is_configured_but_seeds_no_welcome_prose(self):
        """The importer sets the framework home page up. The welcome switch is
        on so the page renders as the framework home from the first import, but
        the prose (framework_welcome_body) is left empty -- it is CMS content
        the content team authors and imports separately."""
        self._import()

        home = self._home()
        self.assertIn("Learn about the digital, data", home.hero_intro)
        self.assertTrue(home.show_role_navigation)
        self.assertTrue(home.show_framework_updates)
        self.assertTrue(home.show_framework_welcome)
        # The welcome prose is never written by the importer.
        self.assertEqual(len(home.framework_welcome_body), 0)

    def test_site_settings_are_configured_on_first_import(self):
        from govuk.models import CustomiseSettings, SearchSettings

        self._import()

        site = Site.objects.get(is_default_site=True)
        customise = CustomiseSettings.for_site(site)
        self.assertEqual(customise.header_logo, "govuk")
        self.assertEqual(customise.service_name_location, "navigation")
        self.assertEqual(customise.sign_in_location, "hidden")
        self.assertTrue(customise.show_back_to_top)
        search = SearchSettings.for_site(site)
        self.assertEqual(search.search_location, "navigation")
        self.assertFalse(search.enable_search_results_page)

    def test_a_second_import_leaves_editor_welcome_content_alone(self):
        """Because the importer never writes the welcome prose, an editor's own
        content survives a re-import untouched."""
        self._import()

        home = self._home()
        home.show_framework_welcome = True
        home.framework_welcome_body = [
            (
                "section",
                {
                    "heading": "Written by an editor",
                    "level": "h2",
                    "anchor": "",
                    "body": "<p>Kept.</p>",
                },
            )
        ]
        home.save()
        home.save_revision().publish()

        self._import()

        home = self._home()
        headings = [
            block.value["heading"]
            for block in home.framework_welcome_body
            if block.block_type == "section"
        ]
        self.assertEqual(headings, ["Written by an editor"])
        self.assertTrue(home.show_framework_welcome)

    def test_a_second_import_leaves_editor_home_page_config_alone(self):
        """Once configured, the CMS owns the home page: a re-import must not
        revert the editor's intro wording or navigation toggles."""
        self._import()

        home = self._home()
        home.hero_intro = "<p>Edited by the content team.</p>"
        home.show_framework_updates = False
        home.save()
        home.save_revision().publish()

        self._import()

        home = self._home()
        self.assertEqual(home.hero_intro, "<p>Edited by the content team.</p>")
        self.assertFalse(home.show_framework_updates)

    def test_home_page_is_reachable_and_links_resolve(self):
        self._import()

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("Data engineer", body)
        # The role is served on a route under the framework main page rather
        # than on a page of its own, so its URL is derived from the page.
        home = self._home()
        role = GovukRole.objects.get(slug="data-engineer")
        self.assertEqual(self.client.get(role_url(home, role)).status_code, 200)

    def test_second_import_keeps_the_same_home_page(self):
        self._import()
        first = Site.objects.get(is_default_site=True).root_page_id
        self._import()

        self.assertEqual(Site.objects.get(is_default_site=True).root_page_id, first)
        self.assertEqual(FrameworkMainPage.objects.filter(slug="home").count(), 1)


class RetiredContentReportTests(CapabilityFrameworkCommandTestCase):
    """What the import says about content the exports have stopped publishing.

    The import matches on slug and never deletes, so a rename in the source
    arrives as an addition and the old role stays live. The difference has to
    be reported or the retired content sits in the navigation unnoticed.
    """

    def setUp(self):
        super().setUp()
        self._import()

    def _rename_in_source(self):
        """Publish the exports again with a role and a skill renamed."""
        renamed = {
            "Data architect": "Data and platform architect",
            "Strategic data planning": "Strategic data and AI planning",
        }
        for name in ("roles.csv", "skills.csv"):
            path = self.data_dir / name
            text = path.read_text()
            for old, new in renamed.items():
                text = text.replace(old, new)
            path.write_text(text)

    def test_a_rename_in_the_source_is_reported_as_a_retirement(self):
        self._rename_in_source()

        output = self._import()

        self.assertIn("In the CMS but not in this file: 1 role and 1 skill.", output)
        self.assertIn("role : data-architect", output)
        self.assertIn("skill: strategic-data-planning", output)

    def test_the_retired_content_is_reported_and_not_deleted(self):
        self._rename_in_source()

        self._import()

        self.assertTrue(GovukRole.objects.filter(slug="data-architect").exists())
        self.assertTrue(GovukSkill.objects.filter(slug="strategic-data-planning").exists())
        # The rename arrives as an addition: the old role snippet and the new
        # one both exist and both are served as routes under the main page.
        self.assertTrue(
            GovukRole.objects.filter(slug="data-and-platform-architect").exists()
        )
        self.assertEqual(GovukRole.objects.count(), 4)

    def test_a_source_that_still_publishes_everything_reports_nothing(self):
        output = self._import()

        self.assertNotIn("In the CMS but not in this file", output)

    def test_a_skill_an_editor_added_by_hand_is_named_too(self):
        """It is in the CMS and not in the file, and whoever runs the refresh
        should know the exports will never account for it."""
        GovukSkill.objects.create(title="Draft skill")

        output = self._import()

        self.assertIn("In the CMS but not in this file: 1 skill.", output)
        self.assertIn("skill: draft-skill", output)


class ExportCapabilityFrameworkTests(CapabilityFrameworkCommandTestCase):
    def setUp(self):
        super().setUp()
        self._import()
        self._export()

    def _rows(self, name: str) -> list[dict]:
        # utf-8-sig: the export carries a byte order mark so Excel opens it
        # as UTF-8, and a reader strips it the way the import command does.
        with open(self.out_dir / name, encoding="utf-8-sig") as f:
            return list(csv.DictReader(f))

    def test_exports_every_skill_with_round_tripped_levels(self):
        rows = {row["Skill Name"]: row for row in self._rows("skills.csv")}

        self.assertEqual(set(rows), {"Data modelling", "Strategic data planning"})
        self.assertEqual(
            rows["Data modelling"]["Working"],
            "You can:\n- produce data models\n- reverse-engineer models",
        )
        self.assertEqual(
            rows["Data modelling"]["Skill Description"], "Producing data models."
        )

    def test_roles_that_require_a_skill_are_derived_from_role_content(self):
        rows = {row["Skill Name"]: row for row in self._rows("skills.csv")}

        self.assertEqual(
            rows["Data modelling"]["Roles that require Skill"],
            "Data architect, Data engineer",
        )

    def test_exports_one_row_per_role_level_and_skill(self):
        rows = self._rows("roles.csv")
        engineer_rows = [r for r in rows if r["Role"] == "Data engineer"]

        self.assertEqual(len(engineer_rows), 2)
        self.assertEqual(
            {r["Role Level"] for r in engineer_rows},
            {"Data engineer", "Senior data engineer"},
        )
        self.assertEqual(engineer_rows[0]["Skill Level"], "Working")

    def test_scs_roles_export_one_row_per_skill_with_levels_not_in_use(self):
        rows = self._rows("roles.csv")
        cdo_rows = [r for r in rows if r["Role"] == "Chief data officer"]

        self.assertEqual(len(cdo_rows), 1)
        row = cdo_rows[0]
        self.assertEqual(row["Role Type"], "Senior Civil Service")
        self.assertEqual(row["Role Level"], "NOT IN USE")
        self.assertEqual(row["Skill Level"], "NOT IN USE")
        self.assertEqual(row["Skill Name"], "Strategic data planning")
        self.assertIn("set a data strategy", row["Skill Description"])

    def test_role_family_is_exported(self):
        rows = self._rows("roles.csv")
        families = {r["Role"]: r["Role Family"] for r in rows}

        self.assertEqual(families["Data engineer"], "Data")
        self.assertEqual(families["Chief data officer"], "Chief digital and data")

    def test_exports_changelog_with_page_names(self):
        rows = self._rows("changelog.csv")
        pages = {row["Page"] for row in rows}

        self.assertEqual(pages, {"Homepage", "Data engineer"})
        note = next(r["Change note"] for r in rows if r["Page"] == "Homepage")
        self.assertEqual(
            note, "[Data engineer](/role/data-engineer) has an updated list of skills."
        )

    def test_unpublished_changelog_entries_are_not_exported(self):
        GovukChangelogEntry.objects.update(live=False)
        self._export()

        self.assertEqual(self._rows("changelog.csv"), [])


class ParsePointsTests(SimpleTestCase):
    def test_parses_the_points_out_of_a_level_cell(self):
        for text, points in (
            ("You can:\n- do a thing\n- do another thing", ["do a thing", "do another thing"]),
            ("", []),
            (NOT_IN_USE, []),
            # Unbulleted text becomes a single point.
            (LEVEL_NOT_DEFINED, [LEVEL_NOT_DEFINED]),
        ):
            with self.subTest(text=text):
                self.assertEqual(parse_points(text), points)

    def test_points_are_truncated_to_field_limit(self):
        self.assertEqual(len(parse_points("You can:\n- " + "x" * 900)[0]), 500)


class TextToRichHtmlTests(SimpleTestCase):
    def test_converts_paragraphs_and_bullets(self):
        for text, html in (
            (
                "Intro line.\n\n- first\n- second",
                "<p>Intro line.</p><ul><li>first</li><li>second</li></ul>",
            ),
            ("", ""),
            (NOT_IN_USE, ""),
        ):
            with self.subTest(text=text):
                self.assertEqual(text_to_rich_html(text), html)

    def test_markup_in_source_is_escaped(self):
        self.assertNotIn("<script>", text_to_rich_html("<script>alert(1)</script>"))


class RichHtmlToTextTests(SimpleTestCase):
    def test_round_trips_the_published_prose_convention(self):
        original = (
            "A data engineer builds data products.\n"
            "\n"
            "In this role, you will:\n"
            "- build pipelines\n"
            "- write ETL scripts"
        )
        self.assertEqual(rich_html_to_text(text_to_rich_html(original)), original)

    def test_converts_fragments_entities_and_empty_input(self):
        for html, text in (
            ("just text", "just text"),
            ("<p>Hodgson&#x27;s rule</p>", "Hodgson's rule"),
            ("", ""),
            (None, ""),
        ):
            with self.subTest(html=html):
                self.assertEqual(rich_html_to_text(html), text)


class PointsToTextTests(SimpleTestCase):
    def test_writes_points_as_the_exports_do(self):
        variant = "This skill level is not defined."
        mention = "This skill level is not defined by the role's seniority alone"
        for points, text in (
            (["first", "second"], "You can:\n- first\n- second"),
            # The placeholder is written without prefix or bullet, in either
            # wording: the published export carries both.
            ([LEVEL_NOT_DEFINED], LEVEL_NOT_DEFINED),
            ([variant], variant),
            # A real point that merely mentions the words is left alone.
            ([mention], f"You can:\n- {mention}"),
            # Beside real points the placeholder is a sentence in a list.
            (
                [LEVEL_NOT_DEFINED, "do a real thing"],
                f"You can:\n- {LEVEL_NOT_DEFINED}\n- do a real thing",
            ),
            ([], ""),
        ):
            with self.subTest(points=points):
                self.assertEqual(points_to_text(points), text)

    def test_round_trips_with_parse_points(self):
        original = "You can:\n- alpha\n- beta"
        self.assertEqual(points_to_text(parse_points(original)), original)


class ChangelogConversionTests(SimpleTestCase):
    def test_note_round_trips_through_html(self):
        note = "[Data engineer](/role/data-engineer) has updated skills.\nA second change."
        self.assertEqual(changelog_html_to_note(changelog_note_to_html(note)), note)

    def test_a_note_becomes_html(self):
        for note, html in (
            (
                "[Data engineer](/role/data-engineer) has updated skills.",
                '<p><a href="/role/data-engineer">Data engineer</a> has updated skills.</p>',
            ),
            ("[Role](/role/x) changed.", '<p><a href="/role/x">Role</a> changed.</p>'),
            ("First change.\n\nSecond change.", "<p>First change.</p><p>Second change.</p>"),
            (
                "These roles changed:\n- Data analyst\n- Data engineer",
                "<p>These roles changed:</p><ul><li>Data analyst</li><li>Data engineer</li></ul>",
            ),
            # The exports carry a stray "[](/skills)" where a link lost its text.
            ("The skills [](/skills) were updated.", "<p>The skills  were updated.</p>"),
            ("[](/skills)", ""),
            ("", ""),
            ("   \n  ", ""),
        ):
            with self.subTest(note=note):
                self.assertEqual(changelog_note_to_html(note), html)

    def test_html_in_a_note_is_escaped(self):
        html = changelog_note_to_html("<script>alert(1)</script>")
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("&lt;b&gt;", changelog_note_to_html("<b>bold</b>"))

    def test_empty_html_becomes_an_empty_note(self):
        self.assertEqual(changelog_html_to_note(""), "")


class ChangelogRepairTests(SimpleTestCase):
    """Notes stored before the import understood the exports' conventions."""

    def test_repairs_hyphen_paragraphs_and_empty_links(self):
        untouched = '<p><a href="/role/x">Role</a> changed.</p><ul><li>alpha</li></ul>'
        for html, repaired in (
            (
                "<p>These roles changed:</p><p>- Data analyst</p><p>- Data engineer</p>",
                "<p>These roles changed:</p><ul><li>Data analyst</li><li>Data engineer</li></ul>",
            ),
            # Separate runs stay separate lists.
            (
                "<p>- one</p><p>Then:</p><p>- two</p>",
                "<ul><li>one</li></ul><p>Then:</p><ul><li>two</li></ul>",
            ),
            (
                "<p>The skills [](/skills) were updated.</p>",
                "<p>The skills  were updated.</p>",
            ),
            # A paragraph left empty by the repair is dropped.
            ("<p>Real change.</p><p>[](/skills)</p>", "<p>Real change.</p>"),
            (untouched, untouched),
            ("", ""),
            (None, ""),
        ):
            with self.subTest(html=html):
                self.assertEqual(repair_changelog_html(html), repaired)


class ChangelogNoteScalingTests(SimpleTestCase):
    """A big note must not send the patterns quadratic.

    The 0058 data migration runs these at container boot, so a note that
    backtracks for minutes stops the site coming up.
    """

    # Each took minutes at this size before the patterns were rewritten and
    # runs in milliseconds now, so a second will not flake.
    BUDGET_SECONDS = 1

    def test_pathological_notes_convert_quickly(self):
        for name, convert, value in (
            ("paragraph that never closes", repair_changelog_html, "<p>-" + " " * 64_000),
            ("unterminated markdown links", changelog_note_to_html, "[a](" * 32_000),
            ("unterminated empty markdown links", changelog_note_to_html, "[](" * 64_000),
        ):
            with self.subTest(name):
                started = time.monotonic()
                convert(value)
                self.assertLess(time.monotonic() - started, self.BUDGET_SECONDS)


class ParseIsoDateTests(SimpleTestCase):
    def test_parses_iso_dates(self):
        self.assertEqual(parse_iso_date("2026-05-29"), date(2026, 5, 29))
        self.assertEqual(parse_iso_date("  2020-01-07 "), date(2020, 1, 7))

    def test_parses_timestamps_by_taking_the_date(self):
        # The source column is a "Timestamp"; a future export may carry a time.
        for value in ("2026-05-29T09:00:00", "2026-05-29 09:00:00", "2026-05-29T09:00:00Z"):
            with self.subTest(value=value):
                self.assertEqual(parse_iso_date(value), date(2026, 5, 29))

    def test_returns_none_for_invalid_dates(self):
        self.assertIsNone(parse_iso_date(""))
        self.assertIsNone(parse_iso_date("29/05/2026"))
