from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from wagtail.models import Page, Site

from govuk.models import (
    ContentPage,
    GovukChangelogEntry,
    GovukRole,
    GovukSkill,
    RolePage,
    SectionPage,
)
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

    def test_export_carries_every_skill_field(self):
        payload = self._export()
        exported = {row["slug"]: row for row in payload["skills"]}

        self.assertTrue(exported["capability-building"]["is_senior_civil_service"])
        self.assertEqual(
            [
                point["value"]
                for point in exported["capability-building"]["leadership_points"]
            ],
            ["prioritising needs"],
        )
        self.assertFalse(exported["data-modelling"]["is_senior_civil_service"])

    def test_import_restores_a_wiped_senior_civil_service_skill(self):
        payload = self._export()

        self.leadership_skill.is_senior_civil_service = False
        self.leadership_skill.leadership_points = []
        self.leadership_skill.save()

        result = import_pages_from_payload(
            payload=payload, site=self.site, user=self.user
        )
        self.assertEqual(result.errors, [])

        skill = GovukSkill.objects.get(slug="capability-building")
        self.assertTrue(skill.is_senior_civil_service)
        self.assertEqual(skill.get_leadership_points(), ["prioritising needs"])

    def test_export_carries_the_site_name(self):
        self.site.site_name = "Capability Framework"
        self.site.save(update_fields=["site_name"])

        self.assertEqual(self._export()["site"]["site_name"], "Capability Framework")

    def test_import_restores_the_site_name_but_leaves_the_hostname_alone(self):
        self.site.site_name = "Capability Framework"
        self.site.save(update_fields=["site_name"])
        payload = self._export()

        self.site.site_name = ""
        self.site.hostname = "somewhere-else.example.com"
        self.site.save(update_fields=["site_name", "hostname"])

        import_pages_from_payload(payload=payload, site=self.site, user=self.user)

        self.site.refresh_from_db()
        self.assertEqual(self.site.site_name, "Capability Framework")
        self.assertEqual(self.site.hostname, "somewhere-else.example.com")

    def test_export_carries_a_role_pages_role_as_a_slug(self):
        page = self._add_role_page()

        payload = build_page_export_payload(
            site=self.site,
            pages=[page],
            skills=list(GovukSkill.objects.all()),
            roles=list(GovukRole.objects.all()),
        )

        self.assertEqual(
            [
                block["value"]
                for block in payload["pages"][0]["fields"]["selected_roles"]
            ],
            ["data-analyst"],
        )

    def test_a_role_page_follows_its_role_when_the_primary_keys_differ(self):
        """The database being imported into numbers its rows its own way."""
        page = self._add_role_page()
        payload = build_page_export_payload(
            site=self.site,
            pages=[page],
            skills=list(GovukSkill.objects.all()),
            roles=list(GovukRole.objects.all()),
        )

        renumbered_pk = self.role.pk + 1000
        self.role.delete()
        GovukRole.objects.create(pk=renumbered_pk, title="Data analyst", family="Data")

        result = import_pages_from_payload(
            payload=payload, site=self.site, user=self.user
        )
        self.assertEqual(result.errors, [])

        imported_page = RolePage.objects.get(slug="data-analyst")
        self.assertEqual(
            [role.pk for role in imported_page.get_selected_roles()], [renumbered_pk]
        )

    def test_a_role_page_reports_a_role_that_is_not_in_the_destination(self):
        page = self._add_role_page()
        payload = build_page_export_payload(
            site=self.site,
            pages=[page],
            skills=list(GovukSkill.objects.all()),
            roles=list(GovukRole.objects.all()),
        )
        payload["roles"] = []
        self.role.delete()

        result = import_pages_from_payload(
            payload=payload, site=self.site, user=self.user
        )

        self.assertEqual(
            result.errors,
            [
                "Page 'data-analyst' dropped role 'data-analyst' because no "
                "Role has that slug."
            ],
        )
        imported_page = RolePage.objects.get(slug="data-analyst")
        self.assertEqual(imported_page.get_selected_roles(), [])

    def test_a_payload_carrying_primary_keys_is_refused_rather_than_guessed_at(self):
        """Keys from another database point at whichever role holds the number."""
        page = self._add_role_page()
        payload = build_page_export_payload(
            site=self.site,
            pages=[page],
            skills=list(GovukSkill.objects.all()),
            roles=list(GovukRole.objects.all()),
        )
        # An export taken before roles were carried as slugs.
        payload["pages"][0]["fields"]["selected_roles"] = [
            {"type": "role", "value": self.scs_role.pk}
        ]

        result = import_pages_from_payload(
            payload=payload, site=self.site, user=self.user
        )

        self.assertEqual(len(result.errors), 1)
        self.assertIn("dropped role", result.errors[0])
        imported_page = RolePage.objects.get(slug="data-analyst")
        self.assertEqual(imported_page.get_selected_roles(), [])

    def test_importing_a_whole_site_twice_does_not_nest_a_second_copy(self):
        """Re-importing is how dev content gets refreshed."""
        self._add_role_page()
        home = self.site.root_page.specific
        payload = build_page_export_payload(
            site=self.site,
            pages=[home],
            skills=list(GovukSkill.objects.all()),
            roles=list(GovukRole.objects.all()),
        )

        for _ in range(2):
            result = import_pages_from_payload(
                payload=payload, site=self.site, user=self.user
            )
            self.assertEqual(result.errors, [])

        self.site.refresh_from_db()
        self.assertEqual(self.site.root_page.pk, home.pk)
        self.assertEqual(
            [child.slug for child in home.get_children()], ["data-analyst"]
        )

    def _framework_payload(self, *, children: list | None = None) -> dict:
        """A payload rooted at a ContentPage home, as the framework's export is."""
        payload = build_page_export_payload(
            site=self.site,
            pages=[],
            skills=list(GovukSkill.objects.all()),
            roles=list(GovukRole.objects.all()),
        )
        payload["pages"] = [
            {
                "model": "govuk.ContentPage",
                "settings": {"title": "Capability Framework", "slug": "home"},
                "fields": {"body": "<p>The framework.</p>"},
                "tags": [],
                "privacy": [],
                "children": children or [],
            }
        ]
        return payload

    def test_a_placeholder_home_page_is_replaced_rather_than_nested_under(self):
        """A new instance ships an empty home page of the wrong type."""
        placeholder = self.site.root_page.specific
        self.assertIsInstance(placeholder, SectionPage)

        payload = self._framework_payload(
            children=[
                {
                    "model": "govuk.RolePage",
                    "settings": {"title": "Data analyst", "slug": "data-analyst"},
                    "fields": {
                        "selected_roles": [{"type": "role", "value": "data-analyst"}]
                    },
                    "tags": [],
                    "privacy": [],
                    "children": [],
                }
            ]
        )

        result = import_pages_from_payload(
            payload=payload, site=self.site, user=self.user
        )
        self.assertEqual(result.errors, [])
        self.assertEqual(len(result.notes), 1)

        self.site.refresh_from_db()
        new_root = self.site.root_page.specific
        self.assertIsInstance(new_root, ContentPage)
        self.assertEqual(new_root.slug, "home")
        self.assertEqual(new_root.depth, 2)
        # The framework sits at the top level, not under /home/.
        self.assertEqual([child.slug for child in new_root.get_children()], ["data-analyst"])
        self.assertEqual(RolePage.objects.get(slug="data-analyst").url, "/data-analyst/")
        self.assertFalse(Page.objects.filter(pk=placeholder.pk).exists())

    def test_a_home_page_with_children_is_left_alone(self):
        """Only an empty placeholder is safe to throw away."""
        placeholder = self.site.root_page.specific
        self._add_role_page()

        result = import_pages_from_payload(
            payload=self._framework_payload(), site=self.site, user=self.user
        )

        self.site.refresh_from_db()
        self.assertEqual(self.site.root_page.pk, placeholder.pk)
        self.assertEqual(result.notes, [])

    def _add_role_page(self) -> RolePage:
        page = self.site.root_page.add_child(
            instance=RolePage(
                title="Data analyst",
                slug="data-analyst",
                selected_roles=[{"type": "role", "value": self.role.pk}],
            )
        )
        page.save_revision().publish()
        return page.specific

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
