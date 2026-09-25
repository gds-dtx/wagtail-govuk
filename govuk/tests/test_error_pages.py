"""The four GOV.UK error pages: not found, problem, unavailable, and the
sign-in failure.

Until now there were none: production answered with Django's bare defaults.
Each follows the Design System's wording, carries the site's own header, and
offers the contact the site has configured -- or no contact at all, which is
what the other sites sharing this codebase get by default.
"""

from unittest.mock import patch

from allauth.socialaccount.helpers import render_authentication_error
from allauth.socialaccount.providers.base import AuthError
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import RequestFactory, TestCase, override_settings
from wagtail.models import Site

from govuk.models import ErrorPagesSettings, MaintenanceModeSettings
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
        error_pages = ErrorPagesSettings.for_site(self.site)
        error_pages.error_contact_link_text = (
            "Digital and Data Profession Capability Framework team"
        )
        error_pages.error_contact_email = (
            "digitalanddatacapabilityframework@dsit.gov.uk"
        )
        error_pages.error_contact_about = (
            "Government Digital and Data Capability Framework"
        )
        error_pages.save()

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


class NotFoundPageWordingTests(TestCase):
    """The heading and body an editor controls from Error pages settings.

    The Design System's wording is the field default, so a site that has
    never been touched reads exactly as it did before these fields existed.
    """

    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.client.raise_request_exception = False

    def _get_missing_page(self):
        return self.client.get("/this-page-does-not-exist/")

    def test_an_editors_heading_replaces_the_design_systems(self):
        error_pages = ErrorPagesSettings.for_site(self.site)
        error_pages.not_found_heading = "We cannot find that page"
        error_pages.save()

        response = self._get_missing_page()

        self.assertContains(response, "We cannot find that page", status_code=404)
        self.assertNotContains(response, "Page not found", status_code=404)

    def test_an_editors_body_replaces_the_design_systems(self):
        error_pages = ErrorPagesSettings.for_site(self.site)
        error_pages.not_found_body = "<p>Try the A to Z of skills instead.</p>"
        error_pages.save()

        response = self._get_missing_page()

        self.assertContains(response, "Try the A to Z of skills instead.", status_code=404)
        self.assertNotContains(
            response, "If you typed the web address", status_code=404
        )

    def test_clearing_the_body_leaves_the_heading_standing_alone(self):
        """A cleared field means the editor wants nothing there, not the
        default back again."""
        error_pages = ErrorPagesSettings.for_site(self.site)
        error_pages.not_found_body = ""
        error_pages.save()

        response = self._get_missing_page()

        self.assertContains(response, "Page not found", status_code=404)
        self.assertNotContains(
            response, "If you typed the web address", status_code=404
        )

    def test_a_cleared_heading_falls_back_rather_than_leaving_no_h1(self):
        """A page with no heading at all fails WCAG, so the default stands in."""
        error_pages = ErrorPagesSettings.for_site(self.site)
        error_pages.not_found_heading = ""
        error_pages.save()

        response = self._get_missing_page()

        self.assertContains(response, "Page not found", status_code=404)

    def test_the_heading_is_also_the_browser_title(self):
        error_pages = ErrorPagesSettings.for_site(self.site)
        error_pages.not_found_heading = "We cannot find that page"
        error_pages.save()

        response = self._get_missing_page()

        self.assertContains(
            response, "<title>", status_code=404
        )
        self.assertIn(
            "We cannot find that page",
            response.content.decode().split("</title>")[0],
        )

    def test_a_request_with_no_site_still_gets_the_design_systems_page(self):
        """With no site there are no settings to read, and the template has
        to stand on its own rather than render a headless page."""
        Site.objects.all().delete()

        response = self._get_missing_page()

        self.assertEqual(response.status_code, 404)
        body = response.content.decode()
        self.assertIn("Page not found", body)
        self.assertIn("If you typed the web address, check it is correct.", body)


class ServerErrorPageTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)

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

    def test_an_editors_heading_replaces_the_design_systems(self):
        error_pages = ErrorPagesSettings.for_site(self.site)
        error_pages.problem_heading = "Something has gone wrong at our end"
        error_pages.save()

        body = server_error(RequestFactory().get("/whatever/")).content.decode()

        self.assertIn("Something has gone wrong at our end", body)
        self.assertNotIn("Sorry, there is a problem with the service", body)

    def test_an_editors_body_replaces_the_design_systems(self):
        error_pages = ErrorPagesSettings.for_site(self.site)
        error_pages.problem_body = "<p>Please try again in a few minutes.</p>"
        error_pages.save()

        body = server_error(RequestFactory().get("/whatever/")).content.decode()

        self.assertIn("Please try again in a few minutes.", body)
        self.assertNotIn("Try again later.", body)

    def test_a_cleared_heading_falls_back_rather_than_leaving_no_h1(self):
        error_pages = ErrorPagesSettings.for_site(self.site)
        error_pages.problem_heading = ""
        error_pages.save()

        body = server_error(RequestFactory().get("/whatever/")).content.decode()

        self.assertIn("Sorry, there is a problem with the service", body)


