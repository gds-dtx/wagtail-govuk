"""Reading a content catalogue: its entries, each layout's shape, and filtering.

No database and no network: the catalogue is ``catalogue_helpers``' in-memory
one.
"""

from types import SimpleNamespace
from unittest import mock

from django.http import QueryDict
from django.test import SimpleTestCase

from govuk.catalogue import fetch
from govuk.catalogue.adapters import build_bundle
from govuk.catalogue.bundle import GuidanceSyncError
from govuk.catalogue.catalogue import read_catalogue
from govuk.catalogue.filtering import filter_entries
from govuk.catalogue.layouts import policy_sections, shape
from govuk.catalogue.render import render_bundle
from govuk.catalogue.repository import RepositoryRef
from govuk.tests.catalogue_helpers import catalogue_files


def _hosted(path: str) -> dict:
    prefix = f"content/{path}/"
    return {
        name[len(prefix) :]: data
        for name, data in catalogue_files().items()
        if name.startswith(prefix)
    }


def _shaped(layout: str, path: str):
    rendered = render_bundle(build_bundle(_hosted(path)), RepositoryRef("example", "catalogue"))
    structure, documents = shape(layout, rendered)
    return structure, {document["path"]: document for document in documents}


class ReadCatalogueTests(SimpleTestCase):
    def setUp(self):
        self.catalogue = read_catalogue(catalogue_files())
        self.entries = {entry.id: entry for entry in self.catalogue.entries}

    def test_groups_and_types_come_from_the_vocabularies(self):
        vocabularies = self.catalogue.vocabularies
        self.assertEqual(
            list(vocabularies["groups"]),
            ["standards-and-policies", "guidance", "resources-and-tools"],
        )
        self.assertEqual(self.entries["hosted-policy"].group, "standards-and-policies")
        self.assertEqual(self.entries["hosted-method"].group, "guidance")
        self.assertEqual(self.entries["linked-tool"].layout, "tool")

    def test_blogs_and_drafts_are_left_out_without_a_warning(self):
        self.assertNotIn("a-blog", self.entries)
        self.assertNotIn("a-draft", self.entries)
        self.assertEqual(self.catalogue.unlisted, 2)

    def test_a_broken_entry_is_left_out_with_a_warning(self):
        self.assertNotIn("broken-entry", self.entries)
        [warning] = self.catalogue.warnings
        self.assertIn("broken-entry.yml: missing", warning)

    def test_which_entries_have_pages(self):
        with_pages = {entry.id for entry in self.catalogue.entries if entry.has_pages}
        self.assertEqual(
            with_pages,
            {"hosted-standard", "hosted-policy", "repo-guide", "hosted-method", "linked-tool"},
        )
        self.assertEqual(
            self.entries["external-policy"].external_url,
            "https://www.gov.uk/guidance/external-policy",
        )

    def test_entry_fields(self):
        standard = self.entries["hosted-standard"]
        self.assertTrue(standard.primary)
        self.assertEqual(standard.tags, ["cloud", "security"])
        # A related id that is not listed is dropped.
        self.assertEqual(standard.related, ["external-policy"])
        tool = self.entries["linked-tool"]
        self.assertEqual(tool.version, "v3")
        # Only https links are kept.
        self.assertEqual([link["title"] for link in tool.links], ["Use it"])

    def test_an_external_source_must_be_https(self):
        files = catalogue_files()
        files["entries/external-policy.yml"] = files["entries/external-policy.yml"].replace(
            b"https://www.gov.uk", b"http://www.gov.uk"
        )
        catalogue = read_catalogue(files)
        self.assertNotIn("external-policy", {entry.id for entry in catalogue.entries})
        self.assertTrue(any("https://" in warning for warning in catalogue.warnings))

    def test_a_hosted_source_needs_its_manifest(self):
        files = catalogue_files()
        del files["content/hosted-policy/guidance.yml"]
        catalogue = read_catalogue(files)
        self.assertNotIn("hosted-policy", {entry.id for entry in catalogue.entries})

    def test_the_catalogue_schema_is_required(self):
        files = catalogue_files()
        files["catalogue.yml"] = b"title: no schema\n"
        with self.assertRaises(GuidanceSyncError):
            read_catalogue(files)

    def test_a_type_naming_an_unknown_group_is_refused(self):
        files = catalogue_files()
        files["vocabularies.yml"] = files["vocabularies.yml"].replace(
            b"group: guidance}", b"group: nowhere}", 1
        )
        with self.assertRaises(GuidanceSyncError):
            read_catalogue(files)


