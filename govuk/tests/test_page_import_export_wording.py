"""The editable wording travelling between environments.

It is a site setting, so nothing selected for export carries it: without this
an edit made in dev would be retyped in staging and again in production, and
the three would be free to drift.
"""

import json

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from wagtail.models import Site

from govuk.models import CapabilityFrameworkWordingSettings, ContentPage
from govuk.page_import_export import (
    FRAMEWORK_WORDING_FIELD_NAMES,
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
class FrameworkWordingExportImportTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.user = get_user_model().objects.create_superuser(
            username="wording-importer",
            email="wording-importer@example.com",
            password="password",
        )
        self.wording = CapabilityFrameworkWordingSettings.for_site(self.site)

    def _export(self) -> dict:
        return build_page_export_payload(
            site=self.site, pages=[], skills=[], roles=[]
        )

    def _import(self, payload: dict):
        return import_pages_from_payload(
            payload=payload, site=self.site, user=self.user
        )

    def _reloaded(self) -> CapabilityFrameworkWordingSettings:
        return CapabilityFrameworkWordingSettings.objects.get(pk=self.wording.pk)

    def test_every_field_on_the_form_is_a_field_that_travels(self):
        """The list is read off the model, so a field added to the form travels
        without anyone remembering to add it here. That holds only while the
        rule the list is read by covers what the form holds: a field of a type
        the rule passes over would be edited in the admin and then quietly left
        behind by every export."""
        on_the_form = [
            child.field_name
            for panel in CapabilityFrameworkWordingSettings.panels
            for child in getattr(panel, "children", [panel])
            if hasattr(child, "field_name")
        ]

        self.assertEqual(sorted(on_the_form), sorted(FRAMEWORK_WORDING_FIELD_NAMES))

    def test_the_export_carries_every_wording_field(self):
        """A field the export leaves out is a field that reverts to its default
        on the other side, which reads as content lost in the transfer."""
        exported = self._export()["wording"]

        self.assertEqual(
            sorted(exported), sorted(FRAMEWORK_WORDING_FIELD_NAMES)
        )
        self.assertEqual(exported["contents_heading"], "Contents")
        # 38 fields under 7 panel groups, which is the whole form.
        self.assertEqual(len(exported), 38)

    def test_an_edit_survives_the_journey_to_another_environment(self):
        self.wording.related_roles_heading = "Roles sharing skills with {role}"
        self.wording.no_skills_message = "There are no skills yet."
        self.wording.save()
        payload = self._export()

        # The environment being imported into, still on the defaults.
        self.wording.related_roles_heading = "Roles that share {role} skills"
        self.wording.no_skills_message = "No skills found."
        self.wording.save()

        result = self._import(payload)

        self.assertEqual(result.errors, [])
        self.assertEqual(
            self._reloaded().related_roles_heading,
            "Roles sharing skills with {role}",
        )
        self.assertEqual(
            self._reloaded().no_skills_message, "There are no skills yet."
        )

    def test_a_file_from_before_the_wording_moved_leaves_it_alone(self):
        """The known-good exports kept for staging and production were written
        against the templates' own wording, and say nothing about it. Reverting
        every heading on import is not what importing one of those files asks
        for."""
        self.wording.updates_heading = "Changes"
        self.wording.save()
        home = self.site.root_page.specific
        page = home.add_child(
            instance=ContentPage(title="Somewhere", slug="somewhere", body="")
        )
        page.save_revision().publish()

        payload = build_page_export_payload(
            site=self.site, pages=[page], skills=[], roles=[]
        )
        payload.pop("wording")
        result = self._import(payload)

        self.assertEqual(result.errors, [])
        self.assertEqual(self._reloaded().updates_heading, "Changes")

    def test_a_field_the_file_does_not_name_keeps_what_this_site_has(self):
        self.wording.published_prefix = "First published"
        self.wording.save()

        result = self._import({"wording": {"updates_heading": "Changes"}})

        self.assertEqual(result.errors, [])
        self.assertEqual(self._reloaded().updates_heading, "Changes")
        self.assertEqual(self._reloaded().published_prefix, "First published")

    def test_wording_alone_is_enough_to_import(self):
        """Pushing a wording change to production is a reason to import on its
        own, and has no page, skill or role to go with it."""
        result = self._import({"wording": {"updates_heading": "Changes"}})

        self.assertEqual(result.errors, [])
        self.assertEqual(result.updated, 1)
        self.assertEqual(self._reloaded().updates_heading, "Changes")

    def test_a_heading_too_long_for_the_column_is_reported_and_the_rest_land(self):
        result = self._import(
            {
                "wording": {
                    "updates_heading": "Changes",
                    "contents_heading": "C" * 256,
                }
            }
        )

        self.assertEqual(len(result.errors), 1)
        self.assertIn("contents_heading", result.errors[0])
        self.assertEqual(self._reloaded().contents_heading, "Contents")
        self.assertEqual(self._reloaded().updates_heading, "Changes")

    def test_wording_given_as_something_other_than_text_is_reported(self):
        """A heading of null would otherwise be stored as the word "None", and
        one given as a list as its Python repr."""
        result = self._import(
            {"wording": {"contents_heading": None, "updates_heading": ["Changes"]}}
        )

        self.assertEqual(len(result.errors), 2)
        self.assertEqual(self._reloaded().contents_heading, "Contents")
        self.assertEqual(self._reloaded().updates_heading, "Updates")

    def test_wording_that_is_not_an_object_is_reported(self):
        result = self._import({"wording": "Contents"})

        self.assertEqual(result.skipped, 1)
        self.assertIn(
            "Payload 'wording' value must be an object when provided.",
            result.errors,
        )

    def test_an_empty_payload_says_wording_is_one_of_the_things_it_could_hold(self):
        result = self._import({})

        self.assertEqual(
            result.errors,
            [
                "Payload must contain at least one entry in 'tags', 'pages', "
                "'skills', 'roles' or 'wording'."
            ],
        )

    def test_a_round_trip_leaves_the_wording_exactly_as_it_was(self):
        for index, field_name in enumerate(FRAMEWORK_WORDING_FIELD_NAMES):
            setattr(self.wording, field_name, f"Wording {index}")
        self.wording.save()
        payload = self._export()

        result = self._import(payload)

        self.assertEqual(result.errors, [])
        reloaded = self._reloaded()
        self.assertEqual(
            {name: getattr(reloaded, name) for name in FRAMEWORK_WORDING_FIELD_NAMES},
            payload["wording"],
        )


@override_settings(FEATURE_FLAGS=_feature_flags())
class WordingOnlyExportViewTests(TestCase):
    """Exporting the wording without carrying a page along with it.

    Everything else on the export form is a page, a skill or a role, and the
    wording is none of those: it is a site setting, so there is nothing to tick
    for it. Ticking nothing is what asks for it on its own, which is what
    applying a wording change in another environment needs.
    """

    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.admin_user = get_user_model().objects.create_superuser(
            username="wording-exporter",
            email="wording-exporter@example.gov.uk",
            password="unused-password",
        )
        self.client.force_login(self.admin_user)
        self.export_url = reverse("govuk_pages_export")
        self.import_url = reverse("govuk_pages_import")
        self.page = self.site.root_page.specific.add_child(
            instance=ContentPage(title="Somewhere", slug="somewhere", body="")
        )
        self.page.save_revision().publish()
        self.wording = CapabilityFrameworkWordingSettings.for_site(self.site)

    def test_selecting_nothing_exports_the_wording_by_itself(self):
        self.wording.updates_heading = "Changes"
        self.wording.save()

        response = self.client.post(self.export_url, data={"site_id": self.site.pk})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        payload = json.loads(response.content)
        self.assertEqual(payload["wording"]["updates_heading"], "Changes")
        self.assertEqual(payload["pages"], [])
        self.assertEqual(payload["skills"], [])
        self.assertEqual(payload["roles"], [])

    def test_the_file_is_named_for_what_is_in_it(self):
        """A downloads folder a fortnight later holds several of these."""
        wording_only = self.client.post(self.export_url, data={"site_id": self.site.pk})
        with_a_page = self.client.post(
            self.export_url,
            data={"site_id": self.site.pk, "page_ids": [self.page.pk]},
        )

        self.assertIn(
            f'filename="wording-export-site-{self.site.pk}-',
            wording_only["Content-Disposition"],
        )
        self.assertIn(
            f'filename="pages-export-site-{self.site.pk}-',
            with_a_page["Content-Disposition"],
        )

    def test_the_exported_file_applies_on_the_way_back_in(self):
        """The journey the export exists for, both halves through the admin."""
        self.wording.related_roles_heading = "Roles sharing skills with {role}"
        self.wording.save()
        exported = self.client.post(self.export_url, data={"site_id": self.site.pk})

        self.wording.related_roles_heading = "Roles that share {role} skills"
        self.wording.save()

        response = self.client.post(
            self.import_url,
            data={
                "site_id": self.site.pk,
                "json_file": SimpleUploadedFile(
                    "wording.json", exported.content, content_type="application/json"
                ),
            },
            follow=True,
        )

        self.assertContains(response, "Import complete.")
        self.assertEqual(
            CapabilityFrameworkWordingSettings.for_site(self.site).related_roles_heading,
            "Roles sharing skills with {role}",
        )

    def test_a_selection_that_matches_nothing_is_still_reported(self):
        """Ticking a page that has since been deleted is a mistake to hear
        about, not an export of the wording under another name."""
        response = self.client.post(
            self.export_url,
            data={"site_id": self.site.pk, "page_ids": [self.page.pk + 5000]},
            follow=True,
        )

        self.assertContains(
            response, "No matching pages, skills or roles were found for export."
        )


@override_settings(FEATURE_FLAGS=_feature_flags(skills_enabled=False))
class WordingOnlyExportWithTheFrameworkOffTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.admin_user = get_user_model().objects.create_superuser(
            username="wording-exporter-off",
            email="wording-exporter-off@example.gov.uk",
            password="unused-password",
        )
        self.client.force_login(self.admin_user)

    def test_selecting_nothing_is_still_a_mistake_where_there_is_no_wording(self):
        response = self.client.post(
            reverse("govuk_pages_export"),
            data={"site_id": self.site.pk},
            follow=True,
        )

        self.assertContains(response, "Select at least one page to export.")


@override_settings(FEATURE_FLAGS=_feature_flags(skills_enabled=False))
class FrameworkWordingWithTheFrameworkOffTests(TestCase):
    """An instance serving another profession has the framework switched off.

    The wording belongs to role and skill pages it does not have, so it is
    neither exported nor imported there. The import says so once, alongside the
    skills and roles it is dropping for the same reason, rather than leaving a
    reader to work out why a file that clearly holds wording changed none.
    """

    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.user = get_user_model().objects.create_superuser(
            username="wording-importer-off",
            email="wording-importer-off@example.com",
            password="password",
        )

    def test_the_export_carries_no_wording(self):
        payload = build_page_export_payload(
            site=self.site, pages=[], skills=[], roles=[]
        )

        self.assertNotIn("wording", payload)

    def test_an_imported_file_holding_wording_is_told_the_framework_is_off(self):
        home = self.site.root_page.specific
        page = home.add_child(
            instance=ContentPage(title="Somewhere", slug="somewhere", body="")
        )
        page.save_revision().publish()

        result = import_pages_from_payload(
            payload={
                "pages": [],
                "tags": [{"slug": "alpha", "name": "Alpha"}],
                "wording": {"updates_heading": "Changes"},
            },
            site=self.site,
            user=self.user,
        )

        self.assertEqual(len(result.errors), 1)
        self.assertIn("wording", result.errors[0])
        self.assertIn("FEATURE_SKILLS", result.errors[0])
        self.assertEqual(
            CapabilityFrameworkWordingSettings.for_site(self.site).updates_heading,
            "Updates",
        )
