"""The GOV.UK error pages: not found, problem, unavailable, and the sign-in failure.

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

CONTACT_LINK_TEXT = "Digital and Data Profession Capability Framework team"
CONTACT_EMAIL = "digitalanddatacapabilityframework@dsit.gov.uk"
CONTACT_ABOUT = "Government Digital and Data Capability Framework"


class ErrorPageMixin:
    """Renders one error page; subclasses name its status and how it is reached."""

    status_code: int

    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)

    def render_page(self):
        raise NotImplementedError

    def page_body(self) -> str:
        response = self.render_page()
        self.assertEqual(response.status_code, self.status_code)
        return response.content.decode()


class ErrorPageWordingMixin(ErrorPageMixin):
    """The heading and body an editor controls; the Design System's wording is the default."""

    settings_model: type
    heading_field: str
    body_field: str
    default_heading: str
    default_body: tuple[str, ...]

    def _set(self, **values):
        settings = self.settings_model.for_site(self.site)
        for field, value in values.items():
            setattr(settings, field, value)
        settings.save()

    def test_an_editors_heading_replaces_the_design_systems(self):
        self._set(**{self.heading_field: "Our own heading"})

        body = self.page_body()

        self.assertIn("Our own heading", body)
        self.assertNotIn(self.default_heading, body)

    def test_the_heading_is_also_the_browser_title(self):
        self._set(**{self.heading_field: "Our own heading"})

        title = self.page_body().split("</title>")[0]

        self.assertIn("<title>", title)
        self.assertIn("Our own heading", title)

    def test_an_editors_body_replaces_the_design_systems(self):
        self._set(**{self.body_field: "<p>Our own body wording.</p>"})

        body = self.page_body()

        self.assertIn("Our own body wording.", body)
        for line in self.default_body:
            self.assertNotIn(line, body)

    def test_clearing_the_body_leaves_the_heading_standing_alone(self):
        """A cleared field means the editor wants nothing there, not the default back again."""
        self._set(**{self.body_field: ""})

        body = self.page_body()

        self.assertIn(self.default_heading, body)
        for line in self.default_body:
            self.assertNotIn(line, body)

    def test_a_cleared_heading_falls_back_rather_than_leaving_no_h1(self):
        """A page with no heading at all fails WCAG, so the default stands in."""
        self._set(**{self.heading_field: ""})

        self.assertIn(self.default_heading, self.page_body())


class ErrorContactMixin(ErrorPageMixin):
    """The optional contact sentence each error page closes with."""

    contact_lead: str

    def test_no_contact_is_offered_until_one_is_configured(self):
        self.assertNotIn("mailto:", self.page_body())

    def test_the_configured_contact_closes_the_page(self):
        error_pages = ErrorPagesSettings.for_site(self.site)
        error_pages.error_contact_link_text = CONTACT_LINK_TEXT
        error_pages.error_contact_email = CONTACT_EMAIL
        error_pages.error_contact_about = CONTACT_ABOUT
        error_pages.save()

        body = self.page_body()

        self.assertIn(self.contact_lead, body)
        self.assertIn(f'href="mailto:{CONTACT_EMAIL}"', body)
        self.assertIn(f"if you need to speak to someone about the {CONTACT_ABOUT}", body)


class MaintenanceClosureMixin:
    """Behaviour shared by both ways of closing the service."""

    def switch_off(self):
        raise NotImplementedError

    def test_readers_meet_the_unavailable_page(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 503)
        self.assertContains(response, "Sorry, the service is unavailable", status_code=503)
        self.assertContains(response, "You will be able to use the service later.", status_code=503)

    def test_the_health_check_and_the_pages_own_dressing_stay_open(self):
        """The dressing is the fonts, crest and customised styles the 503 page itself loads."""
        self.assertEqual(self.client.get("/api/health/").status_code, 200)
        for path in ("/assets/images/govuk-crest.svg", "/gen/custom.css", "/static/main.css"):
            response = self.client.get(path)
            self.assertNotEqual(response.status_code, 503, path)

    def test_switched_off_the_service_answers_normally(self):
        self.switch_off()

        self.assertEqual(self.client.get("/").status_code, 200)


