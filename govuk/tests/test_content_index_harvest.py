"""Fetching external catalogue entries' source pages into the content index.

No test goes to the network: the fetcher is passed in (``FakeWeb``), or, for
what runs on its own after a publish, patched where it is looked up.
"""

import json
from contextlib import contextmanager
from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from wagtail.models import Site

from govuk.content_index import fetch as fetch_module, harvest, indexer, sources
from govuk.content_index.fetch import FetchError, Response, Robots, fetch
from govuk.content_index.search import search_documents
from govuk.content_index.sources import (
    MAX_MARKDOWN_CHARS,
    ROBOTS_REFUSAL,
    NoText,
    NotModified,
    SourceSkipped,
    read_source,
)
from govuk.mcp import tools
from govuk.models import ContentEntryExternalPage, HarvestedSource, IndexedDocument
from govuk.tests.catalogue_helpers import import_fixture, make_content_main_page

FLAGS = {"SKILLS": False, "FEEDBACK": False, "GUIDANCE": True, "MCP": True}
SOURCE = "https://www.gov.uk/guidance/external-policy"
API = "https://www.gov.uk/api/content/guidance/external-policy"
OTHER_SOURCE = "https://www.gov.uk/guidance/external-standard"
OTHER_API = "https://www.gov.uk/api/content/guidance/external-standard"
LONG_TEXT = " ".join(["Organisations must keep their devices patched."] * 10)


def html_response(body: str, url: str, *, content_type="text/html; charset=utf-8", **headers) -> Response:
    return Response(200, url, {"content-type": content_type, **headers}, body.encode())


def api_response(data: dict, url: str = API, **headers) -> Response:
    return Response(200, url, {"content-type": "application/json", **headers}, json.dumps(data).encode())


def guide(body: str = f"<h2>Devices</h2><p>{LONG_TEXT}</p>", **fields) -> dict:
    return {
        "title": "External policy",
        "description": "What to do.",
        "public_updated_at": "2026-03-01T09:00:00+00:00",
        "details": {"body": body},
        **fields,
    }


class FakeWeb:
    """Answers each address from ``pages``: a Response, an exception, or a
    function of the request's ETag. Anything else is a 404, robots.txt included."""

    def __init__(self, pages=None):
        self.pages = dict(pages or {})
        self.requests: list[tuple[str, str]] = []

    def __call__(self, url, *, accept="", etag="", last_modified="", max_bytes=0, timeout=0):
        self.requests.append((url, etag))
        page = self.pages.get(url)
        if page is None:
            raise FetchError("The source answered 404.", status=404)
        if isinstance(page, Exception):
            raise page
        return page(etag) if callable(page) else page


