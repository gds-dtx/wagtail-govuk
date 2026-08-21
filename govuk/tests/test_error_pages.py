"""The three GOV.UK error pages: not found, problem, and unavailable.

Until now there were none: production answered with Django's bare defaults.
Each follows the Design System's wording, carries the site's own header, and
offers the contact the site has configured -- or no contact at all, which is
what the other sites sharing this codebase get by default.
"""

from unittest.mock import patch

from django.test import RequestFactory, TestCase, override_settings
from wagtail.models import Site

from govuk.models import CustomiseSettings
from govuk.views import server_error


class NotFoundPageTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)

    def _get_missing_page(self):
        # raise_request_exception off so the response is the rendered 404,
        # the way a reader meets it, rather than a re-raised Http404.
        self.client.raise_request_exception = False
        return self.client.get("/this-page-does-not-exist/")

    def test_a_missing_page_gets_the_design_systems_wording(self):
        response = self._get_missing_page()

        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "Page not found", status_code=404)
        self.assertContains(
            response, "If you typed the web address, check it is correct.",
            status_code=404,
        )
        self.assertContains(
            response,
            "If you pasted the web address, check you copied the entire address.",
            status_code=404,
        )

    def test_no_contact_is_offered_until_one_is_configured(self):
        response = self._get_missing_page()

        self.assertNotContains(response, "mailto:", status_code=404)

    def test_the_configured_contact_closes_the_page(self):
        customise = CustomiseSettings.for_site(self.site)
        customise.error_contact_link_text = (
            "Digital and Data Profession Capability Framework team"
        )
        customise.error_contact_email = (
            "digitalanddatacapabilityframework@dsit.gov.uk"
        )
        customise.error_contact_about = (
            "Government Digital and Data Capability Framework"
        )
        customise.save()

        response = self._get_missing_page()

        self.assertContains(
            response,
            "If the web address is correct or you selected a link or button, "
            "contact the",
            status_code=404,
        )
        self.assertContains(
            response,
            'href="mailto:digitalanddatacapabilityframework@dsit.gov.uk"',
            status_code=404,
        )
        self.assertContains(
            response,
            "if you need to speak to someone about the "
            "Government Digital and Data Capability Framework",
            status_code=404,
        )


class ServerErrorPageTests(TestCase):
    def test_the_branded_page_renders_with_the_request(self):
        request = RequestFactory().get("/whatever/")

        response = server_error(request)

        self.assertEqual(response.status_code, 500)
        body = response.content.decode()
        self.assertIn("Sorry, there is a problem with the service", body)
        self.assertIn("Try again later.", body)

    def test_a_render_failure_still_answers_with_the_bare_page(self):
        """The 500 page's one job is to exist when nothing else does."""
        request = RequestFactory().get("/whatever/")

        with patch("govuk.views.render", side_effect=Exception("database gone")):
            response = server_error(request)

        self.assertEqual(response.status_code, 500)
        body = response.content.decode()
        self.assertIn("Sorry, there is a problem with the service", body)
        self.assertIn("Try again later.", body)


@override_settings(MAINTENANCE_MODE=True)
class MaintenanceModeTests(TestCase):
    def test_the_service_answers_unavailable(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 503)
        self.assertContains(
            response, "Sorry, the service is unavailable", status_code=503
        )
        self.assertContains(
            response, "You will be able to use the service later.", status_code=503
        )

    @override_settings(MAINTENANCE_RESUME_TEXT="9am on Monday 19 November 2018")
    def test_a_known_return_names_the_moment(self):
        response = self.client.get("/")

        self.assertContains(
            response,
            "You will be able to use the service from 9am on "
            "Monday 19 November 2018.",
            status_code=503,
        )

    def test_the_health_check_stays_open(self):
        response = self.client.get("/api/health/")

        self.assertEqual(response.status_code, 200)

    def test_the_pages_own_dressing_stays_open(self):
        """The fonts, crest and customised styles the 503 page itself loads."""
        for path in ("/assets/images/govuk-crest.svg", "/gen/custom.css", "/static/main.css"):
            response = self.client.get(path)
            self.assertNotEqual(response.status_code, 503, path)

    def test_the_admin_stays_reachable(self):
        response = self.client.get("/admin/")

        # A redirect to sign in, not the unavailable page: the people doing
        # the maintenance still get through.
        self.assertNotEqual(response.status_code, 503)

    @override_settings(MAINTENANCE_MODE=False)
    def test_switched_off_the_service_answers_normally(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
