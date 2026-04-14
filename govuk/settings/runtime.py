import os
import sys

from django.core.exceptions import ImproperlyConfigured

LOCAL_SETTINGS_MODULE = "govuk.settings.local"
RUNSERVER_COMMANDS = {"runserver", "runserver_plus"}


def is_runserver_process(
    argv: list[str] | tuple[str, ...] | None = None,
    loaded_modules: dict[str, object] | None = None,
) -> bool:
    argv = tuple(sys.argv if argv is None else argv)
    loaded_modules = sys.modules if loaded_modules is None else loaded_modules

    command = next((arg for arg in argv[1:] if not arg.startswith("-")), "")
    if command in RUNSERVER_COMMANDS:
        return True

    return any(
        module_name in loaded_modules
        for module_name in (
            "django.core.management.commands.runserver",
            "django_extensions.management.commands.runserver_plus",
        )
    )


def is_gunicorn_process(
    argv: list[str] | tuple[str, ...] | None = None,
    environ: dict[str, str] | None = None,
    loaded_modules: dict[str, object] | None = None,
) -> bool:
    argv = tuple(sys.argv if argv is None else argv)
    environ = os.environ if environ is None else environ
    loaded_modules = sys.modules if loaded_modules is None else loaded_modules

    executable = os.path.basename(argv[0]).lower() if argv else ""
    argv_text = " ".join(part.lower() for part in argv)

    if environ.get("GUNICORN_CMD_ARGS") is not None:
        return True
    if "gunicorn" in executable or "gunicorn" in argv_text:
        return True

    return any(
        module_name == "gunicorn" or module_name.startswith("gunicorn.")
        for module_name in loaded_modules
    )


def resolve_wsgi_settings_module(
    environ: dict[str, str] | None = None,
    argv: list[str] | tuple[str, ...] | None = None,
    loaded_modules: dict[str, object] | None = None,
) -> str:
    environ = os.environ if environ is None else environ
    configured_settings = environ.get("DJANGO_SETTINGS_MODULE")
    runserver = is_runserver_process(argv=argv, loaded_modules=loaded_modules)
    gunicorn = is_gunicorn_process(
        argv=argv,
        environ=environ,
        loaded_modules=loaded_modules,
    )

    if configured_settings == LOCAL_SETTINGS_MODULE and (gunicorn or not runserver):
        raise ImproperlyConfigured(
            "govuk.settings.local is only supported for local "
            "`python manage.py runserver`. Set DJANGO_SETTINGS_MODULE to a "
            "non-local settings module before starting the WSGI application."
        )

    if configured_settings:
        return configured_settings

    if runserver and not gunicorn:
        environ.setdefault("DJANGO_SETTINGS_MODULE", LOCAL_SETTINGS_MODULE)
        return LOCAL_SETTINGS_MODULE

    if gunicorn:
        raise ImproperlyConfigured(
            "DJANGO_SETTINGS_MODULE must be set to a non-local settings module "
            "before starting Gunicorn."
        )

    raise ImproperlyConfigured(
        "DJANGO_SETTINGS_MODULE must be set before starting the WSGI "
        "application. Refusing to default to govuk.settings.local outside "
        "`python manage.py runserver`."
    )
