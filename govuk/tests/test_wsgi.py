from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from govuk.settings.runtime import (
    LOCAL_SETTINGS_MODULE,
    is_gunicorn_process,
    is_runserver_process,
    resolve_wsgi_settings_module,
)


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
