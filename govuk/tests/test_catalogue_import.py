"""Importing a catalogue into pages, exporting it again, the commands that do both, and the Markdown↔blocks conversion."""

import io
import tempfile
import zipfile
from io import StringIO
from pathlib import Path

import yaml
from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, TestCase, override_settings
from wagtail.models import Page, Site

from govuk.catalogue.bundle import GuidanceSyncError
from govuk.catalogue.catalogue import read_catalogue
from govuk.catalogue.export_catalogue import export_catalogue
from govuk.catalogue.import_catalogue import (
    files_from_directory,
    files_from_zip,
    import_catalogue,
)
from govuk.catalogue.markdown_blocks import (
    blocks_to_markdown,
    markdown_to_blocks,
    split_sections,
)
from govuk.catalogue.presentation import _contents, add_heading_anchors
from govuk.models import (
    CatalogueProfession,
    CatalogueScope,
    CatalogueType,
    ContentEntryExternalPage,
    ContentEntryGenericPage,
    ContentEntryGuidePage,
    ContentEntryPolicyPage,
    ContentEntryRequirementsPage,
    ContentEntryStepsPage,
    ContentEntryToolPage,
    ContentGroupPage,
    ContentGuidePage,
    ContentPage,
    ContentRequirementPage,
    ContentStepPage,
    GovukTag,
)
from govuk.models.catalogue.blocks import CatalogueBodyBlock
from govuk.tests.catalogue_helpers import (
    FakeGitHub,
    _dedent,
    _entry,
    catalogue_files,
    catalogue_flags,
    import_fixture,
    make_content_main_page,
)


def _tree(main_page) -> list[tuple]:
    """Every page beneath ``main_page``: its path from there, type and title."""
    return [
        (page.url_path[len(main_page.url_path) :], type(page).__name__, page.title)
        for page in Page.objects.descendant_of(main_page).specific().order_by("path")
    ]


class CatalogueImportTestCase(TestCase):
    def setUp(self):
        self.root = Site.objects.get(is_default_site=True).root_page
        self.page = make_content_main_page(self.root)


