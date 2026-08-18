import os

from django.conf import settings
from django.test import TestCase, override_settings
from wagtail.models import Site

from govuk.models import ContentPage, CustomiseSettings, PhaseBannerSettings

# Whichever module the run was given, rather than the one the author happens to
# export: named outright the footer assertions below pass only on the machine
# they were written on, and `manage.py test` defaults to another. Read the same
# way the context processor reads it, and read now, because `override_settings`
# swaps in a holder whose `SETTINGS_MODULE` is None.
SETTINGS_MODULE = os.getenv("DJANGO_SETTINGS_MODULE") or settings.SETTINGS_MODULE


class HeaderLayoutTests(TestCase):
    """The site name and search can sit in the service navigation bar.

    GOV.UK services usually carry the service name and search on a light bar
    below the black-and-blue GOV.UK header, which is what the DDaT Capability
    Framework does. Existing sites keep the name and search in the header, so
    the behaviour is opt in.
    """

    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.site.site_name = "Capability Framework"
        self.site.save()

        self.page = self.site.root_page.specific.add_child(
            instance=ContentPage(title="A page", slug="a-page")
        )
        self.page.save_revision().publish()

        self.settings = CustomiseSettings.for_site(self.site)

    def _get(self):
        return self.client.get(self.page.url)

    def test_the_site_name_and_search_sit_in_the_header_by_default(self):
        response = self._get()

        self.assertContains(response, "govuk-header__product-name")
        self.assertNotContains(response, "govuk-service-navigation__service-name")
        # The search is rendered once, inside the header.
        self.assertContains(response, 'class="app-site-search"', count=1)

    def test_the_site_name_and_search_move_to_the_navigation_when_asked(self):
        self.settings.show_service_name_in_navigation = True
        self.settings.save()

        response = self._get()

        self.assertNotContains(response, "govuk-header__product-name")
        self.assertContains(response, "govuk-service-navigation__service-name")
        self.assertContains(response, "Capability Framework")
        self.assertContains(response, 'class="app-site-search"', count=1)

    def test_the_govuk_logo_links_to_govuk(self):
        """The Design System's header logo leaves the service for GOV.UK."""
        response = self._get()

        self.assertContains(
            response,
            '<a href="https://www.gov.uk" class="govuk-header__homepage-link">',
        )

    def test_every_page_carries_a_back_to_top_button(self):
        """main.js reveals it once the page has been scrolled."""
        response = self._get()

        self.assertContains(response, 'id="back-to-top"')
        self.assertContains(response, "Back to top")

    def test_the_sign_in_link_shows_unless_it_is_hidden(self):
        self.assertContains(self._get(), "Sign in")

        self.settings.hide_sign_in_link = True
        self.settings.save()

        self.assertNotContains(self._get(), "Sign in")

    def test_the_search_placeholder_can_be_set(self):
        self.assertContains(self._get(), 'placeholder="Search"')

        self.settings.search_placeholder = "Search for roles or skills"
        self.settings.save()

        self.assertContains(self._get(), 'placeholder="Search for roles or skills"')


class PhaseBannerWordingTests(TestCase):
    """The phase banner wording either side of the feedback link is editable."""

    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.page = self.site.root_page.specific.add_child(
            instance=ContentPage(title="A page", slug="a-page")
        )
        self.page.save_revision().publish()

        self.banner = PhaseBannerSettings.for_site(self.site)
        self.banner.enabled = True
        self.banner.save()

    def test_the_default_wording_is_the_usual_govuk_sentence(self):
        response = self.client.get(self.page.url)

        self.assertContains(response, "This is a new service")
        self.assertContains(response, "your feedback")
        self.assertContains(response, "will help us to improve it.")

    def test_the_wording_either_side_of_the_link_can_be_replaced(self):
        self.banner.phase_text = "Complete our 3 minute"
        self.banner.feedback_link_text = "feedback survey"
        self.banner.phase_text_after = "to help us improve the framework."
        self.banner.feedback_url = "https://example.gov.uk/survey"
        self.banner.save()

        response = self.client.get(self.page.url)

        self.assertContains(response, "Complete our 3 minute")
        self.assertContains(
            response,
            '<a class="govuk-link" href="https://example.gov.uk/survey">'
            "feedback survey</a>",
            html=True,
        )
        self.assertContains(response, "to help us improve the framework.")
        self.assertNotContains(response, "will help us to improve it.")


class FooterDebugLineTests(TestCase):
    """What the footer's debug line tells a reader of the page source."""

    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.page = self.site.root_page.specific.add_child(
            instance=ContentPage(title="A page", slug="a-page")
        )
        self.page.save_revision().publish()

    @override_settings(DEBUG=True)
    def test_the_settings_module_and_version_show_while_debugging(self):
        response = self.client.get(self.page.url)

        self.assertContains(response, "govuk-footer__meta-custom")
        self.assertContains(response, f"Settings: {SETTINGS_MODULE}")

    @override_settings(DEBUG=False)
    def test_they_stay_out_of_the_page_source_otherwise(self):
        """`hidden` kept the line out of sight but not out of the source."""
        response = self.client.get(self.page.url)

        self.assertNotContains(response, "govuk-footer__meta-custom")
        self.assertNotContains(response, f"Settings: {SETTINGS_MODULE}")
