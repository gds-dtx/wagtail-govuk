"""The chrome base.html puts round every page: header, navigation, hero, breadcrumbs, footer."""

import os
import re

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from wagtail.models import Site

from govuk.models import (
    ContentPage,
    CustomiseSettings,
    PhaseBannerSettings,
    SearchSettings,
)
from govuk.models.pages import HeroStyle
from govuk.tests.helpers import feature_flags, text_body

# Whichever module the run was given, rather than the one the author happens to
# export: named outright the footer assertions below pass only on the machine
# they were written on, and `manage.py test` defaults to another. Read the same
# way the context processor reads it, and read now, because `override_settings`
# swaps in a holder whose `SETTINGS_MODULE` is None.
SETTINGS_MODULE = os.getenv("DJANGO_SETTINGS_MODULE") or settings.SETTINGS_MODULE


class _PageTestCase(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root = self.site.root_page.specific
        self.page = self.root.add_child(instance=ContentPage(title="A page", slug="a-page"))
        self.page.save_revision().publish()

    def _get(self):
        return self.client.get(self.page.url)


class PageBaselineTests(TestCase):
    """WCAG-relevant guarantees every page inherits, and no template example leftovers.

    Most WCAG 2.2 AA behaviour comes from Design System components; these are
    the page-level invariants base.html is responsible for. The example
    attributes from the GOV.UK Frontend template docs must not ship either: this
    is a public repository and base.html is the first file anyone reuses.
    """

    def setUp(self):
        root_page = Site.objects.get(is_default_site=True).root_page.specific
        page = root_page.add_child(
            instance=ContentPage(
                title="Accessible page",
                slug="accessible-page",
                hero_title="Accessible page",
                body=text_body("<h2>A section</h2><p>Some body copy.</p>"),
            )
        )
        page.save_revision().publish()
        self.html = self.client.get(page.url).content.decode()

    def test_html_declares_a_language(self):
        """WCAG 3.1.1 Language of Page."""
        self.assertIn('<html lang="en"', self.html)

    def test_skip_link_targets_the_main_landmark(self):
        """WCAG 2.4.1 Bypass Blocks: the skip link must point at real content."""
        self.assertIn('class="govuk-skip-link"', self.html)
        self.assertIn('href="#main-content"', self.html)
        self.assertIn('id="main-content"', self.html)

    def test_there_is_a_main_landmark(self):
        """WCAG 1.3.1 / 4.1.2: the main region is programmatically identifiable."""
        self.assertIn('role="main"', self.html)

    def test_there_is_exactly_one_top_level_heading(self):
        """WCAG 1.3.1: a single h1 anchors the heading structure."""
        self.assertEqual(len(re.findall(r"<h1\b", self.html)), 1)

    def test_the_document_has_a_non_empty_title(self):
        """WCAG 2.4.2 Page Titled."""
        match = re.search(r"<title>(.*?)</title>", self.html, re.DOTALL)
        self.assertIsNotNone(match)
        self.assertTrue(match.group(1).strip())

    def test_theme_colour_is_the_design_system_black(self):
        self.assertIn('<meta name="theme-color" content="#0b0c0c">', self.html)

    def test_no_element_is_tinted_the_css_named_blue(self):
        """`blue` is #0000FF, which is in no GOV.UK palette."""
        self.assertNotIn('"blue"', self.html)

    def test_the_body_carries_no_example_data_attributes(self):
        self.assertNotIn("data-test=", self.html)
        self.assertNotIn("data-other=", self.html)


class HeaderLayoutTests(_PageTestCase):
    """The service name, search and sign in link each have their own location.

    Each is placed independently (header bar, service navigation, or hidden),
    with the defaults keeping the historical layout: name and search in the
    header, sign in link in the service navigation.
    """

    def setUp(self):
        super().setUp()
        self.site.site_name = "Capability Framework"
        self.site.save()
        self.customise = CustomiseSettings.for_site(self.site)
        self.search = SearchSettings.for_site(self.site)

    def test_the_site_name_and_search_sit_in_the_header_by_default(self):
        response = self._get()

        self.assertContains(response, "govuk-header__product-name")
        self.assertNotContains(response, "govuk-service-navigation__service-name")
        self.assertContains(response, 'class="app-site-search"', count=1)

    def test_the_service_name_moves_to_the_navigation_when_asked(self):
        self.customise.service_name_location = "navigation"
        self.customise.save()

        response = self._get()

        self.assertNotContains(response, "govuk-header__product-name")
        self.assertContains(response, "govuk-service-navigation__service-name")
        self.assertContains(response, "Capability Framework")

    def test_in_the_navigation_the_logo_links_to_govuk_on_its_own(self):
        self.customise.service_name_location = "navigation"
        self.customise.save()

        self.assertContains(
            self._get(), '<a href="https://www.gov.uk" class="govuk-header__homepage-link">'
        )

    def test_in_the_header_bar_the_logo_and_name_link_to_the_home_page(self):
        self.assertContains(self._get(), '<a href="/" class="govuk-header__homepage-link">')

    def test_the_service_name_link_can_be_customised(self):
        """In both placements; the standalone navigation logo always links to GOV.UK."""
        self.customise.service_name_link = "https://www.gov.uk/"
        self.customise.save()

        self.assertContains(
            self._get(),
            '<a href="https://www.gov.uk/" class="govuk-header__homepage-link">',
        )

        self.customise.service_name_location = "navigation"
        self.customise.save()
        response = self._get()
        self.assertContains(
            response,
            '<a href="https://www.gov.uk" class="govuk-header__homepage-link">',
        )
        name = re.search(
            r'<span class="govuk-service-navigation__service-name">(.*?)</span>',
            response.content.decode(),
            re.S,
        )
        self.assertIsNotNone(name)
        self.assertIn('href="https://www.gov.uk/"', name.group(1))

    def test_the_search_moves_to_the_navigation_independently(self):
        self.search.search_location = "navigation"
        self.search.save()

        response = self._get()

        self.assertContains(response, "govuk-header__product-name")
        self.assertContains(response, 'class="app-site-search"', count=1)

    def test_the_search_can_be_hidden(self):
        self.search.search_location = "hidden"
        self.search.save()

        self.assertNotContains(self._get(), 'class="app-site-search"')

    def test_the_search_box_submits_to_the_results_page_by_default(self):
        response = self._get()

        self.assertContains(response, '<form action="/search/"')
        self.assertContains(response, 'type="submit"')

    def test_disabling_free_text_results_drops_the_submitting_form(self):
        # The box keeps its jump-to-a-suggestion autocomplete but has no form
        # and no button, so Enter is inert; the magnifier becomes decoration.
        self.search.enable_search_results_page = False
        self.search.save()

        response = self._get()

        self.assertContains(response, "app-site-search--icon")
        self.assertContains(response, "data-suggest-url")
        self.assertNotContains(response, '<form action="/search/"')
        self.assertNotContains(response, "app-site-search__button")
        self.assertContains(response, "app-site-search__decoration")

    def test_the_sign_in_link_location(self):
        cases = (
            # location, sign in link shown, in the header
            (None, True, False),
            ("header", True, True),
            ("hidden", False, False),
        )
        for location, shown, in_header in cases:
            with self.subTest(location=location or "default (navigation)"):
                if location:
                    self.customise.sign_in_location = location
                    self.customise.save()

                response = self._get()

                if shown:
                    self.assertContains(response, "Sign in")
                else:
                    self.assertNotContains(response, "Sign in")
                if in_header:
                    self.assertContains(response, "app-header__sign-in")
                else:
                    self.assertNotContains(response, "app-header__sign-in")

    def test_the_sign_out_link_still_shows_when_hidden_for_a_signed_in_user(self):
        """Hidden is for visitors who never sign in; staff must still be able to sign out."""
        user = get_user_model().objects.create_user(username="staff", password="pw")
        self.customise.sign_in_location = "hidden"
        self.customise.save()
        self.client.force_login(user)

        response = self._get()

        self.assertNotContains(response, "Sign in")
        self.assertContains(response, "Sign out")

    def test_the_search_placeholder_can_be_set(self):
        self.assertContains(self._get(), 'placeholder="Search"')

        self.search.search_placeholder = "Search for roles or skills"
        self.search.save()

        self.assertContains(self._get(), 'placeholder="Search for roles or skills"')


class ServiceNavigationTests(_PageTestCase):
    """The menu only appears when it has somewhere to go.

    The Design System's JavaScript un-hides the toggle below its breakpoint
    whatever the list holds, so an empty menu would be a Menu button that
    expands to nothing on every narrow-screen page.
    """

    def setUp(self):
        super().setUp()
        self.customise = CustomiseSettings.for_site(self.site)
        self.search = SearchSettings.for_site(self.site)

    def test_the_menu_renders_for_the_sign_in_link_alone(self):
        response = self._get()

        self.assertContains(response, "govuk-service-navigation__toggle")
        self.assertContains(response, 'id="navigation"')

    def test_the_menu_goes_away_when_it_would_be_empty(self):
        self.customise.sign_in_location = "hidden"
        self.customise.save()

        response = self._get()

        self.assertNotContains(response, "govuk-service-navigation__toggle")
        self.assertNotContains(response, 'id="navigation"')

    def test_a_signed_in_user_keeps_the_menu_for_the_sign_out_link(self):
        user = get_user_model().objects.create_user(username="staff", password="pw")
        self.customise.sign_in_location = "hidden"
        self.customise.save()
        self.client.force_login(user)

        response = self._get()

        self.assertContains(response, "govuk-service-navigation__toggle")
        self.assertContains(response, "Sign out")

    def test_a_page_in_the_menus_brings_it_back(self):
        menu_page = self.root.add_child(
            instance=ContentPage(title="Guidance", slug="guidance", show_in_menus=True)
        )
        menu_page.save_revision().publish()
        self.customise.sign_in_location = "hidden"
        self.customise.save()

        response = self._get()

        self.assertContains(response, "govuk-service-navigation__toggle")
        self.assertContains(response, "Guidance")

    def test_the_search_in_the_navigation_does_not_need_the_menu(self):
        self.customise.sign_in_location = "hidden"
        self.customise.save()
        self.search.search_location = "navigation"
        self.search.save()

        response = self._get()

        self.assertNotContains(response, "govuk-service-navigation__toggle")
        self.assertContains(response, 'class="app-site-search"')


class PhaseBannerWordingTests(_PageTestCase):
    """The phase banner wording either side of the feedback link is editable."""

    def setUp(self):
        super().setUp()
        self.banner = PhaseBannerSettings.for_site(self.site)
        self.banner.enabled = True
        self.banner.save()

    def test_the_default_wording_is_the_usual_govuk_sentence(self):
        response = self._get()

        self.assertContains(response, "This is a new service")
        self.assertContains(response, "your feedback")
        self.assertContains(response, "will help us to improve it.")

    def test_the_wording_either_side_of_the_link_can_be_replaced(self):
        self.banner.phase_text = "Complete our 3 minute"
        self.banner.feedback_link_text = "feedback survey"
        self.banner.phase_text_after = "to help us improve the framework."
        self.banner.feedback_url = "https://example.gov.uk/survey"
        self.banner.save()

        response = self._get()

        self.assertContains(response, "Complete our 3 minute")
        self.assertContains(
            response,
            '<a class="govuk-link" href="https://example.gov.uk/survey">'
            "feedback survey</a>",
            html=True,
        )
        self.assertContains(response, "to help us improve the framework.")
        self.assertNotContains(response, "will help us to improve it.")


class FooterDebugLineTests(_PageTestCase):
    """What the footer's debug line tells a reader of the page source."""

    @override_settings(DEBUG=True)
    def test_the_settings_module_and_version_show_while_debugging(self):
        response = self._get()

        self.assertContains(response, "govuk-footer__meta-custom")
        self.assertContains(response, f"Settings: {SETTINGS_MODULE}")

    @override_settings(DEBUG=False)
    def test_they_stay_out_of_the_page_source_otherwise(self):
        """`hidden` kept the line out of sight but not out of the source."""
        response = self._get()

        self.assertNotContains(response, "govuk-footer__meta-custom")
        self.assertNotContains(response, f"Settings: {SETTINGS_MODULE}")


@override_settings(FEATURE_FLAGS=feature_flags(skills=True))
class BackToTopTests(_PageTestCase):
    """Opt-in, since every site shares base.html; the scroll reveal is main.js's."""

    def test_the_link_is_off_until_a_site_switches_it_on(self):
        response = self._get()

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'id="back-to-top"')

    def test_the_link_appears_once_a_site_switches_it_on(self):
        customise = CustomiseSettings.for_site(self.site)
        customise.show_back_to_top = True
        customise.save()

        response = self._get()

        self.assertContains(response, 'id="back-to-top"')
        self.assertContains(response, "Back to top")