class ReadSourceTests(SimpleTestCase):
    def test_a_govuk_page_is_read_from_the_content_api(self):
        web = FakeWeb({API: api_response(guide())})
        text = read_source(SOURCE, fetcher=web)
        self.assertEqual(web.requests, [(API, "")])
        self.assertEqual(text.title, "External policy")
        self.assertTrue(text.markdown.startswith("## Devices\n\nOrganisations must"))
        self.assertEqual(text.updated_at.isoformat(), "2026-03-01T09:00:00+00:00")
        self.assertEqual(text.fetched_url, SOURCE)

    def test_a_guides_parts_come_under_their_titles(self):
        data = guide(body="")
        data["details"]["parts"] = [
            {"title": "Overview", "body": f"<p>{LONG_TEXT}</p>"},
            {"title": "Eligibility", "body": "<p>Who can apply.</p>"},
        ]
        markdown = read_source(SOURCE, fetcher=FakeWeb({API: api_response(data)})).markdown
        self.assertIn("## Overview\n\nOrganisations must", markdown)
        self.assertIn("## Eligibility\n\nWho can apply.", markdown)

    def test_an_index_page_is_its_pages_in_order(self):
        children = [
            {"title": f"{n}. Point {n}", "base_path": f"/point-{n}", "description": "The point. " * 3}
            for n in (10, 2, 1)
        ]
        data = guide(body="", links={"children": children})
        markdown = read_source(SOURCE, fetcher=FakeWeb({API: api_response(data)})).markdown
        self.assertIn(
            "## Pages\n\n- [1. Point 1](https://www.gov.uk/point-1): The point. The point. The point.\n"
            "- [2. Point 2](https://www.gov.uk/point-2)",
            markdown,
        )
        self.assertLess(markdown.index("2. Point 2"), markdown.index("10. Point 10"))

    def test_the_toolkits_collections_and_a_publications_documents(self):
        data = guide(body=f"<p>{LONG_TEXT}</p>")
        data["details"]["collections"] = [
            {"title": "Design", "description": "Make it.", "links": [{"title": "Kit", "url": "https://kit.example/"}]}
        ]
        data["details"]["attachments"] = [{"title": "The report", "url": "/media/report.pdf"}]
        markdown = read_source(SOURCE, fetcher=FakeWeb({API: api_response(data)})).markdown
        self.assertIn("## Design\n\nMake it.\n\n- [Kit](https://kit.example/)", markdown)
        self.assertIn("## Documents\n\n- [The report](https://www.gov.uk/media/report.pdf)", markdown)

    def test_a_govuk_page_with_no_text(self):
        with self.assertRaises(NoText):
            read_source(SOURCE, fetcher=FakeWeb({API: api_response(guide(body="", description=""))}))

    def test_any_other_page_is_its_main_content(self):
        page = f"""<html><head><title>Kit – Example</title></head><body>
            <header>Site header</header><nav>Menu</nav>
            <main>
              <div class="govuk-breadcrumbs">Home › Kit</div>
              <h1>The kit</h1>
              <div><p>{LONG_TEXT} See <a href="/start/">how to start</a>.<span class="govuk-visually-hidden"> (opens)</span></p>
              <h2>Parts <strong>list</strong></h2>
              <ul><li>One<ul><li>Inner</li></ul></li><li>Two</li></ul>
              <table><tr><th>Name</th><th>Use</th></tr><tr><td>Bolt</td><td>Fix</td></tr></table>
              <pre>make build</pre>
              <script>track()</script></div>
            </main>
            <footer>Site footer</footer></body></html>"""
        text = read_source("https://kit.example/docs/", fetcher=FakeWeb({"https://kit.example/docs/": html_response(page, "https://kit.example/docs/")}))
        self.assertEqual(text.title, "The kit")
        markdown = text.markdown
        for gone in ("Site header", "Menu", "Site footer", "Home › Kit", "track()", "(opens)", "# The kit"):
            self.assertNotIn(gone, markdown)
        self.assertIn("See [how to start](https://kit.example/start/).", markdown)
        self.assertIn("## Parts **list**", markdown)
        self.assertIn("- One\n  - Inner\n- Two", markdown)
        self.assertIn("| Name | Use |\n| --- | --- |\n| Bolt | Fix |", markdown)
        self.assertIn("```\nmake build\n```", markdown)

    def test_a_github_repository_is_its_readme(self):
        page = f'<main><div>Files: a.md b.md</div><article class="markdown-body"><h1>Styles</h1><p>{LONG_TEXT}</p></article></main>'
        url = "https://github.com/example/styles"
        text = read_source(url, fetcher=FakeWeb({url: html_response(page, url)}))
        self.assertEqual(text.title, "Styles")
        self.assertNotIn("Files:", text.markdown)

    def test_pages_the_index_does_not_read(self):
        url = "https://kit.example/"
        with self.assertRaises(NoText):
            read_source(url, fetcher=FakeWeb({url: html_response("<main><div id='app'></div></main>", url)}))
        with self.assertRaises(SourceSkipped):
            read_source(url, fetcher=FakeWeb({url: html_response("%PDF", url, content_type="application/pdf")}))
        with self.assertRaises(SourceSkipped):
            read_source("http://kit.example/", fetcher=FakeWeb())

    def test_robots_txt_is_respected(self):
        url = "https://kit.example/private/page"
        robots = Response(200, "https://kit.example/robots.txt", {}, b"User-agent: *\nDisallow: /private/\n")
        web = FakeWeb({"https://kit.example/robots.txt": robots})
        with self.assertRaises(FetchError) as raised:
            read_source(url, fetcher=web, robots=Robots(web))
        self.assertEqual(raised.exception.status, ROBOTS_REFUSAL)
        self.assertEqual([request for request, _ in web.requests], ["https://kit.example/robots.txt"])

    def test_a_missing_robots_txt_allows_and_an_unreadable_one_does_not(self):
        self.assertTrue(Robots(FakeWeb()).allows("https://kit.example/page"))
        broken = FakeWeb({"https://kit.example/robots.txt": FetchError("down", status=503)})
        self.assertFalse(Robots(broken).allows("https://kit.example/page"))

    def test_an_unchanged_page_answers_not_modified(self):
        web = FakeWeb({API: lambda etag: Response(304, API, {}, b"") if etag == '"v1"' else api_response(guide())})
        with self.assertRaises(NotModified):
            read_source(SOURCE, fetcher=web, etag='"v1"')

    def test_a_very_long_page_is_cut_short(self):
        body = "".join(f"<p>Paragraph {n} {LONG_TEXT}</p>" for n in range(600))
        markdown = read_source(SOURCE, fetcher=FakeWeb({API: api_response(guide(body=body))})).markdown
        self.assertLessEqual(len(markdown), MAX_MARKDOWN_CHARS)
        self.assertTrue(markdown.endswith("patched."))