@override_settings(FEATURE_FLAGS=catalogue_flags())
class ImportTests(CatalogueImportTestCase):

    def test_vocabularies_become_snippets_and_tags(self):
        import_fixture(self.page)
        self.assertEqual(
            list(CatalogueType.objects.values_list("key", "layout")),
            [
                ("standard", "requirements"), ("policy", "policy"), ("guidance", "guide"),
                ("method", "steps"), ("product", "tool"), ("blog", "media"),
            ],
        )
        self.assertEqual(CatalogueType.objects.get(key="standard").heading, "Standards")
        self.assertEqual(CatalogueType.objects.get(key="policy").colour, "#8f338e")
        self.assertEqual(CatalogueType.objects.get(key="standard").colour, "#1d70b8")
        self.assertEqual(CatalogueScope.objects.count(), 2)
        self.assertEqual(CatalogueProfession.objects.count(), 2)
        self.assertTrue(GovukTag.objects.filter(slug="security", name="Security").exists())

    def test_groups_entries_and_their_pages_are_made_in_order(self):
        result = import_fixture(self.page)
        # Groups in the vocabulary's order, entries in their files' order, and
        # an entry's own pages in the order the catalogue numbers or lists them.
        self.assertEqual(
            [(path, kind) for path, kind, _ in _tree(self.page)],
            [
                ("standards-and-policies/", "ContentGroupPage"),
                ("standards-and-policies/external-policy/", "ContentEntryExternalPage"),
                ("standards-and-policies/external-standard/", "ContentEntryExternalPage"),
                ("standards-and-policies/hosted-policy/", "ContentEntryPolicyPage"),
                ("standards-and-policies/hosted-standard/", "ContentEntryRequirementsPage"),
                ("standards-and-policies/hosted-standard/first-point/", "ContentRequirementPage"),
                ("standards-and-policies/hosted-standard/second-point/", "ContentRequirementPage"),
                ("guidance/", "ContentGroupPage"),
                ("guidance/hosted-method/", "ContentEntryStepsPage"),
                ("guidance/hosted-method/prepare/", "ContentStepPage"),
                ("guidance/hosted-method/run/", "ContentStepPage"),
                ("guidance/hosted-method/glossary/", "ContentGuidePage"),
                ("guidance/repo-guide/", "ContentEntryGuidePage"),
                ("guidance/repo-guide/alerting/", "ContentGuidePage"),
                ("guidance/repo-guide/alerting/escalation/", "ContentGuidePage"),
                ("resources-and-tools/", "ContentGroupPage"),
                ("resources-and-tools/linked-tool/", "ContentEntryToolPage"),
            ],
        )
        self.assertEqual(result.created, 17)
        # The blog (no group), the draft and the broken entry are left out.
        self.assertTrue(any("broken-entry" in w for w in result.warnings))
        self.assertFalse(Page.objects.filter(slug__in=["a-blog", "a-draft"]).exists())

    def test_an_entrys_metadata_comes_from_its_record(self):
        import_fixture(self.page)
        standard = ContentEntryRequirementsPage.objects.get(slug="hosted-standard")
        self.assertTrue(standard.primary)
        self.assertEqual(standard.entry_type.key, "standard")
        self.assertEqual(standard.scope.key, "public-sector")
        self.assertEqual(sorted(t.slug for t in standard.topics.all()), ["cloud", "security"])
        self.assertEqual([p.key for p in standard.professions.all()], ["engineering"])
        self.assertEqual(standard.owner_organisation, "Example Department")
        self.assertEqual(standard.source_url, "")
        external = ContentEntryExternalPage.objects.get(slug="external-policy")
        self.assertEqual(external.source_name, "GOV.UK")
        self.assertEqual(external.source_url, "https://www.gov.uk/guidance/external-policy")
        tool = ContentEntryToolPage.objects.get(slug="linked-tool")
        self.assertEqual(tool.version, "v3")
        self.assertEqual([link.value["title"] for link in tool.links], ["Use it"])

    def test_markdown_becomes_blocks(self):
        import_fixture(self.page)
        standard = ContentEntryRequirementsPage.objects.get(slug="hosted-standard")
        # Before the first section is the introduction; the rest follows the points.
        self.assertEqual(standard.body.raw_data[0]["value"], "<p>The introduction.</p>")
        self.assertIn("<h2>Contacts</h2>", standard.closing_body.raw_data[0]["value"])
        point = ContentRequirementPage.objects.get(slug="first-point")
        self.assertEqual(point.summary, "Do the first thing.")
        self.assertEqual([b.block_type for b in point.body], ["text", "raw_html"])
        policy = ContentEntryPolicyPage.objects.get(slug="hosted-policy")
        self.assertEqual([s.value["title"] for s in policy.sections], ["Policy statement", "Scope"])
        self.assertEqual(policy.version, "2.1")
        self.assertEqual(str(policy.effective_from), "2026-04-01")
        self.assertEqual(str(policy.next_review_on), "2027-01-01")

    def test_links_between_an_entrys_pages_become_page_links(self):
        import_fixture(self.page)
        first = ContentRequirementPage.objects.get(slug="first-point")
        closing = ContentEntryRequirementsPage.objects.get(slug="hosted-standard").closing_body
        self.assertIn(f'<a id="{first.pk}" linktype="page">first point</a>', closing.raw_data[0]["value"])
        alerting = ContentGuidePage.objects.get(slug="alerting")
        guide = ContentEntryGuidePage.objects.get(slug="repo-guide")
        self.assertIn(f'id="{alerting.pk}"', guide.body.raw_data[0]["value"])

    def test_a_link_to_a_heading_keeps_its_address(self):
        files = catalogue_files()
        files["content/hosted-standard/second-point.md"] += b"\nSee [why](first-point.md#why).\n"
        import_fixture(self.page, files)
        first = ContentRequirementPage.objects.get(slug="first-point")
        second = ContentRequirementPage.objects.get(slug="second-point")
        self.assertIn(f'href="{first.url}#why"', second.body.raw_data[0]["value"])

    def test_related_entries_are_linked(self):
        import_fixture(self.page)
        standard = ContentEntryRequirementsPage.objects.get(slug="hosted-standard")
        # Its record also names an entry that does not exist, which is dropped.
        self.assertEqual([b.value.slug for b in standard.related_entries], ["external-policy"])

    def test_importing_again_leaves_the_pages_as_they_are(self):
        import_fixture(self.page)
        standard = ContentEntryRequirementsPage.objects.get(slug="hosted-standard")
        standard.title = "Edited"
        standard.save_revision().publish()
        result = import_fixture(self.page)
        self.assertEqual((result.created, result.updated), (0, 0))
        self.assertEqual(ContentEntryRequirementsPage.objects.get(pk=standard.pk).title, "Edited")

    def test_update_rewrites_them_from_the_source(self):
        import_fixture(self.page)
        before = _tree(self.page)
        standard = ContentEntryRequirementsPage.objects.get(slug="hosted-standard")
        standard.title = "Edited"
        standard.save_revision().publish()
        result = import_fixture(self.page, update_existing=True)
        self.assertEqual(result.created, 0)
        self.assertEqual(_tree(self.page), before)
        self.assertEqual(ContentEntryRequirementsPage.objects.get(pk=standard.pk).title, "Hosted standard")

    def test_draft_saves_drafts(self):
        import_fixture(self.page, publish=False)
        entry = ContentEntryRequirementsPage.objects.get(slug="hosted-standard")
        self.assertFalse(entry.live)
        self.assertTrue(entry.has_unpublished_changes)
        self.assertEqual(entry.get_latest_revision_as_object().short_description, "Short description of hosted-standard")
        self.assertFalse(ContentRequirementPage.objects.live().exists())

    def test_a_page_of_another_type_is_reported_and_left_alone(self):
        group = self.page.add_child(
            instance=ContentGroupPage(title="Standards and policies", slug="standards-and-policies")
        )
        # A content page cannot go in a group, so build it as one would be
        # by an older import: a guide where the standard should be.
        import_fixture(self.page)
        standard = ContentEntryRequirementsPage.objects.get(slug="hosted-standard")
        standard.delete()
        CatalogueType.objects.filter(key="guidance").update(layout="guide")
        guide = ContentEntryGuidePage(
            title="Squatter", slug="hosted-standard", entry_type=CatalogueType.objects.get(key="guidance"),
            scope=CatalogueScope.objects.first(), short_description="x", long_description="x",
            owner_organisation="x",
        )
        group.add_child(instance=guide)
        result = import_fixture(self.page, update_existing=True)
        self.assertTrue(any("hosted-standard" in w and "left as it is" in w for w in result.warnings))
        self.assertEqual(ContentEntryGuidePage.objects.get(pk=guide.pk).title, "Squatter")
        # The rest still imported.
        self.assertTrue(ContentEntryPolicyPage.objects.filter(slug="hosted-policy").exists())

    def test_a_repo_that_cannot_be_read_is_reported_and_the_rest_import(self):
        github = FakeGitHub()
        github.failing.add("example/guide-repo")
        result = import_fixture(self.page, github=github)
        self.assertTrue(any("repo-guide" in w and "unavailable" in w for w in result.warnings))
        self.assertFalse(ContentEntryGuidePage.objects.exists())
        self.assertTrue(ContentEntryStepsPage.objects.exists())
        self.assertEqual(result.created, 14)

    def test_hosted_images_are_served_from_the_catalogue_repo(self):
        files = catalogue_files()
        files["content/hosted-method/run.md"] += b"\n![A chart](images/chart.png)\n"
        files["content/hosted-method/images/chart.png"] = None
        import_fixture(self.page, files)
        run = ContentStepPage.objects.get(slug="run")
        self.assertIn(
            "https://raw.githubusercontent.com/example/catalogue/" + "a" * 40
            + "/content/hosted-method/images/chart.png",
            str(run.body.raw_data),
        )


