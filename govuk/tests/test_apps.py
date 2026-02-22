from importlib import import_module
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from govuk.apps import GovukConfig


class GovukConfigTests(SimpleTestCase):
    @override_settings(
        DEBUG=True,
        INCOMING_REQUEST_INFO_LOGGING=False,
        CONTENT_DISCOVERY_REQUEST_INFO_LOGGING=True,
    )
    def test_ready_logs_startup_logging_flags(self):
        app_config = GovukConfig("govuk", import_module("govuk"))

        with (
            patch("govuk.apps.logger.info") as mock_info,
            patch("govuk.apps.post_migrate.connect"),
        ):
            app_config.ready()

        mock_info.assert_called_once_with(
            "Application startup logging flags",
            extra={
                "DEBUG": True,
                "INCOMING_REQUEST_INFO_LOGGING": False,
                "CONTENT_DISCOVERY_REQUEST_INFO_LOGGING": True,
            },
        )