class FetchGuardTests(SimpleTestCase):
    def test_only_https_public_addresses_are_fetched(self):
        for url in ("http://www.gov.uk/", "https://localhost/", "https://10.0.0.1/", "https://intranet/"):
            with self.subTest(url=url), self.assertRaises(FetchError):
                fetch(url)


@override_settings(FEATURE_FLAGS=FLAGS)
class HarvestTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.main = make_content_main_page(self.site.root_page)
        import_fixture(self.main)
        self.entry = ContentEntryExternalPage.objects.get(slug="external-policy")
        indexer.rebuild()

    def _document(self):
        return IndexedDocument.objects.get(page_id=self.entry.pk)

    def test_a_fetched_page_is_indexed_with_its_entry(self):
        source = harvest.harvest_source(SOURCE, fetcher=FakeWeb({API: api_response(guide(), etag='"v1"')}))
        self.assertEqual(source.status, HarvestedSource.Status.OK)
        self.assertEqual(source.etag, '"v1"')
        document = self._document()
        self.assertEqual(document.metadata["source_text"], "fetched")
        self.assertEqual(document.metadata["source_title"], "External policy")
        self.assertIn("## Devices", document.markdown)
        self.assertTrue(document.markdown.startswith("Long description of external-policy."))
        self.assertEqual(search_documents("devices patched")[0].document.page_id, self.entry.pk)

    def test_an_unchanged_page_is_checked_not_refetched(self):
        web = FakeWeb({API: lambda etag: Response(304, API, {}, b"") if etag == '"v1"' else api_response(guide(), etag='"v1"')})
        harvest.harvest_source(SOURCE, fetcher=web)
        source = harvest.harvest_source(SOURCE, fetcher=web)
        self.assertEqual(web.requests[-1], (API, '"v1"'))
        self.assertEqual((source.status, source.http_status), (HarvestedSource.Status.OK, 304))
        self.assertIn("Organisations must", source.markdown)

    def test_a_refusal_or_failure_keeps_the_text_fetched_before(self):
        harvest.harvest_source(SOURCE, fetcher=FakeWeb({API: api_response(guide())}))
        refused = harvest.harvest_source(SOURCE, fetcher=FakeWeb({API: FetchError("The source answered 403.", status=403)}))
        self.assertEqual((refused.status, refused.http_status, refused.failures), ("blocked", 403, 1))
        failed = harvest.harvest_source(SOURCE, fetcher=FakeWeb({API: FetchError("Could not reach the source.")}))
        self.assertEqual((failed.status, failed.failures), ("failed", 2))
        self.assertIn("Organisations must", failed.markdown)
        self.assertEqual(self._document().metadata["source_text"], "fetched")

    def test_a_page_with_no_text_any_more_is_emptied(self):
        harvest.harvest_source(SOURCE, fetcher=FakeWeb({API: api_response(guide())}))
        source = harvest.harvest_source(SOURCE, fetcher=FakeWeb({API: api_response(guide(body="", description=""))}))
        self.assertEqual((source.status, source.markdown), ("no_text", ""))
        metadata = self._document().metadata
        self.assertEqual(metadata["source_text"], "unavailable")
        self.assertEqual(metadata["source_text_reason"], "GOV.UK holds no text for this page.")

    def test_harvest_all_fetches_every_source_and_drops_unused_rows(self):
        HarvestedSource.objects.create(url="https://gone.example/")
        web = FakeWeb({API: api_response(guide()), OTHER_API: api_response(guide(), url=OTHER_API)})
        result = harvest.harvest_all(fetcher=web, delay=0)
        self.assertEqual(set(HarvestedSource.objects.values_list("url", flat=True)), set(harvest.source_urls()))
        self.assertIn(SOURCE, harvest.source_urls())
        self.assertEqual(result.counts["removed"], 1)
        self.assertGreaterEqual(result.counts["ok"], 2)
        self.assertTrue(any(line.startswith("ok       " + SOURCE) for line in result.lines))

    def test_the_mcp_server_says_where_the_text_came_from(self):
        harvest.harvest_source(SOURCE, fetcher=FakeWeb({API: api_response(guide())}))
        document = tools.get_document(url=SOURCE)
        self.assertIn("includes a copy of that page, last checked on", document.notice)
        result = tools.search(query="devices patched").results[0]
        self.assertIsNotNone(result.source_checked_at)
        harvest.harvest_source(OTHER_SOURCE, fetcher=FakeWeb({OTHER_API: FetchError("The source answered 403.", status=403)}))
        other = tools.get_document(url=OTHER_SOURCE)
        self.assertTrue(other.notice.endswith("Its page could not be read: The source answered 403."))