class ZipTests(TestCase):
    def _zip(self, names):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name in names:
                archive.writestr(name, "x")
        return buffer.getvalue()

    def test_a_single_top_folder_is_dropped(self):
        files = files_from_zip(self._zip(["repo-main/catalogue.yml", "repo-main/entries/a.yml"]))
        self.assertEqual(sorted(files), ["catalogue.yml", "entries/a.yml"])

    def test_hidden_files_are_skipped_and_binaries_listed_by_name(self):
        files = files_from_zip(self._zip(["catalogue.yml", ".git/config", "content/a/b.png"]))
        self.assertEqual(files, {"catalogue.yml": b"x", "content/a/b.png": None})

    def test_paths_outside_the_folder_are_refused(self):
        with self.assertRaises(GuidanceSyncError):
            files_from_zip(self._zip(["../evil.yml"]))

    def test_too_big_or_not_a_zip_is_refused(self):
        with self.assertRaises(GuidanceSyncError):
            files_from_zip(b"not a zip")
        with self.assertRaises(GuidanceSyncError):
            files_from_zip(self._zip(["a.yml"]), max_bytes=0)


@override_settings(FEATURE_FLAGS=catalogue_flags())
class ExportTests(CatalogueImportTestCase):
    def setUp(self):
        super().setUp()
        import_fixture(self.page)
        self.result = export_catalogue(self.page)
        self.files = self.result.files

    def _yaml(self, name):
        return yaml.safe_load(self.files[name])

    def test_it_writes_the_catalogue_format(self):
        self.assertEqual(self._yaml("catalogue.yml")["schema"], "guidance-catalogue/v0")
        vocab = self._yaml("vocabularies.yml")
        self.assertEqual(list(vocab["groups"]), ["standards-and-policies", "guidance", "resources-and-tools"])
        self.assertEqual(vocab["types"]["standard"]["group"], "standards-and-policies")
        # A type with no entries has no group: it is not listed on import.
        self.assertNotIn("group", vocab["types"]["blog"])
        self.assertEqual(vocab["types"]["policy"]["colour"], "#8f338e")
        self.assertEqual(self.result.entries, 7)

    def test_an_entry_file_holds_its_record(self):
        entry = self._yaml("entries/hosted-standard.yml")
        self.assertEqual(entry["id"], "hosted-standard")
        self.assertTrue(entry["primary"])
        self.assertEqual(entry["tags"], ["cloud", "security"])
        self.assertEqual(entry["related"], ["external-policy"])
        self.assertEqual(entry["source"]["type"], "hosted")
        self.assertEqual(entry["source"]["path"], "content/hosted-standard")
        external = self._yaml("entries/external-policy.yml")
        self.assertEqual(
            external["source"],
            {"type": "external", "name": "GOV.UK", "path": "https://www.gov.uk/guidance/external-policy"},
        )
        tool = self._yaml("entries/linked-tool.yml")
        self.assertEqual(tool["version"], "v3")
        self.assertEqual(tool["links"], [{"title": "Use it", "url": "https://tool.example.gov.uk/", "description": "Sign in."}])

    def test_content_comes_back_as_markdown(self):
        manifest = self._yaml("content/hosted-standard/guidance.yml")
        self.assertEqual(manifest["requirements"], ["first-point.md", "second-point.md"])
        index = self.files["content/hosted-standard/index.md"]
        self.assertIn("The introduction.", index)
        self.assertIn("See the [first point](first-point.md).", index)
        self.assertIn("summary: Do the first thing.", self.files["content/hosted-standard/first-point.md"])
        guide = self._yaml("content/repo-guide/guidance.yml")
        self.assertEqual(
            guide["navigation"], [{"title": "Pages", "pages": [{"path": "alerting.md", "pages": ["alerting/escalation.md"]}]}]
        )
        policy = self.files["content/hosted-policy/index.md"]
        self.assertIn('version: "2.1"', policy.replace("'", '"'))
        self.assertIn("review_in: 12 months", policy)
        self.assertIn("## Scope", policy)

    def test_the_export_reads_as_a_catalogue(self):
        files = {name: text.encode() for name, text in self.files.items()}
        catalogue = read_catalogue(files)
        self.assertEqual(catalogue.warnings, [])
        self.assertEqual(len(catalogue.entries), 7)

    def test_importing_the_export_makes_the_same_tree(self):
        other = make_content_main_page(self.root, title="Other", slug="other")
        files = {name: text.encode() for name, text in self.files.items()}
        result = import_catalogue(other, files)
        self.assertEqual(result.warnings, [])
        self.assertEqual(_tree(other), _tree(self.page))

    def test_an_unpublished_entry_is_exported_as_a_draft(self):
        ContentEntryExternalPage.objects.get(slug="external-standard").unpublish()
        entry = yaml.safe_load(export_catalogue(self.page).files["entries/external-standard.yml"])
        self.assertEqual(entry["status"], "draft")

    def test_a_block_with_no_markdown_form_is_written_as_html(self):
        steps = ContentEntryStepsPage.objects.get(slug="hosted-method")
        steps.body = [
            *steps.body.raw_data,
            {"type": "banner", "value": {"heading": "Banner heading", "style": "light", "text": "<p>Hi</p>"}},
        ]
        steps.save_revision().publish()
        result = export_catalogue(self.page)
        self.assertIn("Banner heading", result.files["content/hosted-method/index.md"])
        self.assertTrue(any("banner block" in w for w in result.warnings))


