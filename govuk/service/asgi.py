"""ASGI entry point; start via ``manage.py run_service`` so the site's logging applies."""

import os

import django
from django.core.exceptions import ImproperlyConfigured

if not os.environ.get("DJANGO_SETTINGS_MODULE"):
    raise ImproperlyConfigured(
        "Start the service with `manage.py run_service`, or set DJANGO_SETTINGS_MODULE."
    )
django.setup()

from govuk.service.app import create_app  # noqa: E402

app = create_app()
