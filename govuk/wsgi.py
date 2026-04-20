"""
WSGI config for govuk project.

It exposes the WSGI callable as a module-level variable named ``application``.
"""

import os

from django.core.wsgi import get_wsgi_application

from govuk.settings.runtime import resolve_wsgi_settings_module

resolve_wsgi_settings_module(os.environ)

application = get_wsgi_application()
