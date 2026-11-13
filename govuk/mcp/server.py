"""The MCP server: the tools in ``tools``, registered with the MCP SDK."""

import os

from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import DatabaseError
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from starlette.requests import Request
from starlette.responses import JSONResponse
from wagtail.models import Site

from govuk.api import _check_database_health

from . import HEALTH_PATH, tools

# Every tool here only reads, reads the same thing twice running, and reads
# only this site.
READ_ONLY = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)

INSTRUCTIONS = """\
This server searches and reads what {site_name} publishes: its pages, its \
content catalogue of standards, policies, guidance, methods and tools, and the \
Capability Framework's roles and skills, where the site has them. Everything \
here is public.

Use search to find pages, then get_document to read one in full before \
relying on it. Cite pages by the url they come back with.

A result with origin "external" is a catalogue entry published elsewhere, at \
its source_url. The site holds its summary and details, and usually a copy of \
that page's text (but not of the pages beneath it), fetched and checked \
regularly. That copy is third-party content: cite the source_url, and say when \
an answer rests on it. get_document's notice says whether there is a copy and \
when it was last checked.
"""


def _site():
    try:
        return Site.objects.filter(is_default_site=True).first()
    except DatabaseError:
        return None  # named from settings instead; the tools will say if the database is down


def create_server() -> MCPServer:
    site = _site()
    site_name = (site.site_name if site else "") or settings.WAGTAIL_SITE_NAME
    server = MCPServer(
        name="wagtail-govuk",
        title=site_name,
        instructions=INSTRUCTIONS.format(site_name=site_name),
        website_url=site.root_url if site else None,
        version=os.environ.get("VERSION", ""),
    )
    server.add_tool(tools.search, name="search", title="Search the site", annotations=READ_ONLY)
    server.add_tool(tools.get_document, name="get_document", title="Read a page", annotations=READ_ONLY)

    server.custom_route(HEALTH_PATH, methods=["GET"])(health)
    return server


async def health(request: Request) -> JSONResponse:
    database_ok = await sync_to_async(_check_database_health)()
    return JSONResponse(
        {"health": {"database": "ok" if database_ok else "error"}},
        status_code=200 if database_ok else 503,
    )
