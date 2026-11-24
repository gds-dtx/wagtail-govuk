"""robots.txt, security.txt, CORS on /.well-known/, and HEAD on the views this app routes itself."""

from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import resolve, reverse
from wagtail.models import Site

from govuk.middleware import CorsMiddleware
from govuk.models import CustomiseSettings, EdDSAKeyPair, EdDSAKeySettings
from govuk.tests.helpers import feature_flags
from govuk.view_securitytxt import security_txt_view


class RobotsTxtViewTests(TestCase):
    def test_noindex_decides_what_crawlers_are_told(self):
        cases = [
            (
                True,
                "User-agent: *\nUser-agent: Googlebot\nUser-agent: AdsBot-Google\nDisallow: /\n",
            ),
            (False, "User-agent: *\nDisallow: /admin\nAllow: /\n"),
        ]
        for noindex, expected in cases:
            with self.subTest(noindex=noindex), override_settings(NOINDEX=noindex):
                response = self.client.get(reverse("govuk_robots_txt"))

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response["Content-Type"], "text/plain; charset=utf-8")
                self.assertEqual(response.content.decode("utf-8"), expected)


class SecurityTxtViewTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_redirects_to_the_securitytxt_location(self):
        cases = [
            (
                {},
                "https://vulnerability-reporting.service.security.gov.uk/.well-known/security.txt",
            ),
            (
                {"SECURITYTXT_LOCATION": "https://example.gov.uk/.well-known/security.txt"},
                "https://example.gov.uk/.well-known/security.txt",
            ),
        ]
        for overrides, expected in cases:
            with self.subTest(overrides=overrides), override_settings(**overrides):
                response = security_txt_view(self.factory.get("/.well-known/security.txt"))

                self.assertEqual(response.status_code, 301)
                self.assertEqual(response["Location"], expected)

    def test_well_known_path_resolves_to_security_view_name(self):
        match = resolve("/.well-known/security.txt")

        self.assertEqual(match.view_name, "govuk_security_txt")


class CorsMiddlewareTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.middleware = CorsMiddleware(lambda request: HttpResponse("ok"))

    def test_sets_wildcard_origin_header_for_well_known_paths(self):
        response = self.middleware(self.factory.get("/.well-known/jwks.json"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Access-Control-Allow-Origin"], "*")

    def test_leaves_other_paths_unchanged(self):
        response = self.middleware(self.factory.get("/search/"))

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Access-Control-Allow-Origin", response)

    def test_preserves_existing_origin_header_for_well_known_paths(self):
        middleware = CorsMiddleware(
            lambda request: HttpResponse(
                "ok",
                headers={"Access-Control-Allow-Origin": "https://example.com"},
            )
        )

        response = middleware(self.factory.get("/.well-known/jwks.json"))

        self.assertEqual(response["Access-Control-Allow-Origin"], "https://example.com")


@override_settings(FEATURE_FLAGS=feature_flags(skills=True))
class HeadRequestTests(TestCase):
    """HEAD answers wherever GET does.

    Link checkers and uptime monitors ask with HEAD by preference, and views
    decorated ``require_http_methods(["GET"])`` answer it with a 405.
    """

    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.site.hostname = "testserver"
        self.site.port = 80
        self.site.save(update_fields=["hostname", "port"])

    def test_each_view_answers_head_as_it_answers_get(self):
        customise = CustomiseSettings.for_site(self.site)
        customise.extra_css = ".masthead { background: #0b0c0c; }"
        customise.save()
        EdDSAKeyPair.generate_for_settings(settings_obj=EdDSAKeySettings.for_site(self.site))

        cases = [
            (reverse("search"), 200),
            (reverse("search") + "?query=designer", 200),
            (reverse("govuk_custom_css"), 200),
            (reverse("govuk_jwks"), 200),
            *(
                (reverse("govuk_framework_csv", args=[name]), 200)
                for name in ("roles", "skills", "changelog")
            ),
            (reverse("govuk_robots_txt"), 200),
            ("/.well-known/security.txt", 301),
        ]
        for url, status in cases:
            with self.subTest(url=url):
                get_response = self.client.get(url)
                head_response = self.client.head(url)

                # Django strips a HEAD body itself; the status is what a 405 would break.
                self.assertEqual(get_response.status_code, status)
                self.assertEqual(head_response.status_code, status)
                self.assertEqual(head_response.content, b"")

    def test_head_on_a_missing_download_is_still_a_404(self):
        """Not a 405 dressed up as a 404: the view must reach its own Http404."""
        response = self.client.head(reverse("govuk_framework_csv", args=["nope"]))

        self.assertEqual(response.status_code, 404)

    def test_post_is_still_rejected(self):
        """Widening to HEAD must not widen to anything else."""
        for url in (
            reverse("search"),
            reverse("govuk_custom_css"),
            reverse("govuk_jwks"),
            reverse("govuk_framework_csv", args=["roles"]),
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.post(url).status_code, 405)
