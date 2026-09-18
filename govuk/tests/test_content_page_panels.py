"""What each page type offers an editor, and what it writes while rendering.

The Capability Framework's fields and panels live on the framework page types
only, and the two framework page types differ:

* ``FrameworkMainPage`` carries the three framework switches and the welcome
  content, all gated by the SKILLS flag;
* ``FrameworkContentPage`` carries none of those -- it always shows the role
  navigation and offers no switch for it, no welcome content, and no sidebar
  heading navigation.

Neither framework page offers the sidebar heading navigation (the role
navigation takes that column). Plain ``ContentPage`` is offered none of the
framework furniture and keeps the heading navigation.
"""

from django.test import SimpleTestCase, TestCase, override_settings
from wagtail.models import Site

from govuk.models import (
    CapabilityFrameworkWordingSettings,
    ContentPage,
    FrameworkContentPage,
    FrameworkMainPage,
    FrameworkSkillsPage,
    framework_content_panels,
    framework_content_settings_panels,
    framework_main_settings_panels,
)
from govuk.tests.framework_helpers import make_framework_main_page

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


class FrameworkMainPagePanelTests(SimpleTestCase):
    """The framework main page carries the switches and the welcome content."""

    @override_settings(FEATURE_FLAGS=_feature_flags(skills_enabled=True))
    def test_the_main_page_gets_its_switches_and_its_welcome_content(self):
        settings_fields = set(_field_names(framework_main_settings_panels()))
        content_fields = _field_names(framework_content_panels())

        self.assertTrue(FRAMEWORK_SETTINGS_FIELDS <= settings_fields)
        self.assertIn("framework_welcome_body", content_fields)

    @override_settings(FEATURE_FLAGS=_feature_flags(skills_enabled=True))
    def test_the_main_page_offers_no_sidebar_heading_navigation(self):
        """The role navigation takes that side column."""
        settings_fields = _field_names(framework_main_settings_panels())

        self.assertNotIn("enable_free_text_heading_navigation", settings_fields)

    @override_settings(FEATURE_FLAGS=_feature_flags(skills_enabled=True))
    def test_the_main_page_offers_no_hero_styling_toggles(self):
        """Hero styling is plain ContentPage's; the framework sets its own."""
        settings_fields = _field_names(framework_main_settings_panels())

        self.assertNotIn("enable_hero_styling", settings_fields)
        self.assertNotIn(
            "enable_combined_service_navigation_and_hero_styling", settings_fields
        )

    @override_settings(FEATURE_FLAGS=_feature_flags(skills_enabled=False))
    def test_a_site_without_the_framework_is_offered_none_of_the_switches(self):
        settings_fields = set(_field_names(framework_main_settings_panels()))
        content_fields = _field_names(framework_content_panels())

        self.assertEqual(FRAMEWORK_SETTINGS_FIELDS & settings_fields, set())
        self.assertNotIn("framework_welcome_body", content_fields)


class FrameworkContentPagePanelTests(SimpleTestCase):
    """A framework content page offers none of the framework switches.

    It always shows the role navigation, so there is no switch for it, and it
    offers neither the updates block, the welcome content, nor the sidebar
    heading navigation.
    """

    @override_settings(FEATURE_FLAGS=_feature_flags(skills_enabled=True))
    def test_it_offers_no_switches_no_welcome_and_no_heading_navigation(self):
        settings_fields = set(_field_names(framework_content_settings_panels()))
        content_fields = _field_names(FrameworkContentPage.content_panels)

        self.assertEqual(FRAMEWORK_SETTINGS_FIELDS & settings_fields, set())
        self.assertNotIn("enable_free_text_heading_navigation", settings_fields)
        self.assertNotIn("framework_welcome_body", content_fields)

    def test_it_offers_the_page_settings_and_tags_but_no_hero_styling(self):
        settings_panels = FrameworkContentPage.settings_panels
        self.assertEqual(
            _field_names(settings_panels),
            [
                "show_last_updated_date",
                "show_page_content_metadata",
            ],
        )
        self.assertEqual(
            [
                panel.relation_name
                for panel in settings_panels
                if getattr(panel, "relation_name", None)
            ],
            ["tagged_items"],
        )


class FrameworkSkillsPagePanelTests(SimpleTestCase):
    def test_the_skills_page_offers_no_heading_navigation_or_hero_styling(self):
        settings_fields = _field_names(FrameworkSkillsPage.settings_panels)

        self.assertNotIn("enable_free_text_heading_navigation", settings_fields)
        self.assertNotIn("enable_hero_styling", settings_fields)
        self.assertNotIn(
            "enable_combined_service_navigation_and_hero_styling", settings_fields
        )


