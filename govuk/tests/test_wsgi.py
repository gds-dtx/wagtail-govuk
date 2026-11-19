import importlib
import sys
from unittest.mock import patch, sentinel

from django.core.exceptions import ImproperlyConfigured
from django.http.request import validate_host
from django.test import SimpleTestCase

from govuk.settings.runtime import (
    DEVELOPMENT_SETTINGS_MODULE,
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

    def test_only_the_site_domain_and_the_task_address_are_answered(self):
        # The load balancer health check connects to the task by IP and sends
        # that IP as the Host. A target group cannot send the site's domain
        # instead and its matcher only accepts 2xx, so a 400 here marks a
        # healthy container unhealthy and the orchestrator replaces it.
        hosts = self.hosts({"DOMAIN": self.DOMAIN})

        for host in (self.TASK_ADDRESS, self.DOMAIN):
            with self.subTest(host=host):
                self.assertTrue(validate_host(host, hosts))
        for host in ("example.com", "evil.test", "10.0.3.48"):
            with self.subTest(host=host):
                self.assertFalse(validate_host(host, hosts))

    def test_the_hosts_listed(self):
        cases = [
            (
                "ALLOWED_HOSTS names the hosts and DOMAIN is the fallback",
                {"ALLOWED_HOSTS": "one.example, two.example", "DOMAIN": self.DOMAIN},
                self.TASK_ADDRESS,
                ["one.example", "two.example", self.TASK_ADDRESS],
            ),
            (
                "blank entries are dropped",
                {"ALLOWED_HOSTS": " one.example , , two.example ,"},
                self.TASK_ADDRESS,
                ["one.example", "two.example", self.TASK_ADDRESS],
            ),
            (
                "an address already listed is not repeated",
                {"ALLOWED_HOSTS": f"{self.DOMAIN},{self.TASK_ADDRESS}"},
                self.TASK_ADDRESS,
                [self.DOMAIN, self.TASK_ADDRESS],
            ),
            # Nothing to add, rather than a settings module that will not import.
            ("an unresolvable hostname", {"DOMAIN": self.DOMAIN}, None, [self.DOMAIN]),
            (
                "no configuration still answers the health check",
                {},
                self.TASK_ADDRESS,
                [self.TASK_ADDRESS],
            ),
        ]
        for name, environ, own_address, expected in cases:
            with self.subTest(name):
                self.assertEqual(self.hosts(environ, own_address=own_address), expected)


class ProcessDetectionTests(SimpleTestCase):
    def test_runserver_detection(self):
        for argv, expected in (
            (["manage.py", "runserver"], True),
            (["manage.py", "runserver_plus"], True),
            (["manage.py", "migrate"], False),
        ):
            with self.subTest(argv=argv):
                self.assertIs(is_runserver_process(argv=argv), expected)

    def test_gunicorn_detection(self):
        for argv, loaded_modules in (
            (["/venv/bin/gunicorn", "govuk.wsgi:application"], None),
            (["python", "-m", "gunicorn", "govuk.wsgi:application"], None),
            (["python", "-c", "import govuk.wsgi"], {"gunicorn.app.wsgiapp": object()}),
        ):
            with self.subTest(argv=argv, loaded_modules=loaded_modules):
                self.assertTrue(is_gunicorn_process(argv=argv, loaded_modules=loaded_modules))


class ResolveWsgiSettingsModuleTests(SimpleTestCase):
    def test_defaults_to_development_settings_for_runserver(self):
        environ = {}

        resolved = resolve_wsgi_settings_module(
            environ=environ,
            argv=["manage.py", "runserver"],
        )

        self.assertEqual(resolved, DEVELOPMENT_SETTINGS_MODULE)
        self.assertEqual(environ["DJANGO_SETTINGS_MODULE"], DEVELOPMENT_SETTINGS_MODULE)

    def test_preserves_explicit_deployed_settings(self):
        environ = {"DJANGO_SETTINGS_MODULE": "govuk.settings.production"}

        resolved = resolve_wsgi_settings_module(
            environ=environ,
            argv=["/venv/bin/gunicorn", "govuk.wsgi:application"],
        )

        self.assertEqual(resolved, "govuk.settings.production")

    def test_rejects_development_or_missing_settings_outside_runserver(self):
        development_only = (
            "govuk.settings.development is only supported for local "
            "`python manage.py runserver`."
        )
        gunicorn = ["/venv/bin/gunicorn", "govuk.wsgi:application"]
        cases = [
            (
                "development under gunicorn",
                {"DJANGO_SETTINGS_MODULE": DEVELOPMENT_SETTINGS_MODULE},
                gunicorn,
                development_only,
            ),
            (
                "missing under gunicorn",
                {},
                gunicorn,
                "DJANGO_SETTINGS_MODULE must be set to a deployed settings module "
                "before starting Gunicorn.",
            ),
            (
                "development outside runserver",
                {"DJANGO_SETTINGS_MODULE": DEVELOPMENT_SETTINGS_MODULE},
                ["python", "-c", "import govuk.wsgi"],
                development_only,
            ),
        ]
        for name, environ, argv, message in cases:
            with self.subTest(name), self.assertRaisesMessage(ImproperlyConfigured, message):
                resolve_wsgi_settings_module(environ=environ, argv=argv)


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