class LayoutShapeTests(SimpleTestCase):
    def test_requirements_are_numbered_with_their_summaries(self):
        structure, documents = _shaped("requirements", "hosted-standard")
        self.assertEqual(structure["sequence"], ["first-point", "second-point"])
        self.assertEqual(documents["first-point"]["number"], 1)
        self.assertEqual(documents["second-point"]["number"], 2)
        self.assertEqual(documents["first-point"]["metadata"]["summary"], "Do the first thing.")
        self.assertIsNone(documents[""]["number"])

    def test_a_policy_is_split_into_numbered_sections(self):
        _, documents = _shaped("policy", "hosted-policy")
        index = documents[""]
        self.assertIn("Read this first.", index["intro_html"])
        self.assertEqual(
            [(s["number"], s["title"], s["anchor"]) for s in index["sections"]],
            [(1, "Policy statement", "policy-statement"), (2, "Scope", "scope")],
        )
        self.assertIn("<strong>must</strong>", index["sections"][0]["html"])
        self.assertIn("Devices", index["sections"][1]["html"])
        self.assertNotIn("<h2", index["sections"][0]["html"])
        self.assertEqual(index["metadata"]["version"], "2.1")
        self.assertEqual(index["metadata"]["effective_from"], "2026-04-01")
        self.assertEqual(index["metadata"]["next_review_on"], "2027-01-01")

    def test_steps_are_numbered_and_other_groups_are_reference_pages(self):
        structure, documents = _shaped("steps", "hosted-method")
        self.assertEqual(structure["sequence"], ["prepare", "run"])
        self.assertEqual(documents["run"]["number"], 2)
        self.assertEqual(
            [group["title"] for group in structure["navigation"]], ["Reference"]
        )
        self.assertIsNone(documents["glossary"]["number"])

    def test_steps_fall_back_to_the_first_navigation_group(self):
        files = _hosted("hosted-method")
        files["guidance.yml"] = (
            b"schema: guidance-as-code/v0\ntitle: x\nindex: index.md\nnavigation:\n"
            b"  - title: Steps\n    pages: [prepare.md, run.md]\n"
        )
        rendered = render_bundle(build_bundle(files), RepositoryRef("example", "catalogue"))
        structure, _ = shape("steps", rendered)
        self.assertEqual(structure["sequence"], ["prepare", "run"])
        self.assertEqual(structure["navigation"], [])

    def test_policy_sections_keep_text_before_the_first_heading(self):
        intro, sections = policy_sections('<p>Intro</p><h2 id="a">A</h2><p>One</p>')
        self.assertEqual(intro, "<p>Intro</p>")
        self.assertEqual(sections, [{"number": 1, "title": "A", "anchor": "a", "html": "<p>One</p>"}])


class FilterEntriesTests(SimpleTestCase):
    def setUp(self):
        catalogue = read_catalogue(catalogue_files())
        self.entries = catalogue.entries
        self.vocabularies = catalogue.vocabularies

    def _filter(self, query: str):
        visible, state = filter_entries(self.entries, QueryDict(query), self.vocabularies)
        return [entry.id for entry in visible], state

    def test_no_parameters_shows_everything(self):
        ids, state = self._filter("")
        self.assertEqual(len(ids), len(self.entries))
        self.assertFalse(state.active)

    def test_filters_narrow_and_combine(self):
        ids, _ = self._filter("tag=security")
        self.assertEqual(set(ids), {"hosted-standard", "hosted-policy"})
        ids, _ = self._filter("profession=architecture&scope=government")
        self.assertEqual(ids, ["external-standard"])
        # Two values of one filter: either matches.
        ids, _ = self._filter("type=method&type=product")
        self.assertEqual(set(ids), {"hosted-method", "linked-tool"})

    def test_options_list_only_values_in_use_with_counts(self):
        _, state = self._filter("tag=security")
        tags = {option["value"]: option for option in state.options["tag"]}
        self.assertEqual(tags["security"]["count"], 2)
        self.assertTrue(tags["security"]["selected"])
        self.assertEqual(tags["cloud"]["label"], "Cloud")

    def test_each_options_count_allows_for_the_other_filters(self):
        _, state = self._filter("profession=architecture")
        tags = {option["value"]: option["count"] for option in state.options["tag"]}
        # Only the architecture entry is counted for each topic...
        self.assertEqual(tags, {"cloud": 1})
        # ...but a filter's own options still count every entry, so another
        # profession can be chosen instead.
        professions = {o["value"]: o["count"] for o in state.options["profession"]}
        self.assertEqual(professions["engineering"], 6)

    def test_a_chosen_value_stays_listed_when_nothing_else_matches(self):
        _, state = self._filter("profession=architecture&tag=security")
        tags = {option["value"]: option for option in state.options["tag"]}
        self.assertTrue(tags["security"]["selected"])
        self.assertEqual(tags["security"]["count"], 0)

    def test_search_ranks_title_matches_first_and_drops_non_matches(self):
        ids, state = self._filter("q=another+standard")
        self.assertTrue(state.active)
        self.assertEqual(ids[0], "external-standard")
        ids, _ = self._filter("q=zzzz")
        self.assertEqual(ids, [])