class HeroStyleMixinPropertyTests(SimpleTestCase):
    """The base.html conditionals read these properties off the page."""

    def test_each_property_answers_for_its_style_only(self):
        cases = {
            HeroStyle.HIDDEN: (False, False, False, False),
            HeroStyle.NONE: (False, False, False, False),
            HeroStyle.LARGE_TEXT: (True, False, False, False),
            HeroStyle.STYLED: (False, True, False, True),
            HeroStyle.COMBINED: (False, False, True, True),
        }
        for style, expected in cases.items():
            page = ContentPage(title="x", hero_style=style)
            got = (
                page.hero_is_large_text,
                page.hero_is_styled,
                page.hero_is_combined,
                page.hero_uses_masthead,
            )
            self.assertEqual(got, expected, style)

class HeroStyleRenderingTests(TestCase):
    def test_each_style_renders_its_own_top_of_page_markup(self):
        cases = (
            # style, extra fields, in the page, not in the page
            (
                HeroStyle.COMBINED,
                {},
                ["masthead--combined", "govuk-service-navigation--inverse"],
                [],
            ),
            (
                HeroStyle.STYLED,
                {},
                ["masthead"],
                ["masthead--combined", "govuk-service-navigation--inverse"],
            ),
            (
                HeroStyle.LARGE_TEXT,
                {"hero_intro": "<p>Big intro</p>"},
                ["govuk-heading-xl", "Big intro"],
                ["masthead", "hero__title"],
            ),
            (HeroStyle.NONE, {}, ["hero__title"], ["masthead"]),
            # The H1 stays in the document for structure, visually hidden.
            (
                HeroStyle.HIDDEN,
                {},
                ['<h1 class="govuk-visually-hidden">Hero page</h1>'],
                ["hero__title", "masthead", "govuk-heading-xl"],
            ),
        )
        root = Site.objects.get(is_default_site=True).root_page.specific
        for style, fields, present, absent in cases:
            with self.subTest(style=style):
                page = root.add_child(
                    instance=ContentPage(
                        title="Hero page", slug=f"hero-{style}", hero_style=style, **fields
                    )
                )
                page.save_revision().publish()

                response = self.client.get(page.url)

                for text in present:
                    self.assertContains(response, text)
                for text in absent:
                    self.assertNotContains(response, text)