@override_settings(FEATURE_FLAGS=catalogue_flags())
class CommandTests(CatalogueImportTestCase):
    def setUp(self):
        super().setUp()
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        # A folder catalogue with no repo entries, so nothing needs GitHub.
        for name, data in catalogue_files().items():
            if name == "entries/repo-guide.yml":
                continue
            path = Path(self.folder.name) / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data or b"")

    def test_import_from_a_folder_then_export(self):
        out = StringIO()
        call_command("import_content_catalogue", str(self.page.pk), self.folder.name, stdout=out)
        self.assertIn("14 pages created", out.getvalue())
        self.assertTrue(ContentEntryRequirementsPage.objects.filter(slug="hosted-standard").exists())

        with tempfile.TemporaryDirectory() as export_dir:
            out = StringIO()
            call_command("export_content_catalogue", str(self.page.pk), export_dir, stdout=out)
            self.assertIn("Exported 6 entries", out.getvalue())
            files = files_from_directory(export_dir)
            self.assertIn("content/hosted-standard/guidance.yml", files)
            with self.assertRaises(CommandError):
                call_command("export_content_catalogue", str(self.page.pk), export_dir, stdout=StringIO())

    def test_draft_and_update(self):
        call_command("import_content_catalogue", str(self.page.pk), self.folder.name, "--draft", stdout=StringIO())
        self.assertFalse(ContentEntryRequirementsPage.objects.get(slug="hosted-standard").live)
        out = StringIO()
        call_command("import_content_catalogue", str(self.page.pk), self.folder.name, "--update", stdout=out)
        self.assertIn("0 pages created", out.getvalue())
        self.assertTrue(ContentEntryRequirementsPage.objects.get(slug="hosted-standard").live)

    def test_an_unknown_main_page_or_repository_is_an_error(self):
        with self.assertRaises(CommandError):
            call_command("import_content_catalogue", "999999", self.folder.name, stdout=StringIO())
        with self.assertRaises(CommandError):
            call_command("import_content_catalogue", str(self.page.pk), "not a repo", stdout=StringIO())
        content = self.root.add_child(instance=ContentPage(title="Plain", slug="plain"))
        with self.assertRaises(CommandError):
            call_command("export_content_catalogue", str(content.pk), self.folder.name, stdout=StringIO())

    def test_the_groups_are_pages_under_the_main_page(self):
        call_command("import_content_catalogue", str(self.page.pk), self.folder.name, stdout=StringIO())
        self.assertEqual(
            list(ContentGroupPage.objects.child_of(self.page).values_list("slug", flat=True)),
            ["standards-and-policies", "guidance", "resources-and-tools"],
        )


