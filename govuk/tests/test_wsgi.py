import importlib
import sys
from unittest.mock import patch, sentinel

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from django.http.request import validate_host

from govuk.settings.runtime import (
    LOCAL_SETTINGS_MODULE,
    deployment_allowed_hosts,
    is_gunicorn_process,
    is_runserver_process,
    resolve_wsgi_settings_module,
)


class DeploymentAllowedHostsTests(SimpleTestCase):
    """The Host headers a deployed instance has to answer to.

    There is no wildcard in the deployed allow-list, so anything missing from
    it is a 400 rather than a page.
    """

    DOMAIN = "gds-capframework-001.dev.wagtail.ukps.digital"
    TASK_ADDRESS = "10.0.3.47"

    def hosts(self, environ, own_address=TASK_ADDRESS):
        return deployment_allowed_hosts(
            environ=environ, resolve_own_address=lambda: own_address
        )

    def test_the_load_balancer_health_check_is_answered(self):
        """The check connects to the task by IP and sends that IP as the Host.

        A target group has no setting that would make it send the site's
        domain instead, and its matcher only accepts 2xx, so a 400 here marks
        a healthy container unhealthy and the orchestrator replaces it.
        """
        hosts = self.hosts({"DOMAIN": self.DOMAIN})

        self.assertTrue(validate_host(self.TASK_ADDRESS, hosts))

    def test_the_site_domain_is_answered(self):
        hosts = self.hosts({"DOMAIN": self.DOMAIN})

        self.assertTrue(validate_host(self.DOMAIN, hosts))

    def test_nothing_else_is_answered(self):
        """The point of the allow-list survives adding to it."""
        hosts = self.hosts({"DOMAIN": self.DOMAIN})

        for host in ("example.com", "evil.test", "10.0.3.48"):
            with self.subTest(host=host):
                self.assertFalse(validate_host(host, hosts))

    def test_allowed_hosts_names_the_hosts_and_domain_is_the_fallback(self):
        hosts = self.hosts(
            {"ALLOWED_HOSTS": "one.example, two.example", "DOMAIN": self.DOMAIN}
        )

        self.assertEqual(hosts, ["one.example", "two.example", self.TASK_ADDRESS])

    def test_blank_entries_are_dropped(self):
        hosts = self.hosts({"ALLOWED_HOSTS": " one.example , , two.example ,"})

        self.assertEqual(hosts, ["one.example", "two.example", self.TASK_ADDRESS])

    def test_an_address_already_listed_is_not_repeated(self):
        hosts = self.hosts({"ALLOWED_HOSTS": f"{self.DOMAIN},{self.TASK_ADDRESS}"})

        self.assertEqual(hosts, [self.DOMAIN, self.TASK_ADDRESS])

    def test_an_unresolvable_hostname_leaves_the_rest_intact(self):
        """Nothing to add, rather than a settings module that will not import."""
        hosts = self.hosts({"DOMAIN": self.DOMAIN}, own_address=None)

        self.assertEqual(hosts, [self.DOMAIN])

    def test_no_configuration_at_all_still_answers_the_health_check(self):
        self.assertEqual(self.hosts({}), [self.TASK_ADDRESS])


class RunserverDetectionTests(SimpleTestCase):
    def test_detects_runserver_command(self):
        self.assertTrue(is_runserver_process(argv=["manage.py", "runserver"]))

    def test_detects_runserver_plus_command(self):
        self.assertTrue(is_runserver_process(argv=["manage.py", "runserver_plus"]))

    def test_ignores_other_management_commands(self):
        self.assertFalse(is_runserver_process(argv=["manage.py", "migrate"]))


class GunicornDetectionTests(SimpleTestCase):
    def test_detects_gunicorn_executable(self):
        self.assertTrue(
            is_gunicorn_process(
                argv=["/venv/bin/gunicorn", "govuk.wsgi:application"],
            )
        )

    def test_detects_python_module_invocation(self):
        self.assertTrue(
            is_gunicorn_process(
                argv=["python", "-m", "gunicorn", "govuk.wsgi:application"],
            )
        )

    def test_detects_gunicorn_modules(self):
        self.assertTrue(
            is_gunicorn_process(
                argv=["python", "-c", "import govuk.wsgi"],
                loaded_modules={"gunicorn.app.wsgiapp": object()},
            )
        )


class ResolveWsgiSettingsModuleTests(SimpleTestCase):
    def test_defaults_to_local_settings_for_runserver(self):
        environ = {}

        resolved = resolve_wsgi_settings_module(
            environ=environ,
            argv=["manage.py", "runserver"],
        )

        self.assertEqual(resolved, LOCAL_SETTINGS_MODULE)
        self.assertEqual(environ["DJANGO_SETTINGS_MODULE"], LOCAL_SETTINGS_MODULE)

    def test_preserves_explicit_non_local_settings(self):
        environ = {"DJANGO_SETTINGS_MODULE": "govuk.settings.dev"}

        resolved = resolve_wsgi_settings_module(
            environ=environ,
            argv=["/venv/bin/gunicorn", "govuk.wsgi:application"],
        )

        self.assertEqual(resolved, "govuk.settings.dev")

    def test_rejects_local_settings_for_gunicorn(self):
        with self.assertRaisesMessage(
            ImproperlyConfigured,
            "govuk.settings.local is only supported for local "
            "`python manage.py runserver`.",
        ):
            resolve_wsgi_settings_module(
                environ={"DJANGO_SETTINGS_MODULE": LOCAL_SETTINGS_MODULE},
                argv=["/venv/bin/gunicorn", "govuk.wsgi:application"],
            )

    def test_rejects_missing_settings_for_gunicorn(self):
        with self.assertRaisesMessage(
            ImproperlyConfigured,
            "DJANGO_SETTINGS_MODULE must be set to a non-local settings module "
            "before starting Gunicorn.",
        ):
            resolve_wsgi_settings_module(
                environ={},
                argv=["/venv/bin/gunicorn", "govuk.wsgi:application"],
            )

    def test_rejects_local_settings_outside_runserver(self):
        with self.assertRaisesMessage(
            ImproperlyConfigured,
            "govuk.settings.local is only supported for local "
            "`python manage.py runserver`.",
        ):
            resolve_wsgi_settings_module(
                environ={"DJANGO_SETTINGS_MODULE": LOCAL_SETTINGS_MODULE},
                argv=["python", "-c", "import govuk.wsgi"],
            )


class WsgiModuleTests(SimpleTestCase):
    def test_import_does_not_sync_env_state(self):
        sys.modules.pop("govuk.wsgi", None)

        try:
            with (
                patch("govuk.settings.runtime.resolve_wsgi_settings_module"),
                patch(
                    "django.core.wsgi.get_wsgi_application",
                    return_value=sentinel.application,
                ),
                patch("govuk.settings.base.sync_admin_users_from_env") as mock_admin_sync,
                patch(
                    "govuk.settings.base.sync_default_site_from_env"
                ) as mock_site_sync,
            ):
                module = importlib.import_module("govuk.wsgi")
        finally:
            sys.modules.pop("govuk.wsgi", None)

        self.assertIs(module.application, sentinel.application)
        mock_admin_sync.assert_not_called()
        mock_site_sync.assert_not_called()