@override_settings(FEATURE_FLAGS=feature_flags())
class BreadcrumbToggleTests(TestCase):
    """The per-page show_breadcrumbs switch on the non-framework page types."""

    def test_the_switch_and_the_hero_style_choose_the_breadcrumbs(self):
        cases = (
            # fields, breadcrumbs shown, inverse
            ({"show_breadcrumbs": True}, True, False),
            ({"show_breadcrumbs": False}, False, False),
            ({"hero_style": HeroStyle.COMBINED, "show_breadcrumbs": True}, True, True),
            ({"hero_style": HeroStyle.COMBINED, "show_breadcrumbs": False}, False, False),
        )
        root = Site.objects.get(is_default_site=True).root_page.specific
        for index, (fields, shown, inverse) in enumerate(cases):
            with self.subTest(**fields):
                # A child of the site root has a non-empty ancestor trail.
                page = root.add_child(
                    instance=ContentPage(
                        title="Guidance", slug=f"guidance-{index}", body="", **fields
                    )
                )
                page.save_revision().publish()

                response = self.client.get(page.url)

                if shown:
                    self.assertContains(response, "govuk-breadcrumbs")
                else:
                    self.assertNotContains(response, "govuk-breadcrumbs")
                if inverse:
                    self.assertContains(response, "govuk-breadcrumbs--inverse")
                else:
                    self.assertNotContains(response, "govuk-breadcrumbs--inverse")

    def test_the_site_root_still_has_no_breadcrumbs(self):
        root = Site.objects.get(is_default_site=True).root_page.specific

        self.assertNotContains(self.client.get(root.url), "govuk-breadcrumbs")


class PageContentMetadataToggleTests(TestCase):
    def test_content_page_metadata_is_hidden_by_default_and_can_be_enabled(self):
        root = Site.objects.get(is_default_site=True).root_page.specific
        page = root.add_child(
            instance=ContentPage(
                title="Metadata content page",
                slug="metadata-content-page",
                body=text_body("<p>Body</p>"),
                author="Example Author",
                show_last_updated_date=True,
            )
        )
        page.save_revision().publish()

        response = self.client.get(page.url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "page-content-metadata")

        page.show_page_content_metadata = True
        page.save_revision().publish()

        response = self.client.get(page.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "page-content-metadata")
