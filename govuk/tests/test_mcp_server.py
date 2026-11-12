"""The MCP server: its tools, the protocol, and the HTTP app's own rules."""

from unittest import mock

import httpx2
from asgiref.sync import async_to_sync
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from django.core.management import CommandError, call_command
from django.test import TestCase, TransactionTestCase, override_settings
from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError
from wagtail.models import Site

from govuk.content_index import indexer
from govuk.mcp import HEALTH_PATH, MCP_PATH, tools
from govuk.mcp.app import create_app
from govuk.mcp.server import create_server
from govuk.models import ContentEntryExternalPage, ContentPage, MaintenanceModeSettings
from govuk.tests.catalogue_helpers import import_fixture, make_content_main_page
from govuk.tests.page_helpers import text_body

FLAGS = {"SKILLS": False, "FEEDBACK": False, "GUIDANCE": True, "MCP": True}
LIST_TOOLS = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"}


def _publish_page(title: str, html: str) -> ContentPage:
    home = Site.objects.get(is_default_site=True).root_page
    page = home.add_child(instance=ContentPage(title=title, slug=title.lower(), body=text_body(html)))
    page.save_revision().publish()
    return page


@override_settings(FEATURE_FLAGS=FLAGS)
class ToolTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.main = make_content_main_page(self.site.root_page)
        import_fixture(self.main)
        self.page = _publish_page("Accessibility", "<h2>Testing</h2><p>Test with assistive technology users.</p>")
        indexer.rebuild()

    def test_search_returns_results_with_their_sections(self):
        results = tools.search(query="assistive technology").results
        self.assertEqual([result.title for result in results], ["Accessibility"])
        result = results[0]
        self.assertEqual(result.url, self.site.root_url + "/accessibility/")
        self.assertEqual((result.kind, result.origin, result.type), ("page", "site", ""))
        self.assertEqual(result.section.heading, "Testing")
        self.assertIsNotNone(result.last_updated)

    def test_search_filters_by_type(self):
        results = tools.search(query="description", type="policy").results
        self.assertTrue(results)
        self.assertEqual({result.type for result in results}, {"Policy"})

    def test_a_query_that_cannot_be_run_is_refused(self):
        with self.assertRaises(ToolError):
            tools.search(query="   ")

    def test_get_document_reads_a_page(self):
        document = tools.get_document(url=self.site.root_url + "/accessibility/")
        self.assertEqual(document.title, "Accessibility")
        self.assertIn("## Testing", document.markdown)
        self.assertEqual([section.heading for section in document.sections], ["Testing"])
        self.assertFalse(document.truncated)
        self.assertIsNone(document.notice)
        self.assertNotIn("page_type", document.metadata)

    def test_an_external_entry_says_its_content_is_elsewhere(self):
        entry = ContentEntryExternalPage.objects.get(slug="external-policy")
        document = tools.get_document(url=entry.get_url_parts()[2])
        self.assertEqual(document.origin, "external")
        self.assertEqual(document.source_url, "https://www.gov.uk/guidance/external-policy")
        self.assertEqual(document.notice, tools.EXTERNAL_NOTICE + " Its page could not be read: Not fetched yet.")
        self.assertEqual(document.metadata["owner_organisation"], "Example Department")

    def test_a_section_address_names_the_section(self):
        document = tools.get_document(url="/content/standards-and-policies/hosted-policy/#policy-statement")
        self.assertEqual(document.requested_section, "1. Policy statement")

    def test_a_very_long_page_is_cut_short(self):
        with mock.patch.object(tools, "MAX_MARKDOWN_CHARS", 10):
            document = tools.get_document(url="/accessibility/")
        self.assertTrue(document.truncated)
        self.assertEqual(len(document.markdown), 10)

    def test_an_unknown_address_is_refused(self):
        with self.assertRaises(ToolError):
            tools.get_document(url="/no/such/page/")

    def test_calls_are_logged_without_what_was_asked(self):
        with self.assertLogs("govuk.mcp.tools", level="INFO") as logs:
            tools.search(query="assistive technology")
            with self.assertRaises(ToolError):
                tools.get_document(url="/a-private-question/")
        records = [(record.tool, record.outcome, record.results) for record in logs.records]
        self.assertEqual(records, [("search", "ok", 1), ("get_document", "refused", None)])
        output = "\n".join(logs.output) + "".join(str(vars(record)) for record in logs.records)
        self.assertNotIn("assistive", output)
        self.assertNotIn("a-private-question", output)


