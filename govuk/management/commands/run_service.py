"""Run the site's ASGI service (``govuk.service``).

    manage.py run_service [--host 0.0.0.0] [--port 8001]
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from govuk.features import ai_enabled


class Command(BaseCommand):
    help = "Run the site's ASGI service: the MCP server, and AI search's calls to Claude."

    def add_arguments(self, parser):
        parser.add_argument("--host", default="127.0.0.1", help="Address to listen on.")
        parser.add_argument("--port", type=int, default=8001, help="Port to listen on.")
        parser.add_argument("--workers", type=int, default=1, help="Worker processes.")
        parser.add_argument(
            "--reload", action="store_true", help="Restart when the code changes (local only)."
        )

    def handle(self, *args, **options):
        if not ai_enabled():
            raise CommandError("The service has nothing to serve: the AI features are off (FEATURE_AI).")
        import uvicorn

        uvicorn.run(
            "govuk.service.asgi:app",
            host=options["host"],
            port=options["port"],
            workers=options["workers"],
            reload=options["reload"],
            proxy_headers=True,
            forwarded_allow_ips=settings.SERVICE_FORWARDED_ALLOW_IPS,
            # Keep Django's logging config, not uvicorn's default.
            log_config=None,
            server_header=False,
        )
