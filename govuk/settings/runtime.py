import os
import socket
import sys

from django.core.exceptions import ImproperlyConfigured

LOCAL_SETTINGS_MODULE = "govuk.settings.local"
RUNSERVER_COMMANDS = {"runserver", "runserver_plus"}


def own_ipv4_address() -> str | None:
    """The address this container answers on, or None if it cannot be found.

    In an ``awsvpc`` task the container's hostname maps to the task ENI's
    private address, which is the one the load balancer connects to.
    """
    try:
        return socket.gethostbyname(socket.gethostname())
    except OSError:
        # No resolvable hostname. Outside a container there is no load
        # balancer to satisfy either, so there is nothing to add.
        return None


def deployment_allowed_hosts(
    environ: dict[str, str] | None = None,
    resolve_own_address=None,
) -> list[str]:
    """Every host the deployed app answers to, health checks included.

    ``ALLOWED_HOSTS`` carries no wildcard, so Django answers 400 to any Host
    header not in this list. The load balancer health-checks the task by IP
    and sends that IP as the Host header -- a target group offers no way to
    send the site's domain instead. 400 is outside the check's 2xx matcher,
    so without the task's own address here a container that is serving
    perfectly well fails its check and is replaced, over and over, and the
    instance never comes up.

    Allowing that address costs nothing. It is private to the VPC, and the
    absolute URLs Wagtail writes come from ``BASE_URL`` rather than from the
    Host header, so it cannot turn up in a link.
    """
    environ = os.environ if environ is None else environ
    resolve_own_address = (
        own_ipv4_address if resolve_own_address is None else resolve_own_address
    )

    # ALLOWED_HOSTS names the hosts outright; DOMAIN is the fallback for an
    # environment that only ever answers on the one name.
    configured = environ.get("ALLOWED_HOSTS", environ.get("DOMAIN") or "")
    hosts = [host.strip() for host in configured.split(",") if host.strip()]

    own_address = resolve_own_address()
    if own_address and own_address not in hosts:
        hosts.append(own_address)
    return hosts


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
