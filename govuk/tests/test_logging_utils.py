import json
import logging
import sys

from django.test import SimpleTestCase

from govuk.logging_utils import LoggingJSONFormatter, reorder_log_keys


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
