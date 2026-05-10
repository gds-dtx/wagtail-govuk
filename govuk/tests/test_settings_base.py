import importlib
import os
import sys
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings
from wagtail.models import Site

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

    def test_bool_env_treats_false_like_values_as_false(self):
        with patch.dict(
            os.environ,
            {"CONTENT_DISCOVERY_REQUEST_INFO_LOGGING": "false"},
            clear=True,
        ):
            self.assertFalse(
                base_settings._bool_env("CONTENT_DISCOVERY_REQUEST_INFO_LOGGING")
            )

    def test_bool_env_can_disable_default_true_values(self):
        with patch.dict(
            os.environ,
            {"NOINDEX": "0"},
            clear=True,
        ):
            self.assertFalse(base_settings._bool_env("NOINDEX", default=True))


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


class ResolveLogLevelTests(SimpleTestCase):
    def test_returns_default_when_value_is_missing(self):
        self.assertEqual(base_settings._resolve_log_level(None), "INFO")

    def test_maps_verbose_to_debug(self):
        self.assertEqual(base_settings._resolve_log_level("VERBOSE"), "DEBUG")

    def test_returns_normalised_supported_level(self):
        self.assertEqual(base_settings._resolve_log_level("debug"), "DEBUG")

    def test_returns_default_when_value_is_unsupported(self):
        self.assertEqual(base_settings._resolve_log_level("TRACE"), "INFO")


class DevSettingsTests(SimpleTestCase):
    def test_exposes_base_url_from_environment(self):
        module_name = "govuk.settings.dev"
        original_module = sys.modules.pop(module_name, None)

        try:
            with patch.dict(
                os.environ,
                {"BASE_URL": "https://gds-cyber-001.dev.wagtail.ukps.digital/"},
                clear=False,
            ):
                dev_settings = importlib.import_module(module_name)

            self.assertEqual(
                dev_settings.BASE_URL,
                "https://gds-cyber-001.dev.wagtail.ukps.digital",
            )
            self.assertEqual(
                dev_settings.WAGTAILADMIN_BASE_URL,
                "https://gds-cyber-001.dev.wagtail.ukps.digital",
            )
        finally:
            sys.modules.pop(module_name, None)
            if original_module is not None:
                sys.modules[module_name] = original_module


class SyncDefaultSiteFromEnvTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(pk=1)
        self.site.hostname = "localhost"
        self.site.port = 80
        self.site.save(update_fields=["hostname", "port"])

    @override_settings(DEFAULT_SITE_PORT=443)
    def test_sync_updates_hostname_and_port_when_domain_is_set(self):
        with patch.dict(os.environ, {"DOMAIN": "service.example.gov.uk"}, clear=False):
            result = base_settings.sync_default_site_from_env()

        self.site.refresh_from_db()
        self.assertEqual(result, {"updated": 1})
        self.assertEqual(self.site.hostname, "service.example.gov.uk")
        self.assertEqual(self.site.port, 443)

    @override_settings(DEFAULT_SITE_PORT=443)
    def test_sync_updates_port_when_domain_is_empty(self):
        with patch.dict(os.environ, {"DOMAIN": ""}, clear=False):
            result = base_settings.sync_default_site_from_env()

        self.site.refresh_from_db()
        self.assertEqual(result, {"updated": 1})
        self.assertEqual(self.site.hostname, "localhost")
        self.assertEqual(self.site.port, 443)

    @override_settings(DEFAULT_SITE_PORT=None)
    def test_sync_skips_when_domain_and_port_are_not_set(self):
        with patch.dict(os.environ, {"DOMAIN": ""}, clear=False):
            result = base_settings.sync_default_site_from_env()

        self.site.refresh_from_db()
        self.assertEqual(result, {"updated": 0})
        self.assertEqual(self.site.hostname, "localhost")
        self.assertEqual(self.site.port, 80)
