"""The content index: what it holds, how it is split, how it keeps current, and the external sources harvested into it."""

import json
from contextlib import contextmanager
from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from wagtail.models import Page, PageViewRestriction, Site

from govuk.catalogue.presentation import render_blocks
from govuk.content_index import fetch as fetch_module, harvest, indexer, sources
from govuk.content_index.chunking import Part, chunk_parts, parts_markdown
from govuk.content_index.fetch import FetchError, Response, Robots, fetch
from govuk.content_index.markdown import (
    Links,
    block_markdown,
    html_text,
    rich_text_markdown,
)
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
from govuk.models import (
    ContentEntryExternalPage,
    ContentEntryPolicyPage,
    ContentEntryRequirementsPage,
    ContentPage,
    ContentRequirementPage,
    FrameworkSkillsPage,
    GovukRole,
    GovukSkill,
    HarvestedSource,
    IndexedChunk,
    IndexedDocument,
)
from govuk.tests.catalogue_helpers import import_fixture, make_content_main_page
from govuk.tests.helpers import (
    make_framework_main_page,
    publish_page,
    role_url,
    text_body,
)

FLAGS = {"SKILLS": True, "FEEDBACK": False, "CATALOGUE": True, "AI": True}


def _document(**source) -> IndexedDocument:
    return IndexedDocument.objects.get(**source)


class ChunkingTests(SimpleTestCase):
    def test_sections_start_at_h2_and_h3_and_carry_the_headings_above(self):
        chunks = chunk_parts(
            [Part("Opening.\n\n## Testing\n\nHow to test.\n\n### Screen readers\n\nUse one.\n\n## Next\n\nThen.")]
        )
        self.assertEqual(
            [(chunk.heading_path, chunk.text) for chunk in chunks],
            [
                ("", "Opening."),
                ("Testing", "How to test."),
                ("Testing › Screen readers", "Use one."),
                ("Next", "Then."),
            ],
        )

    def test_a_heading_straight_into_another_has_no_section_of_its_own(self):
        chunks = chunk_parts([Part("## Scope\n\n### Devices\n\nPhones.")])
        self.assertEqual([chunk.heading_path for chunk in chunks], ["Scope › Devices"])

    def test_h4_stays_in_its_section(self):
        chunks = chunk_parts([Part("## Scope\n\n#### Small print\n\nDetail.")])
        self.assertEqual(len(chunks), 1)
        self.assertIn("#### Small print", chunks[0].text)

    def test_a_part_heading_opens_a_section(self):
        chunks = chunk_parts([Part("Text.", heading="1. Policy statement", anchor="policy-statement")])
        self.assertEqual(chunks[0].heading_path, "1. Policy statement")
        self.assertEqual(chunks[0].anchor, "policy-statement")
        self.assertEqual(parts_markdown([Part("Text.", heading="1. Policy statement")]), "## 1. Policy statement\n\nText.\n")

    def test_a_long_section_is_split_between_paragraphs(self):
        paragraphs = [f"Paragraph {n} " + " ".join(["word"] * 60) for n in range(10)]
        chunks = chunk_parts([Part("## Long\n\n" + "\n\n".join(paragraphs))], max_chars=800)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(chunk.heading_path == "Long" for chunk in chunks))
        self.assertEqual("\n\n".join(chunk.text for chunk in chunks), "\n\n".join(paragraphs))

    def test_a_code_block_is_never_split_or_read_for_headings(self):
        code = "```\n## not a heading\n\n" + "x = 1\n\n" * 200 + "```"
        chunks = chunk_parts([Part(f"## Example\n\n{code}")], max_chars=200)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].heading_path, "Example")

    def test_anchors_are_counted_as_the_page_counts_them(self):
        chunks = chunk_parts([Part("## Notes\n\nA.\n\n#### Notes\n\nB.\n\n## Notes\n\nC.", anchors=True)])
        self.assertEqual([chunk.anchor for chunk in chunks], ["notes", "notes-2"])
        # Each part is a stream of its own, counted afresh.
        chunks = chunk_parts([Part("## Notes\n\nA.", anchors=True), Part("## Notes\n\nB.", anchors=True)])
        self.assertEqual([chunk.anchor for chunk in chunks], ["notes", "notes"])

    def test_a_heading_with_code_in_it_gets_the_id_the_page_gives_it(self):
        chunks = chunk_parts([Part("## Do not use `git push -f`, use `--force` instead\n\nWhy.", anchors=True)])
        _, headings = render_blocks(
            ContentPage.body.field.stream_block.to_python(
                text_body("<h2>Do not use <code>git push -f</code>, use <code>--force</code> instead</h2>")
            ),
            {},
        )
        self.assertEqual(chunks[0].anchor, headings[0]["anchor"])
        self.assertEqual(chunks[0].heading_path, "Do not use git push -f, use --force instead")

    def test_no_anchors_where_the_page_makes_its_ids_in_the_browser(self):
        self.assertEqual(chunk_parts([Part("## Notes\n\nA.")])[0].anchor, "")


class MarkdownTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.links = Links(self.site.root_url)
        self.target = self.site.root_page.add_child(instance=ContentPage(title="Target", slug="target"))

    def test_rich_text_page_links_become_full_addresses(self):
        markdown = rich_text_markdown(
            f'<p>See <a linktype="page" id="{self.target.pk}">the target</a> or <a href="/elsewhere/">here</a>.</p>',
            self.links,
        )
        self.assertIn(f"[the target]({self.target.get_full_url()})", markdown)
        self.assertIn(f"[here]({self.site.root_url}/elsewhere/)", markdown)

    def test_an_embedded_image_is_its_alt_text(self):
        markdown = rich_text_markdown('<p>Before <embed embedtype="image" id="1" alt="A chart"/> after</p>')
        self.assertNotIn("<embed", markdown)
        self.assertIn("A chart", markdown)

    def test_cards_are_a_list_of_links(self):
        block = ContentPage.body.field.stream_block
        value = block.to_python(
            [
                {
                    "type": "cards",
                    "value": {
                        "heading": "Get started",
                        "cards": [
                            {"title": "Target", "text": "<p>Go here.</p>", "link": {"page": self.target.pk}},
                            {"title": "Out", "link": {"external_url": "https://example.gov.uk/"}},
                        ],
                    },
                }
            ]
        )
        markdown = block_markdown("cards", value[0].value, self.links)
        self.assertIn("## Get started", markdown)
        self.assertIn(f"- **[Target]({self.target.get_full_url()})**: Go here.", markdown)
        self.assertIn("- **[Out](https://example.gov.uk/)**", markdown)

    def test_a_banner_is_its_heading_text_and_button(self):
        value = ContentPage.body.field.stream_block.to_python(
            [
                {
                    "type": "banner",
                    "value": {
                        "heading": "Join us",
                        "text": "<p>Every month.</p>",
                        "button_text": "Sign up",
                        "button_external_url": "https://example.gov.uk/join",
                        "style": "light",
                    },
                }
            ]
        )
        self.assertEqual(
            block_markdown("banner", value[0].value, self.links),
            "## Join us\n\nEvery month.\n\n[Sign up](https://example.gov.uk/join)",
        )

    def test_a_table_is_a_pipe_table(self):
        value = ContentPage.body.field.stream_block.to_python(
            [
                {
                    "type": "table",
                    "value": {
                        "table": {
                            "data": [["Name", "Grade"], ["Analyst", "SEO"]],
                            "first_row_is_table_header": True,
                            "table_caption": "Grades",
                        },
                        "column_widths": [],
                    },
                }
            ]
        )
        self.assertEqual(
            block_markdown("table", value[0].value),
            "**Grades**\n\n| Name | Grade |\n| --- | --- |\n| Analyst | SEO |",
        )

    def test_raw_html_is_text(self):
        text = html_text(
            "<div><p>One <a href='#'>link</a>.</p><table><tr><th>A</th><th>B</th></tr>"
            "<tr><td>1</td><td>2</td></tr></table><script>x()</script></div>"
        )
        self.assertEqual(text, "One link.\n| A | B |\n| --- | --- |\n| 1 | 2 |")


