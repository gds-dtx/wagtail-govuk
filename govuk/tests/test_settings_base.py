import importlib
import os
import sys
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, TestCase, override_settings
from wagtail.models import Site

from govuk.settings import base as base_settings


class ResolveOidcTokenAudienceTests(SimpleTestCase):
    def test_audience_comes_from_the_environment_then_the_default(self):
        cases = [
            (
                "OIDC_TOKEN_AUDIENCES wins over the legacy variable",
                {
                    "OIDC_TOKEN_AUDIENCES": "aud-primary, aud-secondary",
                    "OIDC_TOKEN_AUDIENCE": "legacy-audience",
                },
                "default-audience",
                ("aud-primary", "aud-secondary"),
            ),
            (
                "a single OIDC_TOKEN_AUDIENCES value is a string",
                {"OIDC_TOKEN_AUDIENCES": "aud-primary"},
                "default-audience",
                "aud-primary",
            ),
            (
                "duplicates and empty values are dropped",
                {"OIDC_TOKEN_AUDIENCES": "aud-primary, aud-secondary, aud-primary, , "},
                "default-audience",
                ("aud-primary", "aud-secondary"),
            ),
            (
                "falls back to the legacy OIDC_TOKEN_AUDIENCE",
                {"OIDC_TOKEN_AUDIENCE": "legacy-audience"},
                "default-audience",
                "legacy-audience",
            ),
            ("falls back to the default", {}, "default-audience", "default-audience"),
            ("none when nothing is available", {}, None, None),
        ]
        for name, environ, default, expected in cases:
            with self.subTest(name), patch.dict(os.environ, environ, clear=True):
                self.assertEqual(base_settings._resolve_oidc_token_audience(default), expected)


class CacheConfigTests(SimpleTestCase):
    """CACHE_URL lets a deployment put a real tier behind the cache without a code change."""

    def _config(self, environ, redis_installed=True):
        spec = object() if redis_installed else None
        with patch.dict(os.environ, environ, clear=True):
            with patch.object(base_settings, "find_spec", return_value=spec):
                return base_settings._cache_config()

    def test_unset_gives_the_local_memory_cache_every_instance_runs_today(self):
        config = self._config({})

        self.assertEqual(
            config,
            {
                "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                "LOCATION": "wagtail-govuk",
            },
        )

    def test_a_redis_url_gives_a_shared_tier(self):
        config = self._config(
            {"CACHE_URL": "rediss://cache.example.gov.uk:6379", "DOMAIN": "a.gov.uk"}
        )

        self.assertEqual(config["BACKEND"], "django.core.cache.backends.redis.RedisCache")
        self.assertEqual(config["LOCATION"], "rediss://cache.example.gov.uk:6379")

    def test_several_urls_are_passed_through_as_primary_then_replicas(self):
        """The shape ElastiCache presents, and the shape Django's RedisCache reads a list as."""
        config = self._config(
            {
                "CACHE_URL": "redis://primary:6379, redis://replica:6379",
                "DOMAIN": "a.gov.uk",
            }
        )

        self.assertEqual(
            config["LOCATION"], ["redis://primary:6379", "redis://replica:6379"]
        )

    def test_the_key_prefix_keeps_two_services_on_one_tier_apart(self):
        """Six services run this image and "wagtail-govuk" is the same string
        in all of them, so a shared tier needs the keyspace split by site."""
        self.assertEqual(
            self._config({"CACHE_URL": "redis://c:6379", "DOMAIN": "cyber.gov.uk"})[
                "KEY_PREFIX"
            ],
            "cyber.gov.uk",
        )
        self.assertEqual(
            self._config(
                {
                    "CACHE_URL": "redis://c:6379",
                    "DOMAIN": "cyber.gov.uk",
                    "CACHE_KEY_PREFIX": "chosen-by-hand",
                }
            )["KEY_PREFIX"],
            "chosen-by-hand",
        )

    def test_a_cache_url_that_cannot_work_stops_the_app(self):
        # Django builds a cache backend lazily, so without these errors the
        # instance would start, pass its health check and misbehave later.
        cases = [
            # redis-py is not a dependency, because nothing has a tier to talk to yet.
            (
                "redis-py missing",
                {"CACHE_URL": "redis://c:6379"},
                False,
                "redis-py is not installed",
                None,
            ),
            # Without a per-service prefix, services would answer each other's reads.
            ("no key prefix", {"CACHE_URL": "redis://c:6379"}, True, "keyspace", None),
            (
                "unsupported scheme",
                {"CACHE_URL": "memcached://cache:11211"},
                True,
                "memcached",
                None,
            ),
            # The URL may carry an auth token, so only its scheme is named.
            (
                "url not repeated",
                {"CACHE_URL": "https://:s3cr3t-auth-token@cache:6379"},
                True,
                "https",
                "s3cr3t-auth-token",
            ),
        ]
        for name, environ, redis_installed, expected, unexpected in cases:
            with self.subTest(name):
                with self.assertRaises(ImproperlyConfigured) as raised:
                    self._config(environ, redis_installed=redis_installed)

                self.assertIn(expected, str(raised.exception))
                if unexpected:
                    self.assertNotIn(unexpected, str(raised.exception))


