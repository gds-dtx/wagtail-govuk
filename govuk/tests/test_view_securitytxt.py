from django.test import RequestFactory, SimpleTestCase, override_settings
from django.urls import resolve

from govuk.view_securitytxt import security_txt_view


class SecurityTxtViewTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_redirects_to_default_securitytxt_location(self):
        request = self.factory.get("/.well-known/security.txt")
        response = security_txt_view(request)

        self.assertEqual(response.status_code, 301)
        self.assertEqual(
            response["Location"],
            "https://vulnerability-reporting.service.security.gov.uk/.well-known/security.txt",
        )

    @override_settings(
        SECURITYTXT_LOCATION="https://example.gov.uk/.well-known/security.txt"
    )
    def test_redirects_to_configured_securitytxt_location(self):
        request = self.factory.get("/.well-known/security.txt")
        response = security_txt_view(request)

        self.assertEqual(response.status_code, 301)
        self.assertEqual(
            response["Location"],
            "https://example.gov.uk/.well-known/security.txt",
        )

    def test_head_request_is_allowed(self):
        request = self.factory.head("/.well-known/security.txt")
        response = security_txt_view(request)

        self.assertEqual(response.status_code, 301)

    def test_well_known_path_resolves_to_security_view_name(self):
        match = resolve("/.well-known/security.txt")

        self.assertEqual(match.view_name, "govuk_security_txt")
