import csv
import tempfile
from datetime import date
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase, override_settings
from wagtail.models import Site

from govuk.models import (
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

    def test_scs_role_content_is_preserved_in_the_body(self):
        self._import()

        role = GovukRole.objects.get(slug="chief-data-officer")
        self.assertEqual(role.get_levels_with_skills(), [])
        self.assertIn("Strategic data planning", role.body)
        self.assertIn("set a data strategy", role.body)

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

    def test_roles_without_levels_still_appear(self):
        rows = self._rows("roles.csv")
        cdo_rows = [r for r in rows if r["Role"] == "Chief data officer"]

        self.assertEqual(len(cdo_rows), 1)
        self.assertEqual(cdo_rows[0]["Role Level"], "")

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
