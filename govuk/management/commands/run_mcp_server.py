"""Run the MCP server under uvicorn.

Locally, the defaults serve it at http://127.0.0.1:8001/mcp. Deployed, the
container runs ``manage.py run_mcp_server --host 0.0.0.0`` beside the site's
gunicorn, and the load balancer sends ``/mcp`` to it.
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from govuk.content_index import content_index_enabled


class Command(BaseCommand):
    help = "Run the MCP server, which searches and reads the content index."

    def add_arguments(self, parser):
        parser.add_argument("--host", default="127.0.0.1", help="Address to listen on.")
        parser.add_argument("--port", type=int, default=8001, help="Port to listen on.")
        parser.add_argument("--workers", type=int, default=1, help="Worker processes.")
        parser.add_argument(
            "--reload", action="store_true", help="Restart when the code changes (local only)."
        )

    def handle(self, *args, **options):
        if not content_index_enabled():
            raise CommandError("The MCP server is off (FEATURE_MCP).")
        import uvicorn

        uvicorn.run(
            "govuk.mcp.asgi:app",
            host=options["host"],
            port=options["port"],
            workers=options["workers"],
            reload=options["reload"],
            proxy_headers=True,
            forwarded_allow_ips=settings.MCP_FORWARDED_ALLOW_IPS,
            # The site's logging, which Django has already configured, not
            # uvicorn's plain-text default.
            log_config=None,
            server_header=False,
        )