def _generic_files():
    """The fixture catalogue, with a product laid out generically, held here."""
    files = catalogue_files()
    files["entries/a-model.yml"] = _entry(
        "a-model",
        type="product",
        layout="generic",
        source="\n  type: hosted\n  name: tech.gov.uk\n  path: content/a-model",
    )
    files["content/a-model/guidance.yml"] = _dedent(
        """
        schema: guidance-as-code/v0
        title: A model
        content_dir: .
        index: index.md
        """
    )
    files["content/a-model/index.md"] = _dedent(
        """
        ---
        title: A model
        ---

        # A model

        Everything in one place.

        > [!WARNING]
        > Some of it is old.
        """
    )
    files["content/a-model/stray.md"] = b"# Stray\n"
    return files


@override_settings(FEATURE_FLAGS=catalogue_flags())
class GenericImportExportTests(CatalogueImportTestCase):
    """An entry whose own layout is generic: one page, any type."""

    def setUp(self):
        super().setUp()
        self.result = import_fixture(self.page, _generic_files())

    def test_an_entrys_own_layout_makes_it_a_generic_page(self):
        page = ContentEntryGenericPage.objects.get(slug="a-model")
        self.assertEqual(page.entry_type.key, "product")
        self.assertEqual(page.get_parent().slug, "resources-and-tools")
        self.assertEqual([b.block_type for b in page.body], ["text", "warning_text"])
        self.assertFalse(page.get_children().exists())
        self.assertIn("a-model: a generic entry is one page; left out stray.md", self.result.warnings)

    def test_it_exports_with_its_layout_and_imports_again(self):
        exported = export_catalogue(self.page)
        self.assertIn("layout: generic", exported.files["entries/a-model.yml"])
        self.assertNotIn("layout: generic", exported.files["entries/hosted-standard.yml"])
        self.assertIn("Everything in one place.", exported.files["content/a-model/index.md"])
        other = make_content_main_page(self.root, title="Other", slug="other")
        result = import_catalogue(other, {name: text.encode() for name, text in exported.files.items()})
        self.assertEqual(result.warnings, [])
        again = ContentEntryGenericPage.objects.child_of(
            ContentGroupPage.objects.child_of(other).get(slug="resources-and-tools")
        ).get()
        self.assertEqual(again.body.raw_data[0]["value"], "<p>Everything in one place.</p>")


