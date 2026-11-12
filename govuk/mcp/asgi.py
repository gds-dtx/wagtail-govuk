"""The MCP server's ASGI app, which ``manage.py run_mcp_server`` starts uvicorn on.

Started through the management command, so the settings module and logging
are the site's: uvicorn on its own would apply its own plain-text logging.
"""

import os

import django
from django.core.exceptions import ImproperlyConfigured

if not os.environ.get("DJANGO_SETTINGS_MODULE"):
    raise ImproperlyConfigured(
        "Start the MCP server with `manage.py run_mcp_server`, or set DJANGO_SETTINGS_MODULE."
    )
django.setup()

from govuk.mcp.app import create_app  # noqa: E402

app = create_app()
