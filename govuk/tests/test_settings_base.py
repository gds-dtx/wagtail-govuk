import importlib
import os
import sys
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, TestCase, override_settings
from wagtail.models import Site

from govuk.settings import base as base_settings
from govuk.settings.runtime import own_ipv4_address


class ResolveOidcTokenAudienceTests(SimpleTestCase):
    def test_prefers_oidc_token_audiences_env_value(self):
        with patch.dict(
            os.environ,
            {
                "OIDC_TOKEN_AUDIENCES": "aud-primary, aud-secondary",
                "OIDC_TOKEN_AUDIENCE": "legacy-audience",
            },
            clear=True,
        ):
            audience = base_settings._resolve_oidc_token_audience("default-audience")

        self.assertEqual(audience, ("aud-primary", "aud-secondary"))

    def test_single_oidc_token_audiences_value_returns_string(self):
        with patch.dict(
            os.environ,
            {
                "OIDC_TOKEN_AUDIENCES": "aud-primary",
            },
            clear=True,
        ):
            audience = base_settings._resolve_oidc_token_audience("default-audience")

        self.assertEqual(audience, "aud-primary")

    def test_oidc_token_audiences_deduplicates_and_ignores_empty_values(self):
        with patch.dict(
            os.environ,
            {
                "OIDC_TOKEN_AUDIENCES": "aud-primary, aud-secondary, aud-primary, , ",
            },
            clear=True,
        ):
            audience = base_settings._resolve_oidc_token_audience("default-audience")

        self.assertEqual(audience, ("aud-primary", "aud-secondary"))

    def test_falls_back_to_legacy_oidc_token_audience_env_value(self):
        with patch.dict(
            os.environ,
            {
                "OIDC_TOKEN_AUDIENCE": "legacy-audience",
            },
            clear=True,
        ):
            audience = base_settings._resolve_oidc_token_audience("default-audience")

        self.assertEqual(audience, "legacy-audience")

    def test_falls_back_to_default_audience_when_env_not_set(self):
        with patch.dict(os.environ, {}, clear=True):
            audience = base_settings._resolve_oidc_token_audience("default-audience")

        self.assertEqual(audience, "default-audience")

    def test_returns_none_when_no_audience_is_available(self):
        with patch.dict(os.environ, {}, clear=True):
            audience = base_settings._resolve_oidc_token_audience(None)

        self.assertIsNone(audience)


class CacheConfigTests(SimpleTestCase):
    """CACHE_URL, added at Ollie's suggestion on PR #106 so a deployment can
    put a real tier behind the cache without a code change."""

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
        """The shape ElastiCache presents, and the shape Django's RedisCache
        reads a list as."""
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

    def test_a_cache_url_without_redis_py_installed_stops_the_app(self):
        """redis-py is not a dependency, because nothing has a tier to talk to
        yet. Django builds a cache backend lazily, so without this the
        instance would start, pass its health check and look well until
        something touched the cache."""
        with self.assertRaises(ImproperlyConfigured) as raised:
            self._config({"CACHE_URL": "redis://c:6379"}, redis_installed=False)

        self.assertIn("redis-py is not installed", str(raised.exception))

    def test_a_cache_url_without_a_key_prefix_stops_the_app(self):
        """A shared tier with no per-service prefix would have services answer
        each other's reads, so it refuses to start rather than misbehave."""
        with self.assertRaises(ImproperlyConfigured) as raised:
            self._config({"CACHE_URL": "redis://c:6379"})

        self.assertIn("keyspace", str(raised.exception))

    def test_an_unsupported_scheme_stops_the_app_rather_than_silently_not_caching(self):
        with self.assertRaises(ImproperlyConfigured) as raised:
            self._config({"CACHE_URL": "memcached://cache:11211"})

        self.assertIn("memcached", str(raised.exception))

    def test_the_url_is_not_repeated_in_the_error_because_it_may_carry_a_token(self):
        with self.assertRaises(ImproperlyConfigured) as raised:
            self._config({"CACHE_URL": "https://:s3cr3t-auth-token@cache:6379"})

        self.assertNotIn("s3cr3t-auth-token", str(raised.exception))
        self.assertIn("https", str(raised.exception))


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


class ProductionSettingsTests(SimpleTestCase):
    def test_exposes_base_url_from_environment(self):
        module_name = "govuk.settings.production"
        original_module = sys.modules.pop(module_name, None)

        try:
            with patch.dict(
                os.environ,
                {"BASE_URL": "https://gds-cyber-001.dev.wagtail.ukps.digital/"},
                clear=False,
            ):
                production_settings = importlib.import_module(module_name)

            self.assertEqual(
                production_settings.BASE_URL,
                "https://gds-cyber-001.dev.wagtail.ukps.digital",
            )
            self.assertEqual(
                production_settings.WAGTAILADMIN_BASE_URL,
                "https://gds-cyber-001.dev.wagtail.ukps.digital",
            )
        finally:
            sys.modules.pop(module_name, None)
            if original_module is not None:
                sys.modules[module_name] = original_module


class ProductionSecuritySettingsTests(SimpleTestCase):
    """production.py is the deployed settings module, so it must be secure by default."""

    def _import_production(self, env):
        module_name = "govuk.settings.production"
        original_module = sys.modules.pop(module_name, None)
        try:
            with patch.dict(os.environ, env, clear=True):
                return importlib.import_module(module_name)
        finally:
            sys.modules.pop(module_name, None)
            if original_module is not None:
                sys.modules[module_name] = original_module

    _BASE_ENV = {"BASE_URL": "https://service.example.gov.uk/"}

    def _with_own_address(self, hosts):
        """The configured hosts, plus the address the health check arrives on.

        The load balancer connects to the task by IP and sends that IP as the
        Host header, so ``production.py`` adds it -- see
        ``deployment_allowed_hosts``.
        """
        own_address = own_ipv4_address()
        return [*hosts, own_address] if own_address else list(hosts)

    def test_debug_defaults_to_false_when_unset(self):
        production = self._import_production(self._BASE_ENV)

        self.assertFalse(production.DEBUG)

    def test_debug_can_be_switched_on_explicitly(self):
        production = self._import_production({**self._BASE_ENV, "DEBUG": "True"})

        self.assertTrue(production.DEBUG)

    def test_allowed_hosts_never_contains_a_wildcard(self):
        production = self._import_production(
            {**self._BASE_ENV, "ALLOWED_HOSTS": "service.example.gov.uk"}
        )

        self.assertNotIn("*", production.ALLOWED_HOSTS)

    def test_allowed_hosts_reads_a_comma_separated_list(self):
        production = self._import_production(
            {
                **self._BASE_ENV,
                "ALLOWED_HOSTS": "service.example.gov.uk, health.internal",
            }
        )

        self.assertEqual(
            production.ALLOWED_HOSTS,
            self._with_own_address(["service.example.gov.uk", "health.internal"]),
        )

    def test_allowed_hosts_falls_back_to_domain_when_unset(self):
        production = self._import_production(
            {**self._BASE_ENV, "DOMAIN": "service.example.gov.uk"}
        )

        self.assertEqual(
            production.ALLOWED_HOSTS,
            self._with_own_address(["service.example.gov.uk"]),
        )

    def test_the_load_balancer_health_check_host_is_allowed(self):
        """It arrives as the task's own IP, and a 400 gets the task replaced."""
        production = self._import_production(
            {**self._BASE_ENV, "DOMAIN": "service.example.gov.uk"}
        )

        own_address = own_ipv4_address()
        if own_address is None:
            self.skipTest("No resolvable address on this machine")
        self.assertIn(own_address, production.ALLOWED_HOSTS)
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