@override_settings(MAINTENANCE_MODE=True)
class MaintenanceModeTests(TestCase):
    """The MAINTENANCE_MODE env var: the emergency hard close.

    It closes the service to everyone but the exempt paths -- signed-in staff
    included -- for when the admin or database cannot be relied on. The
    editor-driven toggle that lets staff through is covered by
    MaintenanceToggleTests.
    """

    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)

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

    def test_an_editors_heading_replaces_the_design_systems(self):
        unavailable = MaintenanceModeSettings.for_site(self.site)
        unavailable.heading = "The framework is down for maintenance"
        unavailable.save()

        response = self.client.get("/")

        self.assertContains(
            response, "The framework is down for maintenance", status_code=503
        )
        self.assertNotContains(
            response, "Sorry, the service is unavailable", status_code=503
        )

    def test_an_editors_body_shows_when_no_return_time_is_set(self):
        unavailable = MaintenanceModeSettings.for_site(self.site)
        unavailable.body = "<p>We are making some improvements.</p>"
        unavailable.save()

        response = self.client.get("/")

        self.assertContains(
            response, "We are making some improvements.", status_code=503
        )
        self.assertNotContains(
            response, "You will be able to use the service later.", status_code=503
        )

    @override_settings(MAINTENANCE_RESUME_TEXT="9am on Monday 19 November 2018")
    def test_a_known_return_time_wins_over_the_editors_body(self):
        """The return time is set for this outage, so it takes precedence over
        any standing body wording an editor has left."""
        unavailable = MaintenanceModeSettings.for_site(self.site)
        unavailable.body = "<p>We are making some improvements.</p>"
        unavailable.save()

        response = self.client.get("/")

        self.assertContains(
            response,
            "You will be able to use the service from 9am on "
            "Monday 19 November 2018.",
            status_code=503,
        )
        self.assertNotContains(
            response, "We are making some improvements.", status_code=503
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

    def test_the_exempt_roots_stay_reachable_without_their_trailing_slash(self):
        """/admin should reach APPEND_SLASH, not the unavailable page."""
        for path in ("/admin", "/django-admin", "/accounts", "/api/health"):
            response = self.client.get(path)
            self.assertNotEqual(response.status_code, 503, path)

    def test_a_page_that_merely_starts_with_an_exempt_word_is_closed(self):
        """The prefixes are path segments, not string prefixes.

        Without the trailing slash "/admin" also matches "/admin-guidance",
        which would leave content pages open through a cutover.
        """
        for path in (
            "/admin-guidance/",
            "/accounts-payable/",
            "/assets-register/",
            "/static-content/",
            "/generalist/",
        ):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 503, path)

    def test_the_unavailable_page_says_when_to_come_back(self):
        """RFC 9110 Retry-After, so a crawler does not hammer a closed service."""
        response = self.client.get("/")

        self.assertEqual(response["Retry-After"], "3600")

    @override_settings(MAINTENANCE_RETRY_AFTER=120)
    def test_the_retry_window_is_configurable(self):
        response = self.client.get("/")

        self.assertEqual(response["Retry-After"], "120")

    def test_the_env_override_closes_the_site_even_to_signed_in_users(self):
        """The emergency override is a hard close: unlike the admin toggle it
        does not let signed-in staff through."""
        user = get_user_model().objects.create_user(
            username="staff", password="unused-password"
        )
        self.client.force_login(user)

        response = self.client.get("/")

        self.assertEqual(response.status_code, 503)

    @override_settings(MAINTENANCE_MODE=False)
    def test_switched_off_the_service_answers_normally(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)


