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
    def test_logs_all_sent_headers_when_info_logging_enabled(self):
        request = self.factory.get(
            "/status/?check=true",
            HTTP_X_TRACE_ID="trace-123",
            HTTP_X_FORWARDED_FOR="203.0.113.7",
        )

        with patch("govuk.middleware.logger.info") as mock_info:
            response = self.middleware(request)

        self.assertEqual(response.status_code, 200)
        mock_info.assert_called_once()
        logged_headers = self._logged_headers(mock_info)
        self.assertEqual(logged_headers.get("x-trace-id"), "trace-123")
        # The proxy chain is what this switch is for, so it is logged as sent.
        self.assertEqual(logged_headers.get("x-forwarded-for"), "203.0.113.7")

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
    def test_an_empty_credential_header_is_left_as_it_arrived(self):
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