class GenericLayoutReadingTests(TestCase):
    def _warnings(self, **fields):
        files = catalogue_files()
        files["entries/odd.yml"] = _entry("odd", **fields)
        return read_catalogue(files).warnings

    def test_only_generic_and_only_with_a_page(self):
        external = "\n  type: external\n  name: GOV.UK\n  path: https://www.gov.uk/odd"
        self.assertTrue(any(
            "odd" in w and "external entry has no page" in w
            for w in self._warnings(type="guidance", layout="generic", source=external)
        ))
        self.assertTrue(any(
            "odd" in w and "layout 'guide' must be generic" in w
            for w in self._warnings(type="guidance", layout="guide", source=external)
        ))


def _types(blocks):
    return [block["type"] for block in blocks]


class MarkdownToBlocksTests(SimpleTestCase):
    def test_prose_is_one_text_block_without_the_title(self):
        blocks = markdown_to_blocks(
            "# Title\n\nSome **bold**, _italic_ and `code`.\n\n## Part\n\n- one\n- two\n"
        )
        self.assertEqual(_types(blocks), ["text"])
        self.assertEqual(
            blocks[0]["value"],
            "<p>Some <b>bold</b>, <i>italic</i> and <code>code</code>.</p><h2>Part</h2>"
            "<ul>\n<li>one</li>\n<li>two</li>\n</ul>",
        )

    def test_a_later_h1_and_h5_fit_the_outline(self):
        value = markdown_to_blocks("# Title\n\n# Again\n\n##### Small\n")[0]["value"]
        self.assertIn("<h2>Again</h2>", value)
        self.assertIn("<h4>Small</h4>", value)

    def test_a_comment_is_left_out(self):
        self.assertEqual(
            markdown_to_blocks("<!-- copied on 2026-10-27 -->\n\n# Title\n\nHello\n"),
            [{"type": "text", "value": "<p>Hello</p>"}],
        )

    def test_alerts_and_quotes_become_inset_and_warning_text(self):
        blocks = markdown_to_blocks(
            "> [!NOTE]\n> A note.\n\n> [!TIP]\n> A tip.\n\n> A quote.\n\n"
            "> [!WARNING]\n> Careful.\n>\n> Really.\n\n> [!CAUTION]\n> Mind.\n"
        )
        self.assertEqual(
            _types(blocks), ["inset_text", "inset_text", "inset_text", "warning_text", "warning_text"]
        )
        self.assertEqual(blocks[0]["value"], "<p>A note.</p>")
        self.assertEqual(blocks[3]["value"], "<p>Careful.<br/>Really.</p>")

    def test_code_keeps_its_language_and_text(self):
        blocks = markdown_to_blocks("```python\nif a < b:\n    pass\n```\n")
        self.assertEqual(
            blocks, [{"type": "code", "value": {"language": "python", "code": "if a < b:\n    pass"}}]
        )

    def test_tables_are_table_blocks_unless_their_cells_hold_more_than_text(self):
        plain = markdown_to_blocks("| A | B |\n| --- | --- |\n| 1 | 2 |\n")[0]
        self.assertEqual(plain["type"], "table")
        self.assertEqual(plain["value"]["table"]["data"], [["A", "B"], ["1", "2"]])
        self.assertTrue(plain["value"]["table"]["first_row_is_table_header"])
        linked = markdown_to_blocks("| A |\n| --- |\n| [x](https://example.gov.uk) |\n")[0]
        self.assertEqual(linked["type"], "raw_html")
        self.assertIn('class="govuk-table"', linked["value"])
        self.assertIn('class="govuk-link"', linked["value"])

    def test_an_empty_header_row_means_no_header(self):
        table = markdown_to_blocks("|  |  |\n| --- | --- |\n| 1 | 2 |\n")[0]["value"]["table"]
        self.assertEqual(table["data"], [["1", "2"]])
        self.assertFalse(table["first_row_is_table_header"])

    def test_details_become_a_details_block(self):
        blocks = markdown_to_blocks(
            "<details>\n<summary>More</summary>\n\nHidden **text**.\n\n</details>\n"
        )
        self.assertEqual(
            blocks, [{"type": "details", "value": {"summary": "More", "text": "<p>Hidden <b>text</b>.</p>"}}]
        )

    def test_what_rich_text_cannot_hold_is_kept_as_html(self):
        blocks = markdown_to_blocks(
            "Text.\n\n![A chart](https://example.gov.uk/c.png)\n\n"
            '<p><svg width="10"><path d="m0 0"></path></svg></p>\n\nE = mc<sup>2</sup>\n'
        )
        self.assertEqual(_types(blocks), ["text", "raw_html", "raw_html", "raw_html"])
        self.assertIn('src="https://example.gov.uk/c.png"', blocks[1]["value"])
        self.assertIn("<svg", blocks[2]["value"])

    def test_plain_inline_tags_are_simplified_into_rich_text(self):
        blocks = markdown_to_blocks(
            'Use <abbr title="Long term support">LTS</abbr> and press <kbd>Enter</kbd>.\n'
        )
        self.assertEqual(blocks, [{"type": "text", "value": "<p>Use LTS and press <code>Enter</code>.</p>"}])

    def test_unsafe_html_is_cleaned(self):
        blocks = markdown_to_blocks('<div onclick="x()">Hi<script>alert(1)</script></div>\n')
        self.assertNotIn("script", str(blocks))
        self.assertNotIn("onclick", str(blocks))

    def test_links_to_pages_become_page_links_or_addresses(self):
        def target(href):
            return {"a.md": (5, ""), "b.md#part": (6, "/b/#part"), "file.txt": (None, "https://x/file.txt")}.get(href)

        value = markdown_to_blocks(
            "[A](a.md), [B](b.md#part), [file](file.txt), [here](#top) and [out](https://gov.uk).\n",
            link_target=target,
        )[0]["value"]
        self.assertIn('<a id="5" linktype="page">A</a>', value)
        self.assertIn('<a href="/b/#part">B</a>', value)
        self.assertIn('<a href="https://x/file.txt">file</a>', value)
        self.assertIn('<a href="#top">here</a>', value)
        self.assertIn('<a href="https://gov.uk">out</a>', value)

    def test_relative_images_are_given_their_address(self):
        blocks = markdown_to_blocks("![x](images/a.png)\n", image_src=lambda src: f"https://raw/{src}")
        self.assertIn('src="https://raw/images/a.png"', blocks[0]["value"])

    def test_a_policy_splits_at_each_section(self):
        intro, sections = split_sections("Before.\n\n## One\n\nFirst.\n\n### Sub\n\nMore.\n\n## Two ##\n\nSecond.\n")
        self.assertEqual(intro, [{"type": "text", "value": "<p>Before.</p>"}])
        self.assertEqual([s["title"] for s in sections], ["One", "Two"])
        self.assertIn("<h3>Sub</h3>", sections[0]["body"][0]["value"])

    def test_the_blocks_are_valid_for_the_stream(self):
        stream = CatalogueBodyBlock()
        value = stream.to_python(
            markdown_to_blocks(
                "Text.\n\n> [!WARNING]\n> Careful.\n\n```\ncode\n```\n\n| A |\n| --- |\n| 1 |\n\n"
                "<details><summary>S</summary>\n\nT\n\n</details>\n"
            )
        )
        stream.clean(value)


