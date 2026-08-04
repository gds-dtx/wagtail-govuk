from django.test import TestCase
from wagtail.models import Site

from govuk.models import ContentPage, CustomiseSettings, PhaseBannerSettings


class HeaderLayoutTests(TestCase):
    """The service name, search and sign in link each have their own location.

    GOV.UK services usually carry the service name and search on a light bar
    below the black-and-blue GOV.UK header, which is what the DDaT Capability
    Framework does. Each element is placed independently (header bar, service
    navigation, or hidden), with the defaults keeping the historical layout:
    name and search in the header, sign in link in the service navigation.
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

    def test_the_service_name_moves_to_the_navigation_when_asked(self):
        self.settings.service_name_location = "navigation"
        self.settings.save()

        response = self._get()

        self.assertNotContains(response, "govuk-header__product-name")
        self.assertContains(response, "govuk-service-navigation__service-name")
        self.assertContains(response, "Capability Framework")

    def test_the_search_moves_to_the_navigation_independently(self):
        # The search location is decoupled from the service name location: the
        # search can sit in the navigation while the name stays in the header.
        self.settings.search_location = "navigation"
        self.settings.save()

        response = self._get()

        self.assertContains(response, "govuk-header__product-name")
        # Still rendered exactly once, now inside the navigation.
        self.assertContains(response, 'class="app-site-search"', count=1)

    def test_the_search_can_be_hidden(self):
        self.settings.search_location = "hidden"
        self.settings.save()

        self.assertNotContains(self._get(), 'class="app-site-search"')

    def test_the_sign_in_link_sits_in_the_navigation_by_default(self):
        response = self._get()

        self.assertContains(response, "Sign in")
        self.assertNotContains(response, "app-header__sign-in")

    def test_the_sign_in_link_can_move_to_the_header(self):
        self.settings.sign_in_location = "header"
        self.settings.save()

        response = self._get()

        self.assertContains(response, "app-header__sign-in")
        self.assertContains(response, "Sign in")

    def test_the_sign_in_link_can_be_hidden(self):
        self.settings.sign_in_location = "hidden"
        self.settings.save()

        response = self._get()

        self.assertNotContains(response, "Sign in")
        self.assertNotContains(response, "app-header__sign-in")

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
