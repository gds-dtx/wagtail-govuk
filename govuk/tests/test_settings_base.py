import os
from unittest.mock import patch

from django.test import SimpleTestCase

from govuk.settings import base as base_settings


class ResolveSimpleJwtAudienceTests(SimpleTestCase):
    def test_prefers_oidc_token_audiences_env_value(self):
        with patch.dict(
            os.environ,
            {
                "OIDC_TOKEN_AUDIENCES": "aud-primary, aud-secondary",
                "OIDC_TOKEN_AUDIENCE": "legacy-audience",
            },
            clear=True,
        ):
            audience = base_settings._resolve_simple_jwt_audience("default-audience")

        self.assertEqual(audience, ("aud-primary", "aud-secondary"))

    def test_single_oidc_token_audiences_value_returns_string(self):
        with patch.dict(
            os.environ,
            {
                "OIDC_TOKEN_AUDIENCES": "aud-primary",
            },
            clear=True,
        ):
            audience = base_settings._resolve_simple_jwt_audience("default-audience")

        self.assertEqual(audience, "aud-primary")

    def test_oidc_token_audiences_deduplicates_and_ignores_empty_values(self):
        with patch.dict(
            os.environ,
            {
                "OIDC_TOKEN_AUDIENCES": "aud-primary, aud-secondary, aud-primary, , ",
            },
            clear=True,
        ):
            audience = base_settings._resolve_simple_jwt_audience("default-audience")

        self.assertEqual(audience, ("aud-primary", "aud-secondary"))

    def test_falls_back_to_legacy_oidc_token_audience_env_value(self):
        with patch.dict(
            os.environ,
            {
                "OIDC_TOKEN_AUDIENCE": "legacy-audience",
            },
            clear=True,
        ):
            audience = base_settings._resolve_simple_jwt_audience("default-audience")

        self.assertEqual(audience, "legacy-audience")

    def test_falls_back_to_default_audience_when_env_not_set(self):
        with patch.dict(os.environ, {}, clear=True):
            audience = base_settings._resolve_simple_jwt_audience("default-audience")

        self.assertEqual(audience, "default-audience")

    def test_returns_none_when_no_audience_is_available(self):
        with patch.dict(os.environ, {}, clear=True):
            audience = base_settings._resolve_simple_jwt_audience(None)

        self.assertIsNone(audience)


class BoolEnvTests(SimpleTestCase):
    def test_incoming_request_info_logging_defaults_to_false_when_unset(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(base_settings._bool_env("INCOMING_REQUEST_INFO_LOGGING"))

    def test_incoming_request_info_logging_true_like_value_returns_true(self):
        with patch.dict(
            os.environ,
            {"INCOMING_REQUEST_INFO_LOGGING": "true"},
            clear=True,
        ):
            self.assertTrue(base_settings._bool_env("INCOMING_REQUEST_INFO_LOGGING"))

    def test_bool_env_defaults_to_false_when_unset(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(
                base_settings._bool_env("CONTENT_DISCOVERY_REQUEST_INFO_LOGGING")
            )

    def test_bool_env_treats_true_like_values_as_true(self):
        with patch.dict(
            os.environ,
            {"CONTENT_DISCOVERY_REQUEST_INFO_LOGGING": "true"},
            clear=True,
        ):
            self.assertTrue(
                base_settings._bool_env("CONTENT_DISCOVERY_REQUEST_INFO_LOGGING")
            )


class LoggingSettingsTests(SimpleTestCase):
    def test_uses_logging_json_formatter_for_console_logs(self):
        formatter_config = base_settings.LOGGING["formatters"]["logging_json"]
        console_handler = base_settings.LOGGING["handlers"]["console"]
        root_logger = base_settings.LOGGING["root"]
        loggers = base_settings.LOGGING["loggers"]

        self.assertEqual(
            formatter_config["()"], "govuk.logging_utils.LoggingJSONFormatter"
        )
        self.assertEqual(console_handler["formatter"], "logging_json")
        self.assertEqual(root_logger["handlers"], ["console"])
        self.assertIn("gunicorn.access", loggers)
        self.assertIn("gunicorn.error", loggers)
