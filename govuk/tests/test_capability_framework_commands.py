import csv
import tempfile
from datetime import date
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase, override_settings
from wagtail.models import Site

from govuk.management.commands.import_capability_framework import SITE_NAME
from govuk.models import (
    ContentPage,
    GovukChangelogEntry,
    GovukRole,
    GovukSkill,
    RolePage,
    SkillsAZPage,
)

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


def _feature_flags(*, skills_enabled: bool = True) -> dict[str, bool]:
    return {
        "SKILLS": skills_enabled,
        "ORGANISATIONS": False,
        "PEOPLE_FINDER": False,
        "FEEDBACK": False,
    }


def _write_fixtures(directory: Path, *, include_changelog: bool = True):
    (directory / "roles.csv").write_text(ROLES_CSV)
    (directory / "skills.csv").write_text(SKILLS_CSV)
    if include_changelog:
        (directory / "changelog.csv").write_text(CHANGELOG_CSV)


@override_settings(FEATURE_FLAGS=_feature_flags())
class ImportCapabilityFrameworkTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data_dir = Path(self.tmp.name)
        _write_fixtures(self.data_dir)
        # the command attaches pages to the site's home page
        self.root_page = Site.objects.get(is_default_site=True).root_page.specific

    def _import(self) -> str:
        out = StringIO()
        call_command("import_capability_framework", str(self.data_dir), stdout=out)
        return out.getvalue()

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

    def test_creates_a_published_page_per_role_and_a_skills_index(self):
        self._import()

        self.assertEqual(RolePage.objects.live().count(), 3)
        page = RolePage.objects.get(slug="data-engineer")
        self.assertTrue(page.live)
        self.assertEqual(SkillsAZPage.objects.live().count(), 1)

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
        self.assertEqual(RolePage.objects.count(), 3)
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
        self.assertIsInstance(home, ContentPage)
        self.assertEqual(home.slug, "home")
        self.assertEqual(site.site_name, SITE_NAME)

    def test_home_page_lists_every_role_grouped_by_family(self):
        self._import()

        home = Site.objects.get(is_default_site=True).root_page.specific
        self.assertIn("Data roles", home.body)
        self.assertIn("Chief digital and data roles", home.body)
        for slug in ("data-engineer", "data-architect", "chief-data-officer"):
            self.assertIn(RolePage.objects.get(slug=slug).url, home.body)
        self.assertIn("Skills A to Z", home.body)

    def test_home_page_carries_the_welcome_content(self):
        self._import()

        home = Site.objects.get(is_default_site=True).root_page.specific
        self.assertIn("Learn about the digital, data", home.hero_intro)
        for heading in (
            "How to use this framework",
            "Skills in this framework",
            "Job grades in this framework",
            "Support",
        ):
            self.assertIn(heading, home.body)
        self.assertIn(SkillsAZPage.objects.first().url, home.body)
        self.assertTrue(home.show_role_navigation)
        self.assertTrue(home.show_framework_updates)

    def test_home_page_role_lists_are_for_narrow_screens_only(self):
        """Wide screens reach the roles through the side navigation instead."""
        self._import()

        home = Site.objects.get(is_default_site=True).root_page.specific
        role_list_start = home.body.index("Data roles</h2>")
        mobile_start = home.body.index('<div class="mobile-homepage mobile-homepage-roles">')

        self.assertLess(mobile_start, role_list_start)
        self.assertIn('href="#data-roles"', home.body)

    def test_home_page_is_reachable_and_links_resolve(self):
        self._import()

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("Data engineer", body)
        self.assertEqual(self.client.get("/data-engineer/").status_code, 200)

    def test_second_import_keeps_the_same_home_page(self):
        self._import()
        first = Site.objects.get(is_default_site=True).root_page_id
        self._import()

        self.assertEqual(Site.objects.get(is_default_site=True).root_page_id, first)
        self.assertEqual(ContentPage.objects.filter(slug="home").count(), 1)


@override_settings(FEATURE_FLAGS=_feature_flags())
class ExportCapabilityFrameworkTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data_dir = Path(self.tmp.name) / "in"
        self.data_dir.mkdir()
        self.out_dir = Path(self.tmp.name) / "out"
        _write_fixtures(self.data_dir)
        call_command(
            "import_capability_framework", str(self.data_dir), stdout=StringIO()
        )
        call_command(
            "export_capability_framework", str(self.out_dir), stdout=StringIO()
        )

    def _rows(self, name: str) -> list[dict]:
        with open(self.out_dir / name) as f:
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
        call_command(
            "export_capability_framework", str(self.out_dir), stdout=StringIO()
        )

        self.assertEqual(self._rows("changelog.csv"), [])