class SidebarListingIsCentralisedTests(SimpleTestCase):
    """Sidebar membership is managed in Sidebar settings, not per page.

    The framework page types no longer carry a per-page sidebar toggle; which
    pages appear (and their order) lives in SidebarSettings.
    """

    def test_no_per_page_sidebar_toggle_on_the_framework_pages(self):
        self.assertNotIn(
            "show_in_framework_navigation",
            _field_names(FrameworkContentPage.settings_panels),
        )
        self.assertNotIn(
            "show_in_framework_navigation",
            _field_names(FrameworkSkillsPage.settings_panels),
        )


class PlainContentPagePanelTests(SimpleTestCase):
    """Plain ``ContentPage`` is never offered any of the framework furniture.

    The switches and the welcome content are on the framework page types, so a
    plain content page carries none of them whatever the flag says. It does keep
    the sidebar heading navigation, which the framework pages give up.
    """

    @override_settings(FEATURE_FLAGS=_feature_flags(skills_enabled=True))
    def test_no_framework_panels_even_with_the_flag_on(self):
        settings_fields = set(_field_names(ContentPage.settings_panels))
        content_fields = _field_names(ContentPage.content_panels)

        self.assertEqual(FRAMEWORK_SETTINGS_FIELDS & settings_fields, set())
        self.assertNotIn("framework_welcome_body", content_fields)

    def test_it_keeps_the_sidebar_heading_navigation_and_hero_styling(self):
        settings_fields = _field_names(ContentPage.settings_panels)

        self.assertIn("enable_free_text_heading_navigation", settings_fields)
        self.assertIn("enable_hero_styling", settings_fields)
        self.assertIn(
            "enable_combined_service_navigation_and_hero_styling", settings_fields
        )


class FrameworkMainPageDefaultsTests(SimpleTestCase):
    """A new framework main page starts with all three switches on."""

    def test_the_switches_default_on(self):
        page = FrameworkMainPage(title="Framework", slug="framework")

        self.assertTrue(page.show_role_navigation)
        self.assertTrue(page.show_framework_updates)
        self.assertTrue(page.show_framework_welcome)


class FrameworkContentPageForcesItsBehaviourTests(TestCase):
    """A framework content page shows the role navigation whatever is stored."""

    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root_page = self.site.root_page.specific
        self.main_page = make_framework_main_page(self.root_page)

    @override_settings(FEATURE_FLAGS=_feature_flags(skills_enabled=True))
    def test_role_navigation_is_on_even_when_stored_off(self):
        child = self.main_page.add_child(
            instance=FrameworkContentPage(
                title="Further guidance",
                slug="further-guidance",
                body="<p>Guidance.</p>",
                # Stored the wrong way round on purpose: an import could carry
                # these, and the page must ignore them.
                show_role_navigation=False,
                show_framework_updates=True,
                show_framework_welcome=True,
                enable_free_text_heading_navigation=True,
            )
        )
        child.save_revision().publish()

        response = self.client.get(child.specific.url)

        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.context.get("role_navigation"))
        self.assertIsNone(response.context.get("framework_changelog"))
        self.assertNotIn("framework_sections", response.context)
        self.assertFalse(response.context.get("heading_navigation"))

    @override_settings(FEATURE_FLAGS=_feature_flags(skills_enabled=True))
    def test_it_marks_itself_current_in_the_sidebar(self):
        """A framework content page highlights itself in the "Further
        resources" group, the way a role does on its route."""
        child = self.main_page.add_child(
            instance=FrameworkContentPage(
                title="Guidance", slug="guidance", body="<p>Guidance.</p>"
            )
        )
        child.save_revision().publish()

        response = self.client.get(child.specific.url)

        further_resources = next(
            group
            for group in response.context["role_navigation"]
            if group["title"] == "Further resources"
        )
        current = [item for item in further_resources["items"] if item["is_current"]]
        self.assertEqual([item["title"] for item in current], ["Guidance"])


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
    def test_a_content_page_has_no_framework_columns_to_carry_a_switch(self):
        """The framework switches are gone from the plain content page.

        They used to be columns every instance shared, so a framework export
        could turn them on elsewhere: the import applies any field the model
        has. They live only on the framework page types now, so a plain content
        page has no column to import them onto, and rendering one carries no
        framework furniture.
        """
        field_names = {field.name for field in ContentPage._meta.get_fields()}
        self.assertEqual(
            field_names
            & {
                "show_role_navigation",
                "show_framework_updates",
                "show_framework_welcome",
                "framework_welcome_body",
            },
            set(),
        )

        response = self.client.get(self.page.url)

        self.assertNotContains(response, 'class="role-nav"')
        self.assertIsNone(response.context.get("role_navigation"))
        self.assertIsNone(response.context.get("framework_changelog"))
        self.assertEqual(CapabilityFrameworkWordingSettings.objects.count(), 0)

    @override_settings(FEATURE_FLAGS=_feature_flags(skills_enabled=True))
    def test_a_framework_page_that_asks_for_the_framework_gets_its_wording(self):
        page = make_framework_main_page(
            self.root_page,
            title="Framework",
            slug="framework",
            show_framework_updates=True,
        )

        response = self.client.get(page.url)

        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.context["framework_wording"])
        self.assertIn("framework_changelog", response.context)


