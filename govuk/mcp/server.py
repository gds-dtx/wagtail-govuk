"""Registers ``tools`` with the MCP SDK."""

import os

from django.conf import settings
from django.db import DatabaseError
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from wagtail.models import Site

from . import tools

READ_ONLY = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)

INSTRUCTIONS = """\
This server searches and reads what {site_name} publishes: its content \
catalogue of standards, policies, guidance, methods and tools and, on sites \
that include them, its other pages and the Capability Framework's roles and \
skills. Everything here is public.

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
        return None  # fall back to settings; the tools report a database outage


def create_server(*, auth=None, token_verifier=None) -> MCPServer:
    site = _site()
    site_name = (site.site_name if site else "") or settings.WAGTAIL_SITE_NAME
    server = MCPServer(
        name="wagtail-govuk",
        title=site_name,
        instructions=INSTRUCTIONS.format(site_name=site_name),
        website_url=site.root_url if site else None,
        version=os.environ.get("VERSION", ""),
        auth=auth,
        token_verifier=token_verifier,
    )
    server.add_tool(tools.search, name="search", title="Search the site", annotations=READ_ONLY)
    server.add_tool(tools.get_document, name="get_document", title="Read a page", annotations=READ_ONLY)
    return server