class BoolEnvTests(SimpleTestCase):
    def test_bool_env(self):
        cases = [
            ({}, "INCOMING_REQUEST_INFO_LOGGING", None, False),
            (
                {"INCOMING_REQUEST_INFO_LOGGING": "true"},
                "INCOMING_REQUEST_INFO_LOGGING",
                None,
                True,
            ),
            ({}, "CONTENT_DISCOVERY_REQUEST_INFO_LOGGING", None, False),
            (
                {"CONTENT_DISCOVERY_REQUEST_INFO_LOGGING": "true"},
                "CONTENT_DISCOVERY_REQUEST_INFO_LOGGING",
                None,
                True,
            ),
            (
                {"CONTENT_DISCOVERY_REQUEST_INFO_LOGGING": "false"},
                "CONTENT_DISCOVERY_REQUEST_INFO_LOGGING",
                None,
                False,
            ),
            ({"NOINDEX": "0"}, "NOINDEX", True, False),
        ]
        for environ, name, default, expected in cases:
            with self.subTest(environ=environ, name=name, default=default):
                with patch.dict(os.environ, environ, clear=True):
                    self.assertIs(base_settings._bool_env(name, default=default), expected)


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

    def test_resolve_log_level(self):
        cases = [(None, "INFO"), ("VERBOSE", "DEBUG"), ("debug", "DEBUG"), ("TRACE", "INFO")]
        for raw_level, expected in cases:
            with self.subTest(raw_level=raw_level):
                self.assertEqual(base_settings._resolve_log_level(raw_level), expected)


class ProductionSettingsTests(SimpleTestCase):
    """production.py is the deployed settings module, so it must be secure by default."""

    BASE_ENV = {"BASE_URL": "https://service.example.gov.uk/"}
    # Resolving this machine's own hostname can take seconds, so the address
    # deployment_allowed_hosts adds for the health check is fixed here.
    OWN_ADDRESS = "10.0.0.5"

    def _import_production(self, env):
        module_name = "govuk.settings.production"
        original_module = sys.modules.pop(module_name, None)
        try:
            with (
                patch.dict(os.environ, env, clear=True),
                patch(
                    "govuk.settings.runtime.own_ipv4_address",
                    return_value=self.OWN_ADDRESS,
                ),
            ):
                return importlib.import_module(module_name)
        finally:
            sys.modules.pop(module_name, None)
            if original_module is not None:
                sys.modules[module_name] = original_module

    def test_exposes_base_url_from_environment(self):
        production = self._import_production(
            {"BASE_URL": "https://gds-cyber-001.dev.wagtail.ukps.digital/"}
        )

        self.assertEqual(production.BASE_URL, "https://gds-cyber-001.dev.wagtail.ukps.digital")
        self.assertEqual(
            production.WAGTAILADMIN_BASE_URL,
            "https://gds-cyber-001.dev.wagtail.ukps.digital",
        )

    def test_debug_is_off_unless_switched_on_explicitly(self):
        for env, expected in (({}, False), ({"DEBUG": "True"}, True)):
            with self.subTest(env=env):
                production = self._import_production({**self.BASE_ENV, **env})

                self.assertIs(production.DEBUG, expected)

    def test_allowed_hosts(self):
        # The load balancer health-checks the task by its own IP, and a 400
        # gets the task replaced, so that address is always added; a wildcard never is.
        cases = [
            ({"ALLOWED_HOSTS": "service.example.gov.uk"}, ["service.example.gov.uk"]),
            (
                {"ALLOWED_HOSTS": "service.example.gov.uk, health.internal"},
                ["service.example.gov.uk", "health.internal"],
            ),
            ({"DOMAIN": "service.example.gov.uk"}, ["service.example.gov.uk"]),
        ]
        for env, configured in cases:
            with self.subTest(env=env):
                production = self._import_production({**self.BASE_ENV, **env})

                self.assertEqual(production.ALLOWED_HOSTS, [*configured, self.OWN_ADDRESS])
                self.assertNotIn("*", production.ALLOWED_HOSTS)


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
