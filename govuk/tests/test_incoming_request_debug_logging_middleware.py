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

    @override_settings(INCOMING_REQUEST_INFO_LOGGING=True)
    def test_logs_all_sent_headers_when_info_logging_enabled(self):
        request = self.factory.get(
            "/status/?check=true",
            HTTP_X_TRACE_ID="trace-123",
            HTTP_AUTHORIZATION="Bearer test-token",
        )

        with patch("govuk.middleware.logger.info") as mock_info:
            response = self.middleware(request)

        self.assertEqual(response.status_code, 200)
        mock_info.assert_called_once()
        logged_headers = mock_info.call_args.args[3]
        normalised_headers = {
            key.lower(): value for key, value in logged_headers.items()
        }
        self.assertEqual(normalised_headers.get("x-trace-id"), "trace-123")
        self.assertEqual(normalised_headers.get("authorization"), "Bearer test-token")

    @override_settings(INCOMING_REQUEST_INFO_LOGGING=False)
    def test_does_not_log_when_info_logging_disabled(self):
        request = self.factory.get("/status/")

        with patch("govuk.middleware.logger.info") as mock_info:
            response = self.middleware(request)

        self.assertEqual(response.status_code, 200)
        mock_info.assert_not_called()