@override_settings(FEATURE_FLAGS=_feature_flags(skills_enabled=False))
class FrameworkPagesOnASiteWithoutTheFrameworkTests(TestCase):
    """The framework's page types are not pages anywhere the framework is off.

    The admin cannot make one there, but the page import is not the admin and
    creates any page whose model it can resolve, so a framework export landed
    on another service leaves them in its tree. The skills index already 404ed
    there and stayed out of the generic listings; the main page and the
    framework content pages served and were listed. Now all three behave alike,
    which is what docs/platform-boundaries.md promises.
    """

    def setUp(self):
        from govuk.models import FrameworkContentPage

        self.site = Site.objects.get(is_default_site=True)
        self.root_page = self.site.root_page.specific
        self.main_page = make_framework_main_page(self.root_page)
        self.content_page = self.main_page.add_child(
            instance=FrameworkContentPage(title="Roadmap", slug="roadmap", body="")
        )
        self.content_page.save_revision().publish()

    def test_the_main_page_and_its_framework_children_answer_404(self):
        self.assertEqual(self.client.get(self.main_page.url).status_code, 404)
        self.assertEqual(self.client.get(self.content_page.url).status_code, 404)

    def test_they_are_kept_out_of_the_generic_listings(self):
        from wagtail.models import Page

        from govuk.models import without_framework_pages

        listed = without_framework_pages(Page.objects.live()).specific()

        self.assertNotIn(self.main_page.pk, [page.pk for page in listed])
        self.assertNotIn(self.content_page.pk, [page.pk for page in listed])
        self.assertIn(self.root_page.pk, [page.pk for page in listed])

    @override_settings(FEATURE_FLAGS=_feature_flags(skills_enabled=True))
    def test_with_the_framework_on_they_serve_as_before(self):
        self.assertEqual(self.client.get(self.main_page.url).status_code, 200)
        self.assertEqual(self.client.get(self.content_page.url).status_code, 200)


class PlainPagesMayNestUnderFrameworkPagesTests(TestCase):
    """The development instance holds project pages under the pages beside
    the roles, so a plain page has to be allowed there too."""

    @override_settings(FEATURE_FLAGS=_feature_flags(skills_enabled=True))
    def test_a_content_page_can_be_created_under_a_framework_content_page(self):
        from govuk.models import FrameworkContentPage

        site = Site.objects.get(is_default_site=True)
        main_page = make_framework_main_page(site.root_page.specific)
        roadmap = main_page.add_child(
            instance=FrameworkContentPage(title="Roadmap", slug="roadmap", body="")
        )
        roadmap.save_revision().publish()

        self.assertTrue(ContentPage.can_create_at(roadmap))
        self.assertIn(ContentPage, FrameworkContentPage.allowed_subpage_models())


class FeedbackSettingsKeepTheirLabelsTests(SimpleTestCase):
    """#71 gave the feedback fields labels an editor understands. A panel
    heading overrides the label in the form but not in the revision-compare
    view, so the two disagreed; the form reads the field's own label again."""

    def test_the_feedback_panels_carry_no_heading_override(self):
        from govuk.models import CustomiseSettings

        def field_panels(panel):
            children = getattr(panel, "children", None)
            if children is None:
                return [panel]
            return [found for child in children for found in field_panels(child)]

        panels = [
            panel
            for top in CustomiseSettings.panels
            for panel in field_panels(top)
            if getattr(panel, "field_name", "").startswith("page_feedback_more_")
        ]
        self.assertEqual(len(panels), 3)
        for panel in panels:
            self.assertFalse(panel.heading, panel.field_name)