class BlocksToMarkdownTests(SimpleTestCase):
    def test_each_block_has_its_markdown_form(self):
        markdown = blocks_to_markdown(
            [
                {"type": "text", "value": '<h2>Part</h2><p>Some <b>bold</b> and <a href="https://gov.uk">a link</a>.</p><ul><li>one</li></ul>'},
                {"type": "inset_text", "value": "<p>A note.</p>"},
                {"type": "warning_text", "value": "<p>Careful.</p>"},
                {"type": "code", "value": {"language": "yaml", "code": "a: 1"}},
                {"type": "details", "value": {"summary": "More", "text": "<p>Hidden.</p>"}},
                {"type": "table", "value": {"table": {"data": [["A", "B"], ["1", "2|3"]], "first_row_is_table_header": True}}},
                {"type": "raw_html", "value": "<p><sup>2</sup></p>"},
            ]
        )
        self.assertEqual(
            markdown,
            "## Part\n\nSome **bold** and [a link](https://gov.uk).\n\n- one\n\n"
            "> [!NOTE]\n> A note.\n\n> [!WARNING]\n> Careful.\n\n```yaml\na: 1\n```\n\n"
            "<details>\n<summary>More</summary>\n\nHidden.\n\n</details>\n\n"
            "| A | B |\n| --- | --- |\n| 1 | 2\\|3 |\n\n<p><sup>2</sup></p>\n",
        )

    def test_page_links_are_written_by_the_caller(self):
        markdown = blocks_to_markdown(
            [{"type": "text", "value": '<p><a linktype="page" id="5">A</a> and <abbr>X</abbr></p>'}],
            link_href=lambda attrs: "a.md" if attrs.get("id") == "5" else None,
        )
        self.assertEqual(markdown, "[A](a.md) and <abbr>X</abbr>\n")

    def test_markdown_survives_the_round_trip(self):
        source = (
            "Some **bold** text with `code`.\n\n## Part\n\n1. first\n2. second\n\n"
            "> [!WARNING]\n> Careful.\n\n```python\nprint(1)\n```\n"
        )
        once = markdown_to_blocks(source)
        self.assertEqual(markdown_to_blocks(blocks_to_markdown(once)), once)


