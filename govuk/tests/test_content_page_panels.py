"""What a content page offers an editor, and what it writes while rendering.

ContentPage is the page type every instance on this codebase builds with, so
the Capability Framework's fields on it are offered to every site that runs
this code, framework or not. The fields stay on the model -- removing a column
from a shared page type is a migration and a conversation -- but the panels and
the queries behind them belong to the framework only.
"""

from django.test import SimpleTestCase, TestCase, override_settings
from wagtail.models import Site

from govuk.models import (
    CapabilityFrameworkWordingSettings,
    ContentPage,
    GovukRole,
    content_page_content_panels,
    content_page_settings_panels,
)

FRAMEWORK_SETTINGS_FIELDS = {
    "show_role_navigation",
    "show_framework_updates",
    "show_framework_welcome",
}


def _feature_flags(*, skills_enabled: bool) -> dict[str, bool]:
    return {
        "SKILLS": skills_enabled,
        "ORGANISATIONS": False,
        "PEOPLE_FINDER": False,
        "FEEDBACK": False,
    }


def _field_names(panels) -> list[str]:
    return [
        panel.field_name for panel in panels if getattr(panel, "field_name", None)
    ]


class ContentPagePanelTests(SimpleTestCase):
    @override_settings(FEATURE_FLAGS=_feature_flags(skills_enabled=True))
    def test_the_framework_gets_its_switches_and_its_welcome_content(self):
        settings_fields = set(_field_names(content_page_settings_panels()))
        content_fields = _field_names(content_page_content_panels())

        self.assertTrue(FRAMEWORK_SETTINGS_FIELDS <= settings_fields)
        self.assertIn("framework_welcome_body", content_fields)

    @override_settings(FEATURE_FLAGS=_feature_flags(skills_enabled=False))
    def test_a_site_without_the_framework_is_offered_none_of_it(self):
        settings_fields = set(_field_names(content_page_settings_panels()))
        content_fields = _field_names(content_page_content_panels())

        self.assertEqual(FRAMEWORK_SETTINGS_FIELDS & settings_fields, set())
        self.assertNotIn("framework_welcome_body", content_fields)

    @override_settings(FEATURE_FLAGS=_feature_flags(skills_enabled=False))
    def test_the_rest_of_the_page_is_untouched_by_the_flag(self):
        """The flag takes the framework away, not the page type."""
        settings_fields = _field_names(content_page_settings_panels())
        content_fields = _field_names(content_page_content_panels())

        self.assertEqual(
            content_fields,
            ["hero_title", "hero_intro", "author", "body", "body_blocks"],
        )
        self.assertEqual(
            settings_fields,
            [
                "enable_hero_styling",
                "enable_combined_service_navigation_and_hero_styling",
                "show_last_updated_date",
                "show_page_content_metadata",
                "enable_free_text_heading_navigation",
            ],
        )
        # The tags panel is an InlinePanel with no field_name of its own, and
        # it has to survive the framework panels being taken out around it.
        self.assertEqual(
            [
                panel.relation_name
                for panel in content_page_settings_panels()
                if getattr(panel, "relation_name", None)
            ],
            ["tagged_items"],
        )


class ContentPageRenderWriteTests(TestCase):
    """Rendering a page should not create rows for a feature that is off.

    ``BaseSiteSetting.for_site`` is a ``get_or_create``, so the wording lookup
    reads like a read and is a write. It used to run on every content page on
    every site, which left a Capability Framework settings row on sites whose
    admin never registers the panel to edit it.
    """

    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root_page = self.site.root_page.specific
        self.page = self.root_page.add_child(
            instance=ContentPage(title="Guidance", slug="guidance", body="Hello")
        )
        self.page.save_revision().publish()
        CapabilityFrameworkWordingSettings.objects.all().delete()

    @override_settings(FEATURE_FLAGS=_feature_flags(skills_enabled=False))
    def test_a_page_with_no_framework_furniture_writes_no_framework_row(self):
        response = self.client.get(self.page.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(CapabilityFrameworkWordingSettings.objects.count(), 0)
        self.assertIsNone(response.context.get("framework_wording"))

    @override_settings(FEATURE_FLAGS=_feature_flags(skills_enabled=False))
    def test_a_switch_stored_by_another_site_is_not_honoured(self):
        """The switches are columns on a page type every instance shares.

        The page export carries all four framework fields, and the import
        applies any field the model has, so importing a framework export onto
        another site turns them on there. Hiding the panels alone would leave
        an editor looking at a role navigation with no switch to find, filled
        with whatever the page tree happens to hold -- the probe for this
        rendered a "Further resources" group listing the content page itself.
        """
        GovukRole.objects.create(title="Leftover role", family="Data", slug="leftover")
        self.page.show_role_navigation = True
        self.page.show_framework_updates = True
        self.page.save_revision().publish()

        response = self.client.get(self.page.url)

        self.assertNotContains(response, 'class="role-nav"')
        self.assertIsNone(response.context.get("role_navigation"))
        self.assertIsNone(response.context.get("framework_changelog"))
        self.assertEqual(CapabilityFrameworkWordingSettings.objects.count(), 0)

    @override_settings(FEATURE_FLAGS=_feature_flags(skills_enabled=True))
    def test_a_page_that_asks_for_the_framework_still_gets_its_wording(self):
        self.page.show_framework_updates = True
        self.page.save_revision().publish()

        response = self.client.get(self.page.url)

        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.context["framework_wording"])
        self.assertIn("framework_changelog", response.context)
