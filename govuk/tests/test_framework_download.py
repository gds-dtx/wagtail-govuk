"""The framework's three CSVs, downloadable from the site itself.

The live service linked its downloads to files regenerated on a schedule in
an S3 bucket, and the links now answer AccessDenied. These are built from
the published content at the moment of asking, so what a reader downloads is
what the site says, always.
"""

import csv
import io

from django.test import TestCase, override_settings
from django.urls import reverse

from govuk.framework_csv import CHANGELOG_COLUMNS, ROLE_COLUMNS, SKILL_COLUMNS
from govuk.models import GovukChangelogEntry, GovukRole, GovukSkill


def _feature_flags(*, skills_enabled: bool = True) -> dict[str, bool]:
    return {
        "SKILLS": skills_enabled,
        "ORGANISATIONS": False,
        "PEOPLE_FINDER": False,
        "FEEDBACK": False,
    }


@override_settings(FEATURE_FLAGS=_feature_flags())
class FrameworkCsvDownloadTests(TestCase):
    def setUp(self):
        self.skill = GovukSkill.objects.create(
            title="Prototyping",
            body="<p>Building throwaway versions to test an idea.</p>",
            working_points=[{"type": "point", "value": "build a prototype"}],
        )
        self.role = GovukRole.objects.create(
            title="Interaction designer",
            family="User-centred design",
            body="<p>Designs interactions.</p>",
            levels=[
                {
                    "type": "level",
                    "value": {
                        "title": "Senior interaction designer",
                        "description": "<p>Leads design.</p>",
                        "skills": [{"skill": self.skill.pk, "level": "working"}],
                    },
                }
            ],
        )
        GovukChangelogEntry.objects.create(
            date="2026-08-01", note="<p>Prototyping guidance updated</p>", skill=self.skill
        )

    def _rows(self, name):
        response = self.client.get(reverse("govuk_framework_csv", args=[name]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertIn(".csv", response["Content-Disposition"])
        return list(csv.reader(io.StringIO(response.content.decode())))

    def test_the_roles_csv_carries_the_published_columns_and_the_role(self):
        rows = self._rows("roles")

        self.assertEqual(rows[0], ROLE_COLUMNS)
        data = {tuple(row[:2]) for row in rows[1:]}
        self.assertIn(("User-centred design", "Interaction designer"), data)

    def test_the_skills_csv_carries_the_published_columns_and_the_skill(self):
        rows = self._rows("skills")

        self.assertEqual(rows[0], SKILL_COLUMNS)
        by_name = {row[0]: row for row in rows[1:]}
        self.assertIn("Prototyping", by_name)
        # The roles-that-require column comes from the content model, so it is
        # in step with the role created above by construction.
        self.assertIn("Interaction designer", by_name["Prototyping"][-1])

    def test_the_changelog_csv_names_the_skill_the_entry_belongs_to(self):
        rows = self._rows("changelog")

        self.assertEqual(rows[0], CHANGELOG_COLUMNS)
        self.assertIn(["2026-08-01", "Prototyping", "Prototyping guidance updated"], rows[1:])

    def test_a_download_is_the_content_at_the_moment_of_asking(self):
        before = self._rows("skills")
        GovukSkill.objects.create(title="Zonal networking", body="<p>New skill.</p>")

        after = self._rows("skills")

        self.assertEqual(len(after), len(before) + 1)
        self.assertIn("Zonal networking", {row[0] for row in after[1:]})

    def test_a_name_that_is_not_a_download_is_not_found(self):
        response = self.client.get("/download/passwords.csv")
        self.assertEqual(response.status_code, 404)

    @override_settings(FEATURE_FLAGS=_feature_flags(skills_enabled=False))
    def test_without_the_skills_feature_there_is_no_download(self):
        response = self.client.get(reverse("govuk_framework_csv", args=["roles"]))
        self.assertEqual(response.status_code, 404)

    def test_the_command_and_the_download_write_the_same_file(self):
        import tempfile, pathlib
        from django.core.management import call_command

        with tempfile.TemporaryDirectory() as outdir:
            call_command("export_capability_framework", outdir, stdout=io.StringIO())
            # Bytes, not read_text(): text mode would fold the \r\n line
            # endings the csv module writes and hide a real difference.
            on_disk = (pathlib.Path(outdir) / "skills.csv").read_bytes()

        response = self.client.get(reverse("govuk_framework_csv", args=["skills"]))
        self.assertEqual(response.content, on_disk)