class BlockRenderingTests(SimpleTestCase):
    def render(self, data):
        stream = CatalogueBodyBlock()
        return stream.render(stream.to_python(data))

    def test_warning_text_details_and_code_render_as_govuk_components(self):
        html = self.render(
            [
                {"type": "warning_text", "value": "<p>Careful.</p>"},
                {"type": "details", "value": {"summary": "More", "text": "<p>Hidden.</p>"}},
                {"type": "code", "value": {"language": "python", "code": "a < b"}},
            ]
        )
        self.assertIn('class="govuk-warning-text"', html)
        self.assertIn("Careful.", html)
        self.assertIn('class="govuk-details"', html)
        self.assertIn('<span class="govuk-details__summary-text">', html)
        self.assertIn('class="app-guidance__code"', html)
        self.assertIn("a &lt; b", html)

    def test_headings_get_github_anchors_and_an_outline(self):
        html, headings = add_heading_anchors(
            "<h2>Why it matters</h2><h3>Detail</h3><h2>Why it matters</h2><h4>Deep</h4>"
        )
        self.assertIn('<h2 id="why-it-matters">', html)
        self.assertIn('<h2 id="why-it-matters-1">', html)
        self.assertIn('<h4 id="deep">', html)
        self.assertEqual(
            _contents(headings),
            [
                {"text": "Why it matters", "anchor": "why-it-matters", "children": [
                    {"text": "Detail", "anchor": "detail", "children": []},
                ]},
                {"text": "Why it matters", "anchor": "why-it-matters-1", "children": []},
            ],
        )