@contextmanager
def patched_web(web):
    """The fetcher, where what runs on its own after a publish looks it up."""
    with mock.patch.object(sources, "fetch", web), mock.patch.object(fetch_module, "fetch", web):
        yield web


@override_settings(FEATURE_FLAGS=FLAGS)
class PublishTests(TestCase):
    """Publishing never fetches a source: the timer and Fetch now do."""

    def test_publishing_an_entry_with_a_new_source_fetches_nothing(self):
        main = make_content_main_page(Site.objects.get(is_default_site=True).root_page)
        import_fixture(main)
        entry = ContentEntryExternalPage.objects.get(slug="external-policy")
        entry.source_url = OTHER_SOURCE
        with patched_web(FakeWeb()) as web, self.captureOnCommitCallbacks(execute=True):
            entry.save_revision().publish()
        self.assertEqual(web.requests, [])
        self.assertFalse(HarvestedSource.objects.exists())
        metadata = IndexedDocument.objects.get(page_id=entry.pk).metadata
        self.assertEqual((metadata["source_text"], metadata["source_text_reason"]), ("unavailable", "Not fetched yet."))

    def test_an_import_fetches_nothing(self):
        main = make_content_main_page(Site.objects.get(is_default_site=True).root_page)
        with patched_web(FakeWeb()) as web, self.captureOnCommitCallbacks(execute=True):
            import_fixture(main)
        self.assertEqual(web.requests, [])
        self.assertTrue(
            IndexedDocument.objects.filter(page_id__in=ContentEntryExternalPage.objects.values("pk")).exists()
        )


