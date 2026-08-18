from unittest.mock import patch

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

    def test_export_carries_the_roles_that_could_lead_to_a_role_as_slugs(self):
        self.scs_role.roles_that_could_lead_here = [
            {"type": "role", "value": self.role.pk}
        ]
        self.scs_role.save()

        payload = self._export()
        exported = {row["slug"]: row for row in payload["roles"]}

        self.assertEqual(
            exported["chief-technology-officer"]["roles_that_could_lead_here"],
            ["data-analyst"],
        )

    def test_import_restores_a_wiped_progression_mapping(self):
        self.scs_role.roles_that_could_lead_here = [
            {"type": "role", "value": self.role.pk}
        ]
        self.scs_role.save()
        payload = self._export()

        self.scs_role.roles_that_could_lead_here = []
        self.scs_role.save()

        result = import_pages_from_payload(
            payload=payload, site=self.site, user=self.user
        )
        self.assertEqual(result.errors, [])

        scs_role = GovukRole.objects.get(slug="chief-technology-officer")
        self.assertEqual(
            [role.slug for role in scs_role.get_roles_that_could_lead_here()],
            ["data-analyst"],
        )

    def test_a_role_can_name_one_that_is_imported_after_it(self):
        """Roles arrive in one pass, so the references are resolved in a second."""
        payload = self._export()
        exported = {row["slug"]: row for row in payload["roles"]}
        exported["chief-technology-officer"]["roles_that_could_lead_here"] = [
            "data-analyst"
        ]
        # Chief technology officer is imported first, before data analyst exists.
        payload["roles"] = [
            exported["chief-technology-officer"],
            exported["data-analyst"],
        ]
        GovukRole.objects.all().delete()

        result = import_pages_from_payload(
            payload=payload, site=self.site, user=self.user
        )
        self.assertEqual(result.errors, [])

        scs_role = GovukRole.objects.get(slug="chief-technology-officer")
        self.assertEqual(
            [role.slug for role in scs_role.get_roles_that_could_lead_here()],
            ["data-analyst"],
        )

    def test_a_rejected_role_keeps_the_progression_mapping_it_already_had(self):
        """The references are resolved in a second pass, which must not carry
        over a payload the first pass judged invalid and refused to save."""
        other_role = GovukRole.objects.create(title="Data engineer", family="Data")
        self.scs_role.roles_that_could_lead_here = [
            {"type": "role", "value": other_role.pk}
        ]
        self.scs_role.save()

        payload = self._export()
        for row in payload["roles"]:
            if row["slug"] == "chief-technology-officer":
                # Longer than the field allows, so full_clean rejects it.
                row["title"] = "C" * 300
                row["roles_that_could_lead_here"] = ["data-analyst"]

        result = import_pages_from_payload(
            payload=payload, site=self.site, user=self.user
        )

        self.assertEqual(result.skipped, 1)
        scs_role = GovukRole.objects.get(slug="chief-technology-officer")
        self.assertEqual(
            [role.slug for role in scs_role.get_roles_that_could_lead_here()],
            [other_role.slug],
        )

    def test_a_role_with_no_progression_mapping_is_not_written_a_second_time(self):
        """Almost every role in the framework names none, and the second pass
        would otherwise rewrite an unchanged empty field on each of them."""
        payload = self._export()
        for row in payload["roles"]:
            self.assertEqual(row["roles_that_could_lead_here"], [])

        # The second pass is the only thing that saves that field on its own.
        saved = []
        original_save = GovukRole.save

        def record(role, *args, update_fields=None, **kwargs):
            if update_fields == ["roles_that_could_lead_here"]:
                saved.append(role.slug)
            return original_save(role, *args, update_fields=update_fields, **kwargs)

        with patch.object(GovukRole, "save", record):
            result = import_pages_from_payload(
                payload=payload, site=self.site, user=self.user
            )

        self.assertEqual(result.errors, [])
        self.assertEqual(saved, [])

    def test_a_file_written_before_the_field_existed_leaves_the_mapping_alone(self):
        """The known-good exports kept for staging and production were written
        without this field. Reading their silence as "empty it" would clear the
        curated path off every senior role, and report nothing."""
        self.scs_role.roles_that_could_lead_here = [
            {"type": "role", "value": self.role.pk}
        ]
        self.scs_role.save()
        payload = self._export()
        for row in payload["roles"]:
            row.pop("roles_that_could_lead_here", None)

        result = import_pages_from_payload(
            payload=payload, site=self.site, user=self.user
        )
        self.assertEqual(result.errors, [])

        scs_role = GovukRole.objects.get(slug="chief-technology-officer")
        self.assertEqual(
            [role.slug for role in scs_role.get_roles_that_could_lead_here()],
            ["data-analyst"],
        )

    def test_an_empty_list_still_empties_the_mapping(self):
        """Said outright rather than left out, which is what an export of a
        role an editor has cleared carries."""
        self.scs_role.roles_that_could_lead_here = [
            {"type": "role", "value": self.role.pk}
        ]
        self.scs_role.save()
        payload = self._export()
        for row in payload["roles"]:
            if row["slug"] == "chief-technology-officer":
                row["roles_that_could_lead_here"] = []

        result = import_pages_from_payload(
            payload=payload, site=self.site, user=self.user
        )
        self.assertEqual(result.errors, [])

        scs_role = GovukRole.objects.get(slug="chief-technology-officer")
        self.assertEqual(scs_role.get_roles_that_could_lead_here(), [])

    def test_a_mapping_that_is_not_an_array_is_reported_rather_than_obeyed(self):
        self.scs_role.roles_that_could_lead_here = [
            {"type": "role", "value": self.role.pk}
        ]
        self.scs_role.save()
        payload = self._export()
        for row in payload["roles"]:
            if row["slug"] == "chief-technology-officer":
                row["roles_that_could_lead_here"] = "data-analyst"

        result = import_pages_from_payload(
            payload=payload, site=self.site, user=self.user
        )

        self.assertEqual(
            result.errors,
            [
                "Role 'chief-technology-officer' kept its existing progression "
                "roles because 'roles_that_could_lead_here' is not an array."
            ],
        )
        scs_role = GovukRole.objects.get(slug="chief-technology-officer")
        self.assertEqual(
            [role.slug for role in scs_role.get_roles_that_could_lead_here()],
            ["data-analyst"],
        )

    def test_a_progression_role_missing_from_the_destination_is_reported(self):
        self.scs_role.roles_that_could_lead_here = [
            {"type": "role", "value": self.role.pk}
        ]
        self.scs_role.save()
        payload = self._export()
        payload["roles"] = [
            row for row in payload["roles"] if row["slug"] != "data-analyst"
        ]
        self.role.delete()

        result = import_pages_from_payload(
            payload=payload, site=self.site, user=self.user
        )

        self.assertEqual(
            result.errors,
            [
                "Role 'chief-technology-officer' skipped a role that could lead "
                "to it for missing role 'data-analyst'."
            ],
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


@override_settings(FEATURE_FLAGS=_feature_flags())
class ImportIntoASiteWithSkillsSwitchedOffTests(TestCase):
    """Importing the framework where FEATURE_SKILLS is unset.

    This is how a new environment goes wrong: the flag is set by the Terraform
    that builds the instance, not by anything in this repository, so a site can
    come up without it and take an import that quietly drops the framework.
    """

    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.user = get_user_model().objects.create_superuser(
            username="importer",
            email="importer@example.com",
            password="password",
        )
        self.role = GovukRole.objects.create(title="Data analyst", family="Data")
        GovukSkill.objects.create(title="Data modelling")
        GovukChangelogEntry.objects.create(
            date="2026-03-01", note="<p>Framework wide update.</p>"
        )
        page = self.site.root_page.add_child(
            instance=RolePage(
                title="Data analyst",
                slug="data-analyst",
                selected_roles=[{"type": "role", "value": self.role.pk}],
            )
        )
        page.save_revision().publish()

        self.payload = build_page_export_payload(
            site=self.site,
            pages=[page.specific],
            skills=list(GovukSkill.objects.all()),
            roles=list(GovukRole.objects.all()),
        )

    def _import_with_skills_off(self):
        GovukRole.objects.all().delete()
        GovukSkill.objects.all().delete()
        GovukChangelogEntry.objects.all().delete()
        RolePage.objects.all().delete()
        with override_settings(FEATURE_FLAGS=_feature_flags(skills_enabled=False)):
            return import_pages_from_payload(
                payload=self.payload, site=self.site, user=self.user
            )

    def test_the_flag_is_named_rather_than_the_file_being_blamed(self):
        result = self._import_with_skills_off()

        self.assertEqual(len(result.errors), 1)
        self.assertIn("FEATURE_SKILLS", result.errors[0])
        self.assertIn("skills, roles and changelog", result.errors[0])

    def test_the_pages_still_arrive(self):
        """Reporting the problem must not turn into refusing the import: the
        pages are the bulk of the file and they transfer perfectly well."""
        result = self._import_with_skills_off()

        self.assertEqual(result.skipped, 0)
        self.assertTrue(RolePage.objects.filter(slug="data-analyst").exists())

    def test_each_page_does_not_repeat_the_same_cause(self):
        """Every role page names a role, so left alone this drowns the one
        message that explains the run in a page-by-page list of symptoms."""
        result = self._import_with_skills_off()

        self.assertEqual(
            [error for error in result.errors if "dropped role" in error], []
        )

    def test_a_file_carrying_no_framework_content_is_not_warned_about(self):
        """A site legitimately running without the framework imports its own
        pages all the time, and has nothing to be told."""
        for key in ("skills", "roles", "changelog"):
            self.payload.pop(key, None)

        result = self._import_with_skills_off()

        self.assertEqual(result.errors, [])
