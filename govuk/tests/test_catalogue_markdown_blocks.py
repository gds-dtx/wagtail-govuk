"""Converting between the catalogue's Markdown and its pages' blocks, and rendering those blocks."""

from django.test import SimpleTestCase

from govuk.catalogue.markdown_blocks import (
    blocks_to_markdown,
    markdown_to_blocks,
    split_sections,
)
from govuk.catalogue.presentation import _contents, add_heading_anchors
from govuk.models.catalogue.blocks import CatalogueBodyBlock


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
