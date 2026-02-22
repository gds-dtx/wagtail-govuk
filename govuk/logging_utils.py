from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any, Mapping

CORE_LOG_KEYS = ("_datetime", "level", "event")
_STANDARD_LOG_RECORD_KEYS = frozenset(
    logging.LogRecord(
        name="",
        level=0,
        pathname="",
        lineno=0,
        msg="",
        args=(),
        exc_info=None,
    ).__dict__.keys()
) | {"message", "asctime"}


def reorder_log_keys(event_dict: Mapping[str, Any]) -> dict[str, Any]:
    ordered: dict[str, Any] = {}
    for key in CORE_LOG_KEYS:
        if key in event_dict:
            ordered[key] = event_dict[key]
    for key, value in event_dict.items():
        if key not in ordered:
            ordered[key] = value
    return ordered


class LoggingJSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        event_dict: dict[str, Any] = {
            "_datetime": self._format_timestamp(record.created),
            "level": record.levelname.lower(),
            "event": record.getMessage(),
            "logger": record.name,
        }

        event_dict.update(self._get_extra_fields(record))

        if record.exc_info:
            event_dict["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            event_dict["stack_info"] = record.stack_info

        return json.dumps(reorder_log_keys(event_dict), default=self._json_default)

    @staticmethod
    def _format_timestamp(created: float) -> str:
        return (
            datetime.fromtimestamp(created, tz=UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )

    @staticmethod
    def _json_default(value: Any) -> str:
        return str(value)

    @staticmethod
    def _get_extra_fields(record: logging.LogRecord) -> dict[str, Any]:
        return {
            key: value
            for key, value in record.__dict__.items()
            if key not in _STANDARD_LOG_RECORD_KEYS
        }
