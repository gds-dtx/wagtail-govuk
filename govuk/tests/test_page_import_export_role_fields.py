from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from wagtail.models import Site

from govuk.models import GovukChangelogEntry, GovukRole, GovukSkill
from govuk.page_import_export import (
    build_page_export_payload,
    import_pages_from_payload,
)


def _feature_flags(*, skills_enabled: bool = True) -> dict[str, bool]:
    return {
        "SKILLS": skills_enabled,
        "ORGANISATIONS": False,
        "PEOPLE_FINDER": False,
        "FEEDBACK": False,
    }


@override_settings(FEATURE_FLAGS=_feature_flags())
class RoleExportImportFieldTests(TestCase):
    """The export has to carry every field, or a transfer silently loses content."""

    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.user = get_user_model().objects.create_superuser(
            username="importer",
            email="importer@example.com",
            password="password",
        )

        self.skill = GovukSkill.objects.create(
            title="Data modelling",
            working_points=[{"type": "point", "value": "produce data models"}],
        )
        self.leadership_skill = GovukSkill.objects.create(
            title="Capability building",
            is_senior_civil_service=True,
            leadership_points=[{"type": "point", "value": "prioritising needs"}],
        )

        self.role = GovukRole.objects.create(
            title="Data analyst",
            family="Data",
            levels=[
                {
                    "type": "level",
                    "value": {
                        "title": "Associate data analyst",
                        "description": "",
                        "grades": ["eo", "heo"],
                        "skills": [{"skill": self.skill.pk, "level": "working"}],
                    },
                }
            ],
        )
        self.scs_role = GovukRole.objects.create(
            title="Chief technology officer",
            family="Chief digital and data",
            is_senior_civil_service=True,
            scs_grades=[{"type": "grade", "value": "scs1"}],
            scs_skills=[{"type": "skill", "value": self.leadership_skill.pk}],
        )

        GovukChangelogEntry.objects.create(
            date="2026-04-01",
            role=self.role,
            change_type="Skills updated",
            note="<p>Added data modelling.</p>",
        )
        GovukChangelogEntry.objects.create(
            date="2026-03-01",
            note="<p>Framework wide update.</p>",
        )

    def _export(self) -> dict:
        return build_page_export_payload(
            site=self.site,
            pages=[],
            skills=list(GovukSkill.objects.all()),
            roles=list(GovukRole.objects.all()),
        )

    def test_export_carries_every_role_field(self):
        payload = self._export()
        exported = {row["slug"]: row for row in payload["roles"]}

        self.assertEqual(exported["data-analyst"]["family"], "Data")
        self.assertEqual(
            exported["data-analyst"]["levels"][0]["grades"], ["eo", "heo"]
        )
        self.assertTrue(
            exported["chief-technology-officer"]["is_senior_civil_service"]
        )
        self.assertEqual(
            exported["chief-technology-officer"]["scs_grades"], ["scs1"]
        )
        self.assertEqual(
            exported["chief-technology-officer"]["scs_skills"],
            ["capability-building"],
        )

    def test_export_carries_changelog_entries(self):
        payload = self._export()
        exported = {row["slug"]: row for row in payload["roles"]}

        self.assertEqual(
            [entry["change_type"] for entry in exported["data-analyst"]["changelog"]],
            ["Skills updated"],
        )
        self.assertEqual(len(payload["changelog"]), 1)
        self.assertIn("Framework wide", payload["changelog"][0]["note"])

    def test_import_restores_fields_that_were_wiped(self):
        payload = self._export()

        self.role.family = ""
        self.role.levels = []
        self.role.save()
        self.scs_role.is_senior_civil_service = False
        self.scs_role.scs_grades = []
        self.scs_role.scs_skills = []
        self.scs_role.save()
        GovukChangelogEntry.objects.all().delete()

        result = import_pages_from_payload(
            payload=payload, site=self.site, user=self.user
        )
        self.assertEqual(result.errors, [])

        role = GovukRole.objects.get(slug="data-analyst")
        self.assertEqual(role.family, "Data")
        self.assertEqual(
            role.get_levels_with_skills()[0]["grades"],
            ["EO (Executive Officer)", "HEO (Higher Executive Officer)"],
        )

        scs_role = GovukRole.objects.get(slug="chief-technology-officer")
        self.assertTrue(scs_role.is_senior_civil_service)
        self.assertEqual(
            scs_role.get_scs_grade_labels(), ["SCS 1 (Senior Civil Service 1)"]
        )
        self.assertEqual(len(scs_role.get_scs_skills()), 1)

        self.assertEqual(GovukChangelogEntry.objects.filter(role=role).count(), 1)
        self.assertEqual(
            GovukChangelogEntry.objects.filter(role=None, skill=None).count(), 1
        )

    def test_importing_twice_does_not_duplicate_changelog_entries(self):
        payload = self._export()

        for _ in range(2):
            import_pages_from_payload(payload=payload, site=self.site, user=self.user)

        self.assertEqual(GovukChangelogEntry.objects.count(), 2)

    def test_unknown_grade_keys_are_dropped_rather_than_failing_the_import(self):
        payload = self._export()
        for row in payload["roles"]:
            if row["slug"] == "data-analyst":
                row["levels"][0]["grades"] = ["eo", "not-a-grade"]
            if row["slug"] == "chief-technology-officer":
                row["scs_grades"] = ["scs1", "heo"]

        result = import_pages_from_payload(
            payload=payload, site=self.site, user=self.user
        )
        self.assertEqual(result.errors, [])

        role = GovukRole.objects.get(slug="data-analyst")
        self.assertEqual(
            role.get_levels_with_skills()[0]["grades"], ["EO (Executive Officer)"]
        )
        # HEO is a real grade, but not one a Senior Civil Service role can hold.
        scs_role = GovukRole.objects.get(slug="chief-technology-officer")
        self.assertEqual(
            scs_role.get_scs_grade_labels(), ["SCS 1 (Senior Civil Service 1)"]
        )
