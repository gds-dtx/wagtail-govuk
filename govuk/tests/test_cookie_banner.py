from django.test import TestCase
from wagtail.models import Site

from govuk.models import CookieBannerSettings


class CookieBannerTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.settings = CookieBannerSettings.for_site(self.site)

    def test_banner_is_hidden_until_switched_on(self):
        response = self.client.get("/")

        self.assertNotContains(response, "govuk-cookie-banner")

    def test_banner_renders_when_enabled(self):
        self.settings.enabled = True
        self.settings.service_name = "Capability Framework"
        self.settings.save()

        response = self.client.get("/")

        self.assertContains(response, "govuk-cookie-banner")
        self.assertContains(response, "Cookies on Capability Framework")
        self.assertContains(response, "Accept analytics cookies")
        self.assertContains(response, "Reject analytics cookies")
        self.assertContains(response, "cookie-banner.js")

    def test_heading_falls_back_to_the_site_name(self):
        self.site.site_name = "Example service"
        self.site.save()
        self.settings.refresh_from_db()

        self.assertEqual(
            self.settings.heading_for_site(), "Cookies on Example service"
        )

    def test_heading_copes_with_no_name_at_all(self):
        self.site.site_name = ""
        self.site.save()
        self.settings.refresh_from_db()

        self.assertEqual(self.settings.heading_for_site(), "Cookies on this service")

    def test_cookies_page_link_is_configurable(self):
        self.settings.enabled = True
        self.settings.cookies_page_url = "/cookie-statement"
        self.settings.save()

        response = self.client.get("/")

        self.assertContains(response, 'href="/cookie-statement"')
