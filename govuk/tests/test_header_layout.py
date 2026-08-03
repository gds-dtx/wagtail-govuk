from django.test import TestCase
from wagtail.models import Site

from govuk.models import ContentPage, CustomiseSettings, PhaseBannerSettings


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
