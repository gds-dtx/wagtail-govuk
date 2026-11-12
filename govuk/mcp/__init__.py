"""The MCP server: the content index, searchable and readable by AI tools.

``tools`` holds the tools as plain functions over the index
(``govuk.content_index.search``); ``server`` registers them with the MCP SDK;
``app`` wraps the SDK's HTTP app with the site's own rules (maintenance, a
rate limit, the hosts it answers to); ``asgi`` is what uvicorn runs, started
by ``manage.py run_mcp_server``.

Read only, open to anyone, and gated by ``FEATURE_FLAGS["MCP"]``.
"""

MCP_PATH = "/mcp"
HEALTH_PATH = "/mcp/health"
