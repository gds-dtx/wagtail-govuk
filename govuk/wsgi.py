"""
WSGI config for govuk project.

It exposes the WSGI callable as a module-level variable named ``application``.
"""

import os

from django.core.wsgi import get_wsgi_application
from django.db import OperationalError, ProgrammingError

from govuk.settings.runtime import resolve_wsgi_settings_module

resolve_wsgi_settings_module(os.environ)

application = get_wsgi_application()

try:
    from govuk.settings.base import (
        sync_admin_users_from_env,
        sync_default_site_from_env,
    )

    sync_admin_users_from_env()
    sync_default_site_from_env()
except (OperationalError, ProgrammingError):
    # Database tables may not exist during early startup or migration phases.
    pass
