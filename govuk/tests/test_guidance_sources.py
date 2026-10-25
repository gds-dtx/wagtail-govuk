"""Reading a guidance repo: the two adapters, the ERB translation and rendering.

No database and no network: the repos are the in-memory fixtures in
``guidance_helpers``.
"""

from django.test import SimpleTestCase

from govuk.guidance import erb
from govuk.guidance.adapters import build_bundle
from govuk.guidance.bundle import GuidanceSyncError, clean_path, next_review_date
from govuk.guidance.render import render_bundle, render_markdown, style_html
from govuk.guidance.repository import RepositoryRef, parse_repository_url
from govuk.tests.guidance_helpers import COMMIT, manifest_files, tech_docs_files

REPOSITORY = RepositoryRef("example", "example-way").at(COMMIT)


def _documents(files):
    rendered = render_bundle(build_bundle(files), REPOSITORY)
    return rendered, {document.path: document for document in rendered.documents}


class PathRulesTests(SimpleTestCase):
    def test_source_addresses_become_clean_paths(self):
        cases = {
            "/standards/alerting.html": "standards/alerting",
            "standards/alerting.md": "standards/alerting",
            "/standards/alerting/": "standards/alerting",
            "/nodejs/index.html": "nodejs",
            "docs/README.md": "docs",
            "/index.html": "",
            "/": "",
            "/a/../b.html#frag": "b",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(clean_path(value), expected)

    def test_next_review_date_follows_tech_docs(self):
        self.assertEqual(next_review_date("2026-08-19", "6 months"), "2027-02-19")
        self.assertEqual(next_review_date("2026-01-31", "1 month"), "2026-02-28")
        self.assertEqual(next_review_date("2026-01-01", "2 weeks"), "2026-01-15")
        self.assertEqual(next_review_date("2026-01-01", "soon"), "")

    def test_only_github_repository_urls_are_accepted(self):
        self.assertEqual(
            parse_repository_url("https://github.com/alphagov/gds-way.git"),
            ("alphagov", "gds-way"),
        )
        for bad in ("https://gitlab.com/a/b", "http://github.com/a/b", "https://github.com/a"):
            with self.subTest(url=bad), self.assertRaises(ValueError):
                parse_repository_url(bad)


class ErbTranslationTests(SimpleTestCase):
    def _translate(self, text, partials=None):
        return erb.translate(
            text, title="A title", load_partial=(partials or {}).get, source_name="page"
        )

    def test_the_helpers_it_knows_are_translated(self):
        result = self._translate(
            "# <%= current_page.data.title %>\n"
            "<%= partial 'partials/x' %>\n"
            "<%= warning_text('Mind the <a href=\"/x\">gap</a>') %>\n"
            "<%%= literal %>",
            partials={"partials/x": "From a partial"},
        )
        self.assertIn("# A title", result.text)
        self.assertIn("From a partial", result.text)
        self.assertIn(
            '<div data-guidance-component="warning">Mind the <a href="/x">gap</a></div>',
            result.text,
        )
        self.assertIn("<%= literal %>", result.text)
        self.assertEqual(result.warnings, [])

    def test_content_for_is_captured_not_output(self):
        result = self._translate("<% content_for :sidebar do %><ul></ul><% end %>Body")
        self.assertEqual(result.text, "Body")
        self.assertEqual(result.captures["sidebar"], "<ul></ul>")

    def test_anything_else_is_dropped_with_a_warning(self):
        result = self._translate("A<%= secret_helper %>B<% loop.each do |x| %>C<% end %>D")
        self.assertEqual(result.text, "ABD")
        self.assertEqual(len(result.warnings), 2)

    def test_partials_cannot_recurse_forever(self):
        result = self._translate("<%= partial 'p' %>", partials={"p": "x<%= partial 'p' %>"})
        self.assertEqual(result.text, "x" * erb.MAX_PARTIAL_DEPTH)
        self.assertTrue(any("too deeply" in w for w in result.warnings))


class TechDocsAdapterTests(SimpleTestCase):
    def setUp(self):
        self.rendered, self.documents = _documents(tech_docs_files())

    def test_every_page_is_read_at_its_clean_path(self):
        self.assertEqual(
            set(self.documents),
            {
                "",
                "accessibility",
                "standards/alerting",
                "standards/monitoring",
                "standards/operating-systems",
                "standards/source-code",
                "standards/source-code/use-github",
            },
        )

    def test_navigation_comes_from_the_index_sidebar_and_its_partials(self):
        """The first group only linked to headings on the index, so it goes."""
        [group] = self.rendered.site["navigation"]
        self.assertEqual(group["title"], "Operating a service")
        self.assertEqual(
            [(i["title"], i["path"]) for i in group["items"]],
            [("Manage alerts", "standards/alerting"), ("Store source code", "standards/source-code")],
        )

    def test_a_core_pages_sidebar_nests_under_its_entry(self):
        source_code = self.rendered.site["navigation"][0]["items"][1]
        self.assertEqual(
            [(i["title"], i["path"]) for i in source_code["children"]],
            [("Use GitHub", "standards/source-code/use-github")],
        )
        # Placed in the navigation, so not kept as a section of its own.
        self.assertEqual(self.rendered.site["sections"], [])

    def test_site_details_come_from_tech_docs_yml(self):
        site = self.rendered.site
        self.assertEqual(site["title"], "The Example Way")
        self.assertEqual(site["owner"], {"default": "#example-way", "slack_workspace": "example"})
        self.assertIn("For internal use.", site["banner_html"])
        self.assertNotIn("page-banner", site["banner_html"])
        self.assertEqual(
            site["redirects"],
            {"standards/old-alerting": {"path": "standards/alerting", "fragment": "sending-alerts"}},
        )

    def test_the_layout_banner_is_not_repeated_in_page_bodies(self):
        self.assertNotIn("For internal use", self.documents[""].html)

    def test_frontmatter_becomes_metadata(self):
        metadata = self.documents["standards/alerting"].metadata
        self.assertEqual(metadata["owner"], "#alerts")
        self.assertEqual(metadata["next_review_on"], "2026-02-28")
        self.assertEqual(self.documents["standards/monitoring"].metadata["owner"], "#example-way")
        self.assertTrue(self.documents["accessibility"].metadata["hidden"])

    def test_withdrawn_pages_keep_only_their_title(self):
        document = self.documents["standards/operating-systems"]
        self.assertEqual(document.metadata["status"], "withdrawn")
        self.assertEqual(document.html, "")

    def test_the_withdrawn_list_is_generated(self):
        self.assertIn(
            '<a class="govuk-link" href="guidance:/standards/operating-systems/">Operating systems</a>',
            self.documents[""].html,
        )

    def test_untranslatable_erb_is_reported(self):
        self.assertTrue(any("some_unknown_helper" in w for w in self.rendered.warnings))

    def test_a_repo_with_neither_layout_is_refused(self):
        with self.assertRaises(GuidanceSyncError):
            build_bundle({"README.md": b"# Hello"})


class RenderingTests(SimpleTestCase):
    def setUp(self):
        self.rendered, self.documents = _documents(tech_docs_files())
        self.alerting = self.documents["standards/alerting"]

    def test_anchors_match_tech_docs(self):
        """A clash takes the parent heading's id as a prefix, as tech-docs does."""
        self.assertEqual(
            [h["anchor"] for h in self.alerting.headings],
            ["sending-alerts", "overview", "prioritising-alerts", "prioritising-alerts-overview"],
        )
        self.assertIn('id="sending-alerts"', self.alerting.html)

    def test_the_source_h1_is_left_to_the_template(self):
        self.assertNotIn("<h1", self.alerting.html)

    def test_links_are_rewritten(self):
        monitoring = self.documents["standards/monitoring"].html
        # A reference-style link to a sibling page.
        self.assertIn('href="guidance:/standards/monitoring/"', self.alerting.html)
        # A relative link with a fragment.
        self.assertIn(
            'href="guidance:/standards/source-code/use-github/#rules"',
            self.documents["standards/source-code"].html,
        )
        # A file in the repo goes to GitHub, a missing page is left and reported.
        self.assertIn(
            f'href="https://github.com/example/example-way/blob/{COMMIT}/source/manuals/editorconfig"',
            monitoring,
        )
        self.assertIn('href="/nope.html"', monitoring)
        self.assertTrue(any("'/nope.html'" in w for w in self.rendered.warnings))

    def test_links_to_the_source_site_are_internal(self):
        html, _ = style_html(
            render_markdown("[x](https://example-way.gov.uk/standards/alerting.html#a)"),
            resolver=_resolver(),
            base_url_path="/index.html",
            source_name="test",
        )
        self.assertIn('href="guidance:/standards/alerting/#a"', html)

    def test_source_html_is_sanitised(self):
        html = self.documents["standards/monitoring"].html
        self.assertNotIn("<script", html)
        self.assertNotIn("onclick", html)
        self.assertNotIn("style=", html)
        self.assertNotIn("evil", html)
        self.assertIn('<p class="govuk-body">Styled</p>', html)

    def test_govuk_frontend_markup(self):
        html = self.alerting.html
        self.assertIn('<div class="govuk-warning-text">', html)
        self.assertIn('<span class="govuk-visually-hidden">Warning</span>', html)
        self.assertIn('class="govuk-heading-l"', html)
        self.assertIn('<div class="app-guidance__table"><table class="govuk-table">', html)
        self.assertIn('<th class="govuk-table__header" scope="row">P1', html)
        self.assertNotIn("data-guidance-component", html)

    def test_dropped_erb_leaves_the_text_around_it(self):
        self.assertRegex(self.alerting.html, r"The year is \d{4}\.</p>")


class ManifestAdapterTests(SimpleTestCase):
    def setUp(self):
        self.rendered, self.documents = _documents(manifest_files())

    def test_pages_come_from_content_dir_only(self):
        self.assertEqual(
            set(self.documents),
            {"", "standards/alerting", "standards/alerting/escalation", "standards/orphan"},
        )

    def test_navigation_nests_and_keeps_external_links(self):
        [group] = self.rendered.site["navigation"]
        alerting, manual = group["items"]
        self.assertEqual((alerting["path"], alerting["title"]), ("standards/alerting", "How to manage alerts"))
        # A heading on the page an item sits under is in-page, so left out.
        self.assertEqual(
            [(c["path"], c["fragment"]) for c in alerting["children"]],
            [("standards/alerting/escalation", "")],
        )
        self.assertEqual(manual["url"], "https://www.gov.uk/service-manual")
        self.assertTrue(any("standards/missing.md" in w for w in self.rendered.warnings))

    def test_a_section_whose_page_is_not_in_the_navigation_is_kept(self):
        [section] = self.rendered.site["sections"]
        self.assertEqual(section["owner_path"], "standards/orphan")
        self.assertEqual([i["path"] for i in section["items"]], ["standards/alerting"])

    def test_redirects(self):
        self.assertEqual(
            self.rendered.site["redirects"],
            {"old/alerting": {"path": "standards/alerting", "fragment": ""}},
        )

    def test_github_style_anchors(self):
        self.assertEqual(
            [h["anchor"] for h in self.documents["standards/alerting"].headings],
            ["sending-alerts", "sending-alerts-1"],
        )

    def test_github_alerts_become_govuk_components(self):
        html = self.documents["standards/alerting"].html
        self.assertIn('<div class="govuk-warning-text">', html)
        self.assertIn("Do not page people at night.", html)
        self.assertIn('<div class="govuk-inset-text">', html)
        self.assertNotIn("[!", html)

    def test_images_are_served_from_the_repo_at_the_commit(self):
        self.assertIn(
            f'src="https://raw.githubusercontent.com/example/example-way/{COMMIT}/docs/images/diagram.png"',
            self.documents["standards/alerting"].html,
        )

    def test_markdown_links_between_pages(self):
        self.assertIn('href="guidance:/standards/alerting/"', self.documents[""].html)

    def test_banner_is_markdown(self):
        self.assertIn("<strong>For internal use.</strong>", self.rendered.site["banner_html"])

    def test_the_schema_is_required(self):
        files = manifest_files()
        files["guidance.yml"] = b"title: No schema\n"
        with self.assertRaises(GuidanceSyncError):
            build_bundle(files)


def _resolver():
    from govuk.guidance.render import LinkResolver

    return LinkResolver(build_bundle(tech_docs_files()), REPOSITORY)
