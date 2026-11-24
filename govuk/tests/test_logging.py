import json
import logging
import sys
from unittest.mock import patch

from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings

from govuk.logging_utils import LoggingJSONFormatter, reorder_log_keys
from govuk.middleware import IncomingRequestDebugLoggingMiddleware


class ReorderLogKeysTests(SimpleTestCase):
    def test_moves_core_fields_to_front(self):
        event_dict = {
            "request_id": "req-123",
            "event": "processed",
            "level": "info",
            "_datetime": "2026-02-22T12:00:00.000Z",
        }

        ordered = reorder_log_keys(event_dict)

        self.assertEqual(list(ordered.keys())[:3], ["_datetime", "level", "event"])
        self.assertEqual(ordered["request_id"], "req-123")


class LoggingJSONFormatterTests(SimpleTestCase):
    def setUp(self):
        self.formatter = LoggingJSONFormatter()
        self.logger = logging.getLogger("govuk.tests")

    def test_formats_log_record_as_logging_json(self):
        record = self.logger.makeRecord(
            name=self.logger.name,
            level=logging.INFO,
            fn=__file__,
            lno=12,
            msg="Request completed: %s",
            args=("ok",),
            exc_info=None,
            extra={"request_id": "req-123"},
        )
        record.created = 0

        payload = json.loads(self.formatter.format(record))

        self.assertEqual(list(payload.keys())[:3], ["_datetime", "level", "event"])
        self.assertEqual(payload["_datetime"], "1970-01-01T00:00:00.000Z")
        self.assertEqual(payload["level"], "info")
        self.assertEqual(payload["event"], "Request completed: ok")
        self.assertEqual(payload["logger"], "govuk.tests")
        self.assertEqual(payload["request_id"], "req-123")

    def test_includes_exception_details_when_present(self):
        try:
            raise ValueError("boom")
        except ValueError:
            exc_info = sys.exc_info()

        record = self.logger.makeRecord(
            name=self.logger.name,
            level=logging.ERROR,
            fn=__file__,
            lno=41,
            msg="Unhandled error",
            args=(),
            exc_info=exc_info,
            extra=None,
        )

        payload = json.loads(self.formatter.format(record))

        self.assertIn("exception", payload)
        self.assertIn("ValueError: boom", payload["exception"])


class IncomingRequestDebugLoggingMiddlewareTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.middleware = IncomingRequestDebugLoggingMiddleware(
            lambda request: HttpResponse("ok")
        )

    def _log(self, request):
        """The response, the mocked ``logger.info`` and the logged headers (lower-cased)."""
        with patch("govuk.middleware.logger.info") as mock_info:
            response = self.middleware(request)
        headers = (
            {key.lower(): value for key, value in mock_info.call_args.args[3].items()}
            if mock_info.called
            else None
        )
        return response, mock_info, headers

    @override_settings(INCOMING_REQUEST_INFO_LOGGING=True)
    def test_the_proxy_chain_headers_are_logged_as_they_arrived(self):
        """Seeing what CloudFront and the load balancer send is why this switch exists."""
        response, mock_info, headers = self._log(
            self.factory.get(
                "/status/?check=true",
                HTTP_X_FORWARDED_FOR="203.0.113.7",
                HTTP_X_FORWARDED_PROTO="https",
                HTTP_CLOUDFRONT_VIEWER_COUNTRY="GB",
                HTTP_USER_AGENT="Mozilla/5.0",
            )
        )

        self.assertEqual(response.status_code, 200)
        mock_info.assert_called_once()
        self.assertEqual(headers.get("x-forwarded-for"), "203.0.113.7")
        self.assertEqual(headers.get("x-forwarded-proto"), "https")
        self.assertEqual(headers.get("cloudfront-viewer-country"), "GB")
        self.assertEqual(headers.get("user-agent"), "Mozilla/5.0")

    @override_settings(INCOMING_REQUEST_INFO_LOGGING=True)
    def test_other_headers_are_logged_by_name_and_length_only(self):
        # These lines stay in CloudWatch for the retention period. Headers are
        # logged by an allow-list, so a header nobody anticipated (the next
        # single sign-on scheme's, say) is redacted along with known credentials.
        cases = [
            ("authorization", {"HTTP_AUTHORIZATION": "Bearer test-token"}, "test-token", 17),
            ("cookie", {"HTTP_COOKIE": "sessionid=abcdef123456"}, "abcdef123456", 22),
            (
                "x-acme-sso-assertion",
                {"HTTP_X_ACME_SSO_ASSERTION": "a-secret-nobody-anticipated"},
                "a-secret-nobody-anticipated",
                27,
            ),
        ]
        for header, request_headers, secret, length in cases:
            with self.subTest(header):
                _, mock_info, headers = self._log(self.factory.get("/status/", **request_headers))

                self.assertEqual(headers.get(header), f"[redacted, {length} chars]")
                self.assertNotIn(secret, str(mock_info.call_args))

    @override_settings(INCOMING_REQUEST_INFO_LOGGING=True)
    def test_an_empty_redacted_header_is_left_as_it_arrived(self):
        """A browser with no cookies sends an empty Cookie header; "[redacted, 0 chars]" says less."""
        _, _, headers = self._log(self.factory.get("/status/", HTTP_COOKIE=""))

        self.assertEqual(headers.get("cookie"), "")

    @override_settings(INCOMING_REQUEST_INFO_LOGGING=False)
    def test_does_not_log_when_info_logging_disabled(self):
        response, mock_info, _ = self._log(self.factory.get("/status/"))

        self.assertEqual(response.status_code, 200)
        mock_info.assert_not_called()