@override_settings(FEATURE_FLAGS=FLAGS)
class PageIndexTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.home = self.site.root_page
        self.section = self._publish(ContentPage(title="Section", slug="section"), self.home)
        self.page = self._publish(
            ContentPage(
                title="Accessibility",
                slug="accessibility",
                search_description="How to make services accessible.",
                hero_intro="<p>Everyone should be able to use it.</p>",
                body=text_body("<p>Opening.</p><h2>Testing</h2><p>Test with users.</p>"),
            ),
            self.section,
        )

    def _publish(self, page, parent):
        with self.captureOnCommitCallbacks(execute=True):
            parent.add_child(instance=page)
            page.save_revision().publish()
        return Page.objects.get(pk=page.pk).specific

    def test_a_published_page_is_indexed(self):
        document = _document(page=self.page)
        self.assertEqual(document.kind, "page")
        self.assertEqual(document.origin, "site")
        self.assertEqual(document.url_path, "/section/accessibility/")
        self.assertEqual(document.site, self.site)
        self.assertEqual(document.description, "How to make services accessible.")
        self.assertEqual(document.breadcrumbs, [{"title": "Section", "url_path": "/section/"}])
        self.assertEqual(
            document.markdown,
            "Everyone should be able to use it.\n\nOpening.\n\n## Testing\n\nTest with users.\n",
        )
        self.assertEqual(
            [(chunk.heading_path, chunk.text) for chunk in document.chunks.all()],
            [("", "Everyone should be able to use it.\n\nOpening."), ("Testing", "Test with users.")],
        )

    def test_a_draft_is_not_indexed_until_it_is_published(self):
        self.page.body = text_body("<p>Rewritten.</p>")
        with self.captureOnCommitCallbacks(execute=True):
            self.page.save_revision()
        self.assertIn("Opening.", _document(page=self.page).markdown)
        with self.captureOnCommitCallbacks(execute=True):
            self.page.get_latest_revision().publish()
        self.assertEqual(_document(page=self.page).markdown, "Everyone should be able to use it.\n\nRewritten.\n")

    def test_unpublishing_removes_the_page(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.page.unpublish()
        self.assertFalse(IndexedDocument.objects.filter(page_id=self.page.pk).exists())

    def test_deleting_removes_the_page_and_its_chunks(self):
        document = _document(page=self.page)
        self.page.delete()
        self.assertFalse(IndexedDocument.objects.filter(pk=document.pk).exists())
        self.assertFalse(IndexedChunk.objects.filter(document_id=document.pk).exists())

    def test_a_view_restriction_removes_the_page_and_those_beneath_it(self):
        with self.captureOnCommitCallbacks(execute=True):
            PageViewRestriction.objects.create(page=self.section, restriction_type=PageViewRestriction.LOGIN)
        self.assertFalse(IndexedDocument.objects.filter(page_id__in=[self.section.pk, self.page.pk]).exists())
        with self.captureOnCommitCallbacks(execute=True):
            PageViewRestriction.objects.filter(page=self.section).delete()
        self.assertTrue(IndexedDocument.objects.filter(page_id=self.page.pk).exists())

    def test_a_group_restriction_leaves_the_page_out_too(self):
        restriction = PageViewRestriction.objects.create(
            page=self.page, restriction_type=PageViewRestriction.GROUPS
        )
        restriction.groups.add(Group.objects.create(name="Insiders"))
        indexer.rebuild()
        self.assertFalse(IndexedDocument.objects.filter(page_id=self.page.pk).exists())

    def test_moving_a_page_changes_the_addresses_beneath_it(self):
        other = self._publish(ContentPage(title="Other", slug="other"), self.home)
        with self.captureOnCommitCallbacks(execute=True):
            Page.objects.get(pk=self.section.pk).move(other, pos="last-child")
        self.assertEqual(_document(page=self.page).url_path, "/other/section/accessibility/")

    def test_renaming_a_page_updates_the_breadcrumbs_beneath_it(self):
        self.section.title = "Guides"
        with self.captureOnCommitCallbacks(execute=True):
            self.section.save_revision().publish()
        self.assertEqual(_document(page=self.page).breadcrumbs, [{"title": "Guides", "url_path": "/section/"}])

    def test_a_failure_to_index_does_not_stop_the_publish(self):
        def page_changed(page_id):
            raise RuntimeError("boom")

        self.page.title = "Accessibility for all"
        with (
            mock.patch.object(indexer, "page_changed", page_changed),
            self.assertLogs("govuk.content_index.signals", level="ERROR"),
            self.captureOnCommitCallbacks(execute=True),
        ):
            self.page.save_revision().publish()
        self.assertEqual(Page.objects.get(pk=self.page.pk).title, "Accessibility for all")

    @override_settings(FEATURE_FLAGS={**FLAGS, "AI": False})
    def test_nothing_is_written_with_the_index_off(self):
        page = self._publish(ContentPage(title="Quiet", slug="quiet"), self.home)
        self.assertFalse(IndexedDocument.objects.filter(page_id=page.pk).exists())

    def test_the_tree_root_and_bare_pages_are_left_out(self):
        indexer.rebuild()
        self.assertFalse(IndexedDocument.objects.filter(page_id=Page.get_first_root_node().pk).exists())
        # The default site's home page is a bare Page with nothing on it.
        if type(self.home.specific) is Page:
            self.assertFalse(IndexedDocument.objects.filter(page_id=self.home.pk).exists())


@override_settings(FEATURE_FLAGS=FLAGS)
class RebuildTests(TestCase):
    def setUp(self):
        self.page = publish_page("Guide", "<p>Text.</p>")

    def test_a_second_rebuild_changes_nothing(self):
        first = indexer.rebuild()
        self.assertEqual(first.counts["created"], IndexedDocument.objects.count())
        second = indexer.rebuild()
        self.assertEqual(second.counts["created"] + second.counts["updated"], 0)
        self.assertEqual(second.counts["unchanged"], IndexedDocument.objects.count())

    def test_force_and_a_new_index_version_rewrite_every_row(self):
        indexer.rebuild()
        count = IndexedDocument.objects.count()
        self.assertEqual(indexer.rebuild(force=True).counts["updated"], count)
        with mock.patch.object(indexer, "INDEX_VERSION", indexer.INDEX_VERSION + 1):
            self.assertEqual(indexer.rebuild().counts["updated"], count)

    def test_a_dry_run_writes_nothing(self):
        result = indexer.rebuild(dry_run=True)
        self.assertGreater(result.counts["created"], 0)
        self.assertFalse(IndexedDocument.objects.exists())

    def test_a_row_readers_can_no_longer_reach_is_removed(self):
        indexer.rebuild()
        Page.objects.filter(pk=self.page.pk).update(live=False)
        self.assertEqual(indexer.rebuild().counts["removed"], 1)

    def test_one_failure_is_reported_and_the_rest_still_index(self):
        other = Site.objects.get(is_default_site=True).root_page.add_child(
            instance=ContentPage(title="Other", slug="other")
        )
        other.save_revision().publish()
        real = indexer.page_document

        def page_document(page, ctx):
            if page.pk == self.page.pk:
                raise ValueError("unreadable")
            return real(page, ctx)

        with mock.patch.object(indexer, "page_document", side_effect=page_document), self.assertLogs(
            "govuk.content_index.indexer", level="ERROR"
        ):
            result = indexer.rebuild()
        self.assertEqual(result.failures, [f"page {self.page.pk}: unreadable"])
        self.assertTrue(IndexedDocument.objects.filter(page_id=other.pk).exists())

    def test_the_command_reports_its_counts(self):
        out = StringIO()
        call_command("rebuild_content_index", stdout=out)
        self.assertIn("created", out.getvalue())
        out = StringIO()
        call_command("rebuild_content_index", "--dry-run", stdout=out)
        self.assertTrue(out.getvalue().startswith("Dry run, nothing written: "))

    @override_settings(FEATURE_FLAGS={**FLAGS, "AI": False})
    def test_the_commands_refuse_with_the_index_off(self):
        for command in ("rebuild_content_index", "embed_content_index", "harvest_external_sources"):
            with self.subTest(command=command), self.assertRaisesMessage(CommandError, "FEATURE_AI"):
                call_command(command)

    @override_settings(FEATURE_FLAGS={**FLAGS, "SKILLS": False})
    def test_framework_pages_are_left_out_without_the_framework(self):
        main = make_framework_main_page(Site.objects.get(is_default_site=True).root_page)
        indexer.rebuild()
        self.assertFalse(IndexedDocument.objects.filter(page_id=main.pk).exists())
        self.assertTrue(IndexedDocument.objects.filter(page_id=self.page.pk).exists())


@override_settings(FEATURE_FLAGS=FLAGS)
class FrameworkIndexTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        home = self.site.root_page
        self.forensics = GovukSkill.objects.create(
            title="Forensics",
            body="<p>The capture and analysis of evidence.</p>",
            working_points=[{"type": "point", "value": "Analyses digital evidence."}],
        )
        self.role = GovukRole.objects.create(
            title="Digital forensics analyst",
            family="Security",
            body="<p>Acquires and analyses evidence.</p>",
            levels=[
                {
                    "type": "level",
                    "value": {
                        "title": "Associate digital forensics analyst",
                        "description": "<p>Supports investigations.</p>",
                        "grades": ["eo"],
                        "skills": [{"skill": self.forensics.pk, "level": "working"}],
                    },
                }
            ],
        )
        self.main = make_framework_main_page(home)
        self.skills_page = self.main.add_child(instance=FrameworkSkillsPage(title="Skills", slug="skills"))
        self.skills_page.save_revision().publish()
        indexer.rebuild()

    def test_a_role_is_indexed_at_its_route(self):
        document = _document(role=self.role)
        self.assertEqual(document.kind, "role")
        self.assertEqual(document.type_label, "Role")
        self.assertEqual(document.url_path, role_url(self.main, self.role))
        self.assertEqual(document.metadata["family"], "Security")
        skill_url = f"{self.skills_page.get_full_url()}#{self.forensics.slug}"
        self.assertIn("## Associate digital forensics analyst", document.markdown)
        self.assertIn("Supports investigations.", document.markdown)
        self.assertIn(f"- [Forensics]({skill_url}) at working level", document.markdown)
        self.assertIn("  - Analyses digital evidence.", document.markdown)

    def test_a_skill_is_indexed_at_its_place_in_the_a_to_z(self):
        document = _document(skill=self.forensics)
        self.assertEqual(document.url_path, f"/capability-framework/skills/#{self.forensics.slug}")
        self.assertIn("## Working\n\nYou can:\n\n- Analyses digital evidence.", document.markdown)

    def test_publishing_a_skill_updates_the_roles_that_list_it(self):
        self.forensics.title = "Digital forensics"
        with self.captureOnCommitCallbacks(execute=True):
            self.forensics.save_revision().publish()
        self.assertEqual(_document(skill=self.forensics).title, "Digital forensics")
        self.assertIn("[Digital forensics](", _document(role=self.role).markdown)

    def test_unpublishing_a_skill_takes_it_off_its_roles(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.forensics.unpublish()
        self.assertFalse(IndexedDocument.objects.filter(skill=self.forensics).exists())
        self.assertNotIn("Forensics", _document(role=self.role).markdown)

    def test_unpublishing_a_role_removes_it(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.role.unpublish()
        self.assertFalse(IndexedDocument.objects.filter(role=self.role).exists())

    def test_a_role_draft_is_not_indexed(self):
        self.role.title = "Renamed"
        with self.captureOnCommitCallbacks(execute=True):
            self.role.save_revision()
        self.assertEqual(_document(role=self.role).title, "Digital forensics analyst")

    def test_roles_follow_the_main_page(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.main.unpublish()
        self.assertFalse(IndexedDocument.objects.filter(role=self.role).exists())


@override_settings(FEATURE_FLAGS=FLAGS)
class CatalogueIndexTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.main = make_content_main_page(self.site.root_page)
        import_fixture(self.main)
        indexer.rebuild()
        self.standard = ContentEntryRequirementsPage.objects.get(slug="hosted-standard")

    def test_an_entry_carries_its_details(self):
        document = _document(page=self.standard)
        self.assertEqual(document.type_label, "Standard")
        self.assertEqual(document.description, "Short description of hosted-standard")
        self.assertEqual(document.metadata["owner_organisation"], "Example Department")
        self.assertEqual(document.metadata["topics"], ["Cloud", "Security"])
        self.assertEqual(document.metadata["scope"], "Public sector")
        self.assertTrue(document.markdown.startswith("Long description of hosted-standard."))
        self.assertIn("## Points\n\n1. [The first point](", document.markdown)

    def test_an_external_entry_is_indexed_with_its_source(self):
        entry = ContentEntryExternalPage.objects.get(slug="external-policy")
        document = _document(page=entry)
        self.assertEqual(document.origin, "external")
        self.assertEqual(document.source_url, "https://www.gov.uk/guidance/external-policy")
        self.assertEqual(document.markdown, "Long description of external-policy.\n")

    def test_section_anchors_match_the_page(self):
        _, headings = render_blocks(self.standard.body, {"page": self.standard, "self": self.standard})
        chunks = _document(page=self.standard).chunks.exclude(anchor="")
        self.assertEqual(
            [chunk.anchor for chunk in chunks if chunk.heading_path != "Points"][: len(headings)],
            [heading["anchor"] for heading in headings],
        )

    def test_a_policy_is_its_numbered_sections(self):
        policy = ContentEntryPolicyPage.objects.get(slug="hosted-policy")
        document = _document(page=policy)
        self.assertIn("## 1. Policy statement", document.markdown)
        chunks = {chunk.heading_path: chunk.anchor for chunk in document.chunks.all()}
        self.assertEqual(chunks["1. Policy statement"], "policy-statement")
        self.assertEqual(chunks["2. Scope › Devices"], "devices")
        self.assertEqual(document.metadata["version"], "2.1")

    def test_points_are_numbered_and_renumbered_when_moved(self):
        first, second = ContentRequirementPage.objects.child_of(self.standard).order_by("path")
        self.assertEqual(_document(page=first).metadata["number"], 1)
        with self.captureOnCommitCallbacks(execute=True):
            Page.objects.get(pk=first.pk).move(self.standard, pos="last-child")
        self.assertEqual(_document(page=first).metadata["number"], 2)
        self.assertEqual(_document(page=second).metadata["number"], 1)
        self.assertIn("1. [The second point](", _document(page=self.standard).markdown)

    @override_settings(FEATURE_FLAGS={**FLAGS, "CATALOGUE": False})
    def test_catalogue_pages_are_left_out_without_the_catalogue(self):
        indexer.rebuild()
        self.assertFalse(IndexedDocument.objects.filter(page_id=self.standard.pk).exists())


# -- harvesting external sources; the fetcher is always faked --------------------------


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
    """Answers from ``pages`` (a Response, an exception or a function of the ETag); anything else 404s."""

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
    """Patched where the post-publish code looks the fetcher up."""
    with mock.patch.object(sources, "fetch", web), mock.patch.object(fetch_module, "fetch", web):
        yield web


@override_settings(FEATURE_FLAGS=FLAGS)
class HarvestPublishTests(TestCase):
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
class HarvestAdminTests(TestCase):
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

    @override_settings(FEATURE_FLAGS={**FLAGS, "AI": False})
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
class HarvestCommandTests(TestCase):
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