class FetchGuardTests(SimpleTestCase):
    def test_only_https_to_github_is_fetched(self):
        for url in (
            "http://api.github.com/repos/a/b",
            "https://example.com/archive.tar.gz",
            "file:///etc/passwd",
            "https://api.github.com.evil.example/",
        ):
            with self.subTest(url=url), self.assertRaisesMessage(GuidanceSyncError, "only GitHub"):
                fetch._get(url, accept="*/*", timeout=1, max_bytes=10)

    def test_github_requests_still_go_through_the_content_discovery_guards(self):
        with mock.patch.object(
            fetch, "_validate_source_fetch_dns_resolution", side_effect=fetch.ContentDiscoveryError("blocked")
        ), mock.patch.object(fetch, "_validate_source_fetch_url"):
            with self.assertRaisesMessage(GuidanceSyncError, "blocked"):
                fetch._get("https://api.github.com/repos/a/b", accept="*/*", timeout=1, max_bytes=10)

    def test_a_pinned_ref_is_resolved_instead_of_the_branch(self):
        ref = RepositoryRef.parse("alphagov/gds-way", branch="main", pin="v1.2")
        self.assertTrue(ref.commit_api_url.endswith("/commits/v1.2"))
        self.assertEqual(ref.source_key, "alphagov/gds-way@v1.2:")

    def test_repository_forms(self):
        self.assertEqual(RepositoryRef.parse("alphagov/gds-way").name, "gds-way")
        self.assertEqual(RepositoryRef.parse("https://github.com/alphagov/gds-way").owner, "alphagov")
        for bad in ("gds-way", "https://gitlab.com/a/b", "a/b/c"):
            with self.subTest(value=bad), self.assertRaises(ValueError):
                RepositoryRef.parse(bad)
        with self.assertRaises(ValueError):
            RepositoryRef.parse("a/b", subdirectory="../escape")


class DetailsRenderingTests(SimpleTestCase):
    def test_details_become_the_govuk_component(self):
        from govuk.catalogue.render import render_markdown, style_html

        html, _ = style_html(
            render_markdown("<details>\n<summary>Why</summary>\n\nBecause.\n\n</details>\n"),
            resolver=None, base_url_path="/", source_name="test",
        )
        self.assertIn('<details class="govuk-details">', html)
        self.assertIn(
            '<summary class="govuk-details__summary"><span class="govuk-details__summary-text">Why</span></summary>',
            html,
        )
        self.assertIn('<div class="govuk-details__text">', html)
        self.assertIn("Because.", html)


class CardTests(SimpleTestCase):
    def test_external_cards_link_out_and_say_where(self):
        from govuk.catalogue.context import card

        entry = SimpleNamespace(
            title="X", has_pages=False, external_url="https://www.gov.uk/x",
            source={"name": "GOV.UK"}, short_description="d", type="policy",
            scope="government", tags=["cloud"], primary=True, status="current",
        )
        vocab = read_catalogue(catalogue_files()).vocabularies
        result = card(None, None, entry, vocab)
        self.assertEqual(result["url"], "https://www.gov.uk/x")
        self.assertTrue(result["external"])
        self.assertEqual(result["source_name"], "GOV.UK")
        self.assertEqual((result["type"], result["scope"], result["tags"]), ("Policy", "Central government", ["Cloud"]))