@override_settings(FEATURE_FLAGS=FLAGS)
class ProtocolTests(TransactionTestCase):
    """Through the SDK's own client, in process.

    A transaction test: the SDK runs each tool on a worker thread, with its own
    database connection, which would not see a test transaction's rows. The
    rollback is serialised so the site the migrations made is there each time.
    """

    serialized_rollback = True

    def setUp(self):
        _publish_page("Accessibility", "<h2>Testing</h2><p>Test with assistive technology users.</p>")
        indexer.rebuild()

    def _session(self, use):
        server = create_server()  # reads the site's name, which async code may not

        async def run():
            async with Client(server) as client:
                return await use(client)

        return async_to_sync(run)()

    def test_the_tools_are_listed_as_read_only(self):
        listed = self._session(lambda client: client.list_tools()).tools
        self.assertEqual([tool.name for tool in listed], ["search", "get_document"])
        for tool in listed:
            self.assertTrue(tool.annotations.read_only_hint)
            self.assertFalse(tool.annotations.open_world_hint)
            self.assertIsNotNone(tool.output_schema)
        search = listed[0]
        self.assertEqual(search.input_schema["required"], ["query"])
        self.assertEqual(search.input_schema["properties"]["limit"]["maximum"], 25)

    def test_search_then_read(self):
        async def use(client):
            found = await client.call_tool("search", {"query": "assistive technology"})
            url = found.structured_content["results"][0]["url"]
            return found, await client.call_tool("get_document", {"url": url})

        found, document = self._session(use)
        self.assertFalse(found.is_error)
        self.assertFalse(document.is_error)
        self.assertEqual(document.structured_content["title"], "Accessibility")

    def test_a_bad_argument_is_an_error_result(self):
        result = self._session(lambda client: client.call_tool("search", {"query": "x", "limit": 99}))
        self.assertTrue(result.is_error)


@override_settings(FEATURE_FLAGS=FLAGS, MCP_RATE_LIMIT_PER_MINUTE=0)
class AppTests(TestCase):
    """The HTTP app's own rules, over an ASGI transport with its lifespan running."""

    def setUp(self):
        cache.clear()

    def _requests(self, *requests, app=None):
        app = app or create_app()

        async def run():
            async with app.router.lifespan_context(app):
                transport = httpx2.ASGITransport(app=app)
                async with httpx2.AsyncClient(transport=transport, base_url="http://testserver") as client:
                    return [await client.request(method, path, **kwargs) for method, path, kwargs in requests]

        return async_to_sync(run)()

    def test_the_health_check(self):
        (response,) = self._requests(("GET", HEALTH_PATH, {}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"health": {"database": "ok"}})

    def test_only_the_sites_own_hosts_are_answered(self):
        (response,) = self._requests(
            ("POST", MCP_PATH, {"json": LIST_TOOLS, "headers": {**MCP_HEADERS, "Host": "evil.example"}})
        )
        self.assertEqual(response.status_code, 421)

    def test_a_browser_on_another_site_is_refused(self):
        (response,) = self._requests(
            ("POST", MCP_PATH, {"json": LIST_TOOLS, "headers": {**MCP_HEADERS, "Origin": "https://evil.example"}})
        )
        self.assertEqual(response.status_code, 403)

    @override_settings(MAINTENANCE_MODE=True, MAINTENANCE_RETRY_AFTER=120)
    def test_closed_for_maintenance_but_still_healthy(self):
        closed, health = self._requests(
            ("POST", MCP_PATH, {"json": LIST_TOOLS, "headers": MCP_HEADERS}),
            ("GET", HEALTH_PATH, {}),
        )
        self.assertEqual(closed.status_code, 503)
        self.assertEqual(closed.headers["Retry-After"], "120")
        self.assertEqual(health.status_code, 200)

    def test_planned_maintenance_closes_it_too(self):
        settings = MaintenanceModeSettings.for_site(Site.objects.get(is_default_site=True))
        settings.enabled = True
        settings.save()
        (response,) = self._requests(("POST", MCP_PATH, {"json": LIST_TOOLS, "headers": MCP_HEADERS}))
        self.assertEqual(response.status_code, 503)

    @override_settings(MCP_RATE_LIMIT_PER_MINUTE=2)
    def test_a_client_over_the_limit_is_told_to_wait(self):
        # Requests the host check refuses still count: the limit comes first.
        request = ("POST", MCP_PATH, {"json": LIST_TOOLS, "headers": {**MCP_HEADERS, "Host": "evil.example"}})
        responses = self._requests(request, request, request, ("GET", HEALTH_PATH, {}))
        self.assertEqual([response.status_code for response in responses], [421, 421, 429, 200])
        self.assertTrue(1 <= int(responses[2].headers["Retry-After"]) <= 60)

    @override_settings(FEATURE_FLAGS={**FLAGS, "MCP": False})
    def test_it_will_not_start_with_the_flag_off(self):
        with self.assertRaisesMessage(ImproperlyConfigured, "FEATURE_MCP"):
            create_app()


@override_settings(FEATURE_FLAGS=FLAGS, MCP_FORWARDED_ALLOW_IPS="10.0.0.0/16")
class CommandTests(TestCase):
    def test_it_runs_uvicorn_with_the_sites_logging(self):
        with mock.patch("uvicorn.run") as run:
            call_command("run_mcp_server", "--host", "0.0.0.0", "--port", "9000")
        run.assert_called_once()
        args, kwargs = run.call_args
        self.assertEqual(args, ("govuk.mcp.asgi:app",))
        self.assertEqual((kwargs["host"], kwargs["port"]), ("0.0.0.0", 9000))
        self.assertIsNone(kwargs["log_config"])
        self.assertTrue(kwargs["proxy_headers"])
        self.assertEqual(kwargs["forwarded_allow_ips"], "10.0.0.0/16")

    @override_settings(FEATURE_FLAGS={**FLAGS, "MCP": False})
    def test_it_refuses_with_the_flag_off(self):
        with self.assertRaisesMessage(CommandError, "FEATURE_MCP"):
            call_command("run_mcp_server")