@override_settings(FEATURE_FLAGS=FLAGS)
class AdminTests(TestCase):
    def setUp(self):
        self.main = make_content_main_page(Site.objects.get(is_default_site=True).root_page)
        import_fixture(self.main)
        self.entry = ContentEntryExternalPage.objects.get(slug="external-policy")
        self.admin = get_user_model().objects.create_superuser("admin", "a@example.gov.uk", "x")
        self.client.force_login(self.admin)
        self.fetch_url = reverse("govuk_catalogue_fetch_source", args=[self.entry.pk])

    def test_the_edit_view_shows_the_source_text_panel(self):
        HarvestedSource.objects.create(url=SOURCE, status="blocked", reason="The source answered 403.")
        response = self.client.get(reverse("wagtailadmin_pages:edit", args=[self.entry.pk]))
        self.assertContains(response, "Source text")
        self.assertContains(response, "Refused by the source")
        self.assertContains(response, "The source answered 403.")
        self.assertContains(response, self.fetch_url)

    @override_settings(FEATURE_FLAGS={**FLAGS, "MCP": False})
    def test_no_panel_and_no_fetch_with_the_index_off(self):
        response = self.client.get(reverse("wagtailadmin_pages:edit", args=[self.entry.pk]))
        self.assertNotContains(response, self.fetch_url)
        self.assertEqual(self.client.get(self.fetch_url).status_code, 404)

    def test_fetch_now_asks_then_fetches(self):
        self.assertContains(self.client.get(self.fetch_url), "Fetch now")
        with patched_web(FakeWeb({API: api_response(guide())})):
            response = self.client.post(self.fetch_url, follow=True)
        self.assertRedirects(response, reverse("wagtailadmin_pages:edit", args=[self.entry.pk]))
        self.assertContains(response, f"Fetched {SOURCE}")
        self.assertEqual(HarvestedSource.objects.get(url=SOURCE).status, "ok")

    def test_fetch_now_needs_permission_to_publish(self):
        editor = get_user_model().objects.create_user("editor", "e@example.gov.uk", "x")
        editor.groups.add(Group.objects.get(name="Editors"))
        self.client.force_login(editor)
        with patched_web(FakeWeb()) as web:
            response = self.client.post(self.fetch_url)
        # Wagtail's permission_denied: back to the dashboard, not to the page.
        self.assertRedirects(response, reverse("wagtailadmin_home"), fetch_redirect_response=False)
        self.assertEqual(web.requests, [])
        self.assertFalse(HarvestedSource.objects.filter(url=SOURCE).exists())


@override_settings(FEATURE_FLAGS=FLAGS)
class CommandTests(TestCase):
    def setUp(self):
        self.main = make_content_main_page(Site.objects.get(is_default_site=True).root_page)
        import_fixture(self.main)

    def test_it_reports_each_source_and_the_totals(self):
        out = StringIO()
        with mock.patch.object(harvest, "HOST_DELAY_SECONDS", 0), patched_web(
            FakeWeb({API: api_response(guide()), OTHER_API: FetchError("The source answered 403.", status=403)})
        ):
            call_command("harvest_external_sources", "--url", SOURCE, "--url", OTHER_SOURCE, stdout=out)
        output = out.getvalue()
        self.assertIn(f"ok       {SOURCE}: ", output)
        self.assertIn(f"blocked  {OTHER_SOURCE}: The source answered 403.", output)
        self.assertIn("1 ok, 0 failed, 1 blocked", output)

    @override_settings(FEATURE_FLAGS={**FLAGS, "MCP": False})
    def test_it_refuses_with_the_index_off(self):
        with self.assertRaisesMessage(CommandError, "FEATURE_MCP"):
            call_command("harvest_external_sources")