class NotFoundPageTests(ErrorPageWordingMixin, ErrorContactMixin, TestCase):
    status_code = 404
    settings_model = ErrorPagesSettings
    heading_field = "not_found_heading"
    body_field = "not_found_body"
    default_heading = "Page not found"
    default_body = (
        "If you typed the web address, check it is correct.",
        "If you pasted the web address, check you copied the entire address.",
    )
    contact_lead = "If the web address is correct or you selected a link or button, contact the"

    def setUp(self):
        super().setUp()
        # Off so the response is the rendered 404, the way a reader meets it,
        # rather than a re-raised Http404.
        self.client.raise_request_exception = False

    def render_page(self):
        return self.client.get("/this-page-does-not-exist/")

    def test_a_request_with_no_site_still_gets_the_design_systems_page(self):
        """With no site there are no settings to read, so the template has to stand on its own."""
        Site.objects.all().delete()

        body = self.page_body()

        self.assertIn("Page not found", body)
        self.assertIn("If you typed the web address, check it is correct.", body)


class ServerErrorPageTests(ErrorPageWordingMixin, ErrorContactMixin, TestCase):
    status_code = 500
    settings_model = ErrorPagesSettings
    heading_field = "problem_heading"
    body_field = "problem_body"
    default_heading = "Sorry, there is a problem with the service"
    default_body = ("Try again later.",)
    contact_lead = "Contact the"

    def render_page(self):
        return server_error(RequestFactory().get("/whatever/"))

    def test_a_render_failure_still_answers_with_the_bare_page(self):
        """The 500 page's one job is to exist when nothing else does."""
        with patch("govuk.views.render", side_effect=Exception("database gone")):
            body = self.page_body()

        self.assertIn("Sorry, there is a problem with the service", body)
        self.assertIn("Try again later.", body)


@override_settings(MAINTENANCE_MODE=True)
class MaintenanceModeTests(ErrorPageWordingMixin, ErrorContactMixin, MaintenanceClosureMixin, TestCase):
    """The MAINTENANCE_MODE env var: the emergency hard close.

    It closes the service to everyone but the exempt paths -- signed-in staff
    included -- for when the admin or database cannot be relied on.
    """

    status_code = 503
    settings_model = MaintenanceModeSettings
    heading_field = "heading"
    body_field = "body"
    default_heading = "Sorry, the service is unavailable"
    default_body = ("You will be able to use the service later.",)
    contact_lead = "Contact the"

    def render_page(self):
        return self.client.get("/")

    def switch_off(self):
        self.enterContext(override_settings(MAINTENANCE_MODE=False))

    @override_settings(MAINTENANCE_RESUME_TEXT="9am on Monday 19 November 2018")
    def test_a_known_return_names_the_moment(self):
        self.assertIn(
            "You will be able to use the service from 9am on Monday 19 November 2018.",
            self.page_body(),
        )

    @override_settings(MAINTENANCE_RESUME_TEXT="9am on Monday 19 November 2018")
    def test_a_known_return_time_wins_over_the_editors_body(self):
        """The return time is set for this outage, so it beats any standing body wording."""
        self._set(body="<p>We are making some improvements.</p>")

        body = self.page_body()

        self.assertIn("You will be able to use the service from 9am on Monday 19 November 2018.", body)
        self.assertNotIn("We are making some improvements.", body)

    def test_the_admin_stays_reachable(self):
        # A redirect to sign in, not the unavailable page: the people doing
        # the maintenance still get through.
        self.assertNotEqual(self.client.get("/admin/").status_code, 503)

    def test_the_exempt_roots_stay_reachable_without_their_trailing_slash(self):
        """/admin should reach APPEND_SLASH, not the unavailable page."""
        for path in ("/admin", "/django-admin", "/accounts", "/api/health"):
            response = self.client.get(path)
            self.assertNotEqual(response.status_code, 503, path)

    def test_a_page_that_merely_starts_with_an_exempt_word_is_closed(self):
        """The prefixes are path segments: "/admin" must not also open "/admin-guidance"."""
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
        self.assertEqual(self.client.get("/")["Retry-After"], "3600")

    @override_settings(MAINTENANCE_RETRY_AFTER=120)
    def test_the_retry_window_is_configurable(self):
        self.assertEqual(self.client.get("/")["Retry-After"], "120")

    def test_the_env_override_closes_the_site_even_to_signed_in_users(self):
        """Unlike the admin toggle, the emergency override does not let signed-in staff through."""
        user = get_user_model().objects.create_user(username="staff", password="unused-password")
        self.client.force_login(user)

        self.assertEqual(self.client.get("/").status_code, 503)