class MaintenanceToggleTests(TestCase):
    """The "Maintenance mode" admin setting: planned maintenance.

    Closing the site from the admin answers readers with the unavailable page
    but lets CMS staff (admins, moderators, editors) carry on, and keeps the
    health check and the page's own dressing open. A visitor who has only
    passed SSO, with no admin access, is closed out like any other reader.
    """

    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        maintenance = MaintenanceModeSettings.for_site(self.site)
        maintenance.enabled = True
        maintenance.save()

    def test_readers_meet_the_unavailable_page(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 503)
        self.assertContains(
            response, "Sorry, the service is unavailable", status_code=503
        )
        self.assertContains(
            response, "You will be able to use the service later.", status_code=503
        )

    def test_cms_staff_are_let_through(self):
        editor = get_user_model().objects.create_user(
            username="editor", password="unused-password"
        )
        # The Editors group carries wagtailadmin.access_admin, which is what
        # the middleware checks -- as Moderators and superusers also do.
        editor.groups.add(Group.objects.get(name="Editors"))
        self.client.force_login(editor)

        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)

    def test_a_plain_sso_user_without_admin_access_is_closed_out(self):
        """Passing SSO is not enough: someone with no CMS access is a reader."""
        user = get_user_model().objects.create_user(
            username="sso-only", password="unused-password"
        )
        self.client.force_login(user)

        response = self.client.get("/")

        self.assertEqual(response.status_code, 503)

    def test_the_editors_wording_shows(self):
        maintenance = MaintenanceModeSettings.for_site(self.site)
        maintenance.heading = "The framework is down for maintenance"
        maintenance.body = "<p>We are making some improvements.</p>"
        maintenance.save()

        response = self.client.get("/")

        self.assertContains(
            response, "The framework is down for maintenance", status_code=503
        )
        self.assertContains(
            response, "We are making some improvements.", status_code=503
        )

    def test_the_health_check_and_dressing_stay_open(self):
        for path in (
            "/api/health/",
            "/assets/images/govuk-crest.svg",
            "/gen/custom.css",
            "/static/main.css",
        ):
            response = self.client.get(path)
            self.assertNotEqual(response.status_code, 503, path)

    def test_switched_off_the_service_answers_normally(self):
        maintenance = MaintenanceModeSettings.for_site(self.site)
        maintenance.enabled = False
        maintenance.save()

        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)


class SignInErrorPageTests(TestCase):
    """django-allauth's authentication_error page, dressed as this service.

    allauth renders it at status 401 and, left alone, in its own bare HTML:
    "Third-Party Login Failure" with a "Menu:" list and no stylesheet. The
    override has to keep the status -- CloudFront and the load balancer
    count it -- while looking like the rest of the site.
    """

    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)

    def _render(self, error=AuthError.UNKNOWN):
        request = RequestFactory().get(
            "/accounts/oidc/internal-access/login/callback/?code=abc&state=xyz"
        )
        return render_authentication_error(request, "openid_connect", error=error)

    def test_a_failed_sign_in_still_answers_unauthorised(self):
        response = self._render()

        self.assertEqual(response.status_code, 401)

    def test_allauths_bare_default_no_longer_reaches_the_reader(self):
        body = self._render().content.decode()

        self.assertNotIn("Third-Party Login Failure", body)
        self.assertNotIn("<strong>Menu:</strong>", body)

    def test_it_is_the_services_own_page(self):
        body = self._render().content.decode()

        self.assertIn("There is a problem signing you in", body)
        self.assertIn("govuk-heading-l", body)
        self.assertIn("govuk-template", body)

    def test_an_expired_or_replayed_link_is_named_as_the_likely_cause(self):
        """The commonest arrival is a reloaded callback URL, whose one-use
        code is already spent. Saying so stops a reader concluding their
        account is the problem."""
        body = self._render().content.decode()

        self.assertIn(
            "The sign-in link you used has expired or has already been used.", body
        )

    def test_a_refusal_is_not_described_as_an_expired_link(self):
        body = self._render(error=AuthError.DENIED).content.decode()

        self.assertIn("You did not give permission to sign in", body)
        self.assertNotIn("has already been used", body)

    def test_the_reader_is_offered_the_way_back_in(self):
        body = self._render().content.decode()

        # The same entry point the header's "Sign in" link uses, which
        # starts the OIDC round trip again rather than replaying the
        # spent callback URL.
        self.assertIn('href="/accounts/login/"', body)
        self.assertIn("Try signing in again", body)

    def test_no_contact_is_offered_until_one_is_configured(self):
        self.assertNotIn("mailto:", self._render().content.decode())

    def test_the_configured_contact_closes_the_page(self):
        error_pages = ErrorPagesSettings.for_site(self.site)
        error_pages.error_contact_link_text = (
            "Digital and Data Profession Capability Framework team"
        )
        error_pages.error_contact_email = (
            "digitalanddatacapabilityframework@dsit.gov.uk"
        )
        error_pages.save()

        body = self._render().content.decode()

        self.assertIn("If this keeps happening, contact the", body)
        self.assertIn(
            'href="mailto:digitalanddatacapabilityframework@dsit.gov.uk"', body
        )
