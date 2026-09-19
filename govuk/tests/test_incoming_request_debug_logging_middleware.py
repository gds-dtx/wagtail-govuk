from unittest.mock import patch

from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings

from govuk.middleware import IncomingRequestDebugLoggingMiddleware


class IncomingRequestDebugLoggingMiddlewareTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.middleware = IncomingRequestDebugLoggingMiddleware(
            lambda request: HttpResponse("ok")
        )

    def _logged_headers(self, mock_info):
        return {
            key.lower(): value for key, value in mock_info.call_args.args[3].items()
        }

    @override_settings(INCOMING_REQUEST_INFO_LOGGING=True)
    def test_the_proxy_chain_headers_are_logged_as_they_arrived(self):
        """Working out what CloudFront and the load balancer actually send is
        the reason this switch exists, so the headers that answer that are
        logged with their values."""
        request = self.factory.get(
            "/status/?check=true",
            HTTP_X_FORWARDED_FOR="203.0.113.7",
            HTTP_X_FORWARDED_PROTO="https",
            HTTP_CLOUDFRONT_VIEWER_COUNTRY="GB",
            HTTP_USER_AGENT="Mozilla/5.0",
        )

        with patch("govuk.middleware.logger.info") as mock_info:
            response = self.middleware(request)

        self.assertEqual(response.status_code, 200)
        mock_info.assert_called_once()
        logged_headers = self._logged_headers(mock_info)
        self.assertEqual(logged_headers.get("x-forwarded-for"), "203.0.113.7")
        self.assertEqual(logged_headers.get("x-forwarded-proto"), "https")
        self.assertEqual(logged_headers.get("cloudfront-viewer-country"), "GB")
        self.assertEqual(logged_headers.get("user-agent"), "Mozilla/5.0")

    @override_settings(INCOMING_REQUEST_INFO_LOGGING=True)
    def test_credential_headers_are_logged_by_name_and_length_only(self):
        """These lines go to CloudWatch and stay for the retention period, so a
        bearer token or a session cookie must not be one of them. The name and
        the length are what makes the log useful for debugging a proxy."""
        request = self.factory.get(
            "/status/",
            HTTP_AUTHORIZATION="Bearer test-token",
            HTTP_COOKIE="sessionid=abcdef123456",
        )

        with patch("govuk.middleware.logger.info") as mock_info:
            self.middleware(request)

        logged_headers = self._logged_headers(mock_info)
        self.assertEqual(logged_headers.get("authorization"), "[redacted, 17 chars]")
        self.assertEqual(logged_headers.get("cookie"), "[redacted, 22 chars]")
        self.assertNotIn("test-token", str(mock_info.call_args))
        self.assertNotIn("abcdef123456", str(mock_info.call_args))

    @override_settings(INCOMING_REQUEST_INFO_LOGGING=True)
    def test_a_header_nobody_listed_is_redacted_rather_than_logged(self):
        """The point of naming what to log rather than what to hide. Nobody
        here has heard of this header, and whoever adds the next single
        sign-on scheme will not be editing this middleware -- so its value
        must not reach CloudWatch on the strength of not being on a list of
        known credentials."""
        request = self.factory.get(
            "/status/", HTTP_X_ACME_SSO_ASSERTION="a-secret-nobody-anticipated"
        )

        with patch("govuk.middleware.logger.info") as mock_info:
            self.middleware(request)

        self.assertEqual(
            self._logged_headers(mock_info).get("x-acme-sso-assertion"),
            "[redacted, 27 chars]",
        )
        self.assertNotIn("a-secret-nobody-anticipated", str(mock_info.call_args))

    @override_settings(INCOMING_REQUEST_INFO_LOGGING=True)
    def test_an_empty_redacted_header_is_left_as_it_arrived(self):
        """A browser with no cookies for the site sends an empty Cookie header,
        and "[redacted, 0 chars]" would say less than the empty string does."""
        request = self.factory.get("/status/", HTTP_COOKIE="")

        with patch("govuk.middleware.logger.info") as mock_info:
            self.middleware(request)

        self.assertEqual(self._logged_headers(mock_info).get("cookie"), "")

    @override_settings(INCOMING_REQUEST_INFO_LOGGING=False)
    def test_does_not_log_when_info_logging_disabled(self):
        request = self.factory.get("/status/")

        with patch("govuk.middleware.logger.info") as mock_info:
            response = self.middleware(request)

        self.assertEqual(response.status_code, 200)
        mock_info.assert_not_called()