class MaintenanceToggleTests(MaintenanceClosureMixin, TestCase):
    """The "Maintenance mode" admin setting: planned maintenance.

    Readers meet the unavailable page; CMS staff (admins, moderators, editors)
    carry on. A visitor who has only passed SSO, with no admin access, is a reader.
    """

    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self._set_enabled(True)

    def _set_enabled(self, enabled):
        maintenance = MaintenanceModeSettings.for_site(self.site)
        maintenance.enabled = enabled
        maintenance.save()

    def switch_off(self):
        self._set_enabled(False)

    def test_cms_staff_are_let_through(self):
        editor = get_user_model().objects.create_user(username="editor", password="unused-password")
        # The Editors group carries wagtailadmin.access_admin, which is what
        # the middleware checks -- as Moderators and superusers also do.
        editor.groups.add(Group.objects.get(name="Editors"))
        self.client.force_login(editor)

        self.assertEqual(self.client.get("/").status_code, 200)

    def test_a_plain_sso_user_without_admin_access_is_closed_out(self):
        user = get_user_model().objects.create_user(username="sso-only", password="unused-password")
        self.client.force_login(user)

        self.assertEqual(self.client.get("/").status_code, 503)

    def test_the_editors_wording_shows(self):
        maintenance = MaintenanceModeSettings.for_site(self.site)
        maintenance.heading = "The framework is down for maintenance"
        maintenance.body = "<p>We are making some improvements.</p>"
        maintenance.save()

        response = self.client.get("/")

        self.assertContains(response, "The framework is down for maintenance", status_code=503)
        self.assertContains(response, "We are making some improvements.", status_code=503)


class SignInErrorPageTests(ErrorContactMixin, TestCase):
    """django-allauth's authentication_error page, dressed as this service.

    allauth renders it at status 401 and, left alone, in its own bare HTML. The
    override has to keep the status -- CloudFront and the load balancer count
    it -- while looking like the rest of the site.
    """

    status_code = 401
    contact_lead = "If this keeps happening, contact the"

    def render_page(self, error=AuthError.UNKNOWN):
        request = RequestFactory().get("/accounts/oidc/internal-access/login/callback/?code=abc&state=xyz")
        return render_authentication_error(request, "openid_connect", error=error)

    def test_allauths_bare_default_no_longer_reaches_the_reader(self):
        body = self.page_body()

        self.assertNotIn("Third-Party Login Failure", body)
        self.assertNotIn("<strong>Menu:</strong>", body)

    def test_it_is_the_services_own_page(self):
        body = self.page_body()

        self.assertIn("There is a problem signing you in", body)
        self.assertIn("govuk-heading-l", body)
        self.assertIn("govuk-template", body)

    def test_an_expired_or_replayed_link_is_named_as_the_likely_cause(self):
        """The commonest arrival is a reloaded callback URL, whose one-use code is already spent."""
        self.assertIn("The sign-in link you used has expired or has already been used.", self.page_body())

    def test_a_refusal_is_not_described_as_an_expired_link(self):
        response = self.render_page(error=AuthError.DENIED)
        body = response.content.decode()

        self.assertEqual(response.status_code, 401)
        self.assertIn("You did not give permission to sign in", body)
        self.assertNotIn("has already been used", body)

    def test_the_reader_is_offered_the_way_back_in(self):
        body = self.page_body()

        # The header's "Sign in" entry point, which starts the OIDC round trip
        # again rather than replaying the spent callback URL.
        self.assertIn('href="/accounts/login/"', body)
        self.assertIn("Try signing in again", body)
