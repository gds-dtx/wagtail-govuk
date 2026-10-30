"""Importing a catalogue into pages, exporting it again, and the commands that do both."""

import io
import tempfile
import zipfile
from io import StringIO
from pathlib import Path

import yaml
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings
from wagtail.models import Page, Site

from govuk.catalogue.bundle import GuidanceSyncError
from govuk.catalogue.catalogue import read_catalogue
from govuk.catalogue.export_catalogue import export_catalogue
from govuk.catalogue.import_catalogue import files_from_directory, files_from_zip
from govuk.models import (
    CatalogueProfession,
    CatalogueScope,
    CatalogueType,
    ContentEntryExternalPage,
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
from govuk.tests.catalogue_helpers import (
    FakeGitHub,
    catalogue_files,
    guidance_flags,
    import_fixture,
    make_content_main_page,
)


def _tree(main_page) -> list[tuple]:
    """Every page beneath ``main_page``: its path from there, type and title."""
    return [
        (page.url_path[len(main_page.url_path) :], type(page).__name__, page.title)
        for page in Page.objects.descendant_of(main_page).specific().order_by("path")
    ]


@override_settings(FEATURE_FLAGS=guidance_flags())
class ImportTests(TestCase):
    def setUp(self):
        self.root = Site.objects.get(is_default_site=True).root_page
        self.page = make_content_main_page(self.root)

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


@override_settings(FEATURE_FLAGS=guidance_flags())
class ExportTests(TestCase):
    def setUp(self):
        self.root = Site.objects.get(is_default_site=True).root_page
        self.page = make_content_main_page(self.root)
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
        from govuk.catalogue.import_catalogue import import_catalogue

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


@override_settings(FEATURE_FLAGS=guidance_flags())
class CommandTests(TestCase):
    def setUp(self):
        self.root = Site.objects.get(is_default_site=True).root_page
        self.page = make_content_main_page(self.root)
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
