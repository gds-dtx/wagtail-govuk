"""Tables on a simple content page.

Wagtail's rich text has no table feature, so without a block the only route
would be hand-written HTML in a raw HTML embed, which is not a formatting
option a content designer has. A content page body's Table block gives them a
grid instead.

Two things are checked beyond "a table appears". It has to be a GOV.UK table,
because the rest of the service is, and it has to scroll rather than drag the
page sideways: four columns of full sentences cannot be made to fit 320px, and
a page that overflows fails WCAG 1.4.10 Reflow. The service is at zero axe
violations and no overflow at 320px, and a new field is the obvious way to
lose that.
"""

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from wagtail.models import Site

from govuk.models import ContentPage
from govuk.models.blocks import GovukTableBlock
from govuk.page_import_export import (
    PAGE_EXPORT_FORMAT,
    build_page_export_payload,
    import_pages_from_payload,
)
from govuk.tests.page_helpers import text_body
from govuk.wagtail_hooks import (
    RawHtmlEmbedHandler,
    _encode_raw_html,
    _wrap_table_in_scroll_region,
)


def _feature_flags() -> dict[str, bool]:
    return {
        "SKILLS": True,
        "FEEDBACK": False,
    }


def _table(
    data, *, header_row=False, header_col=False, caption="", cell=None, merge=None
) -> list[dict]:
    value = {
        "data": data,
        "first_row_is_table_header": header_row,
        "first_col_is_header": header_col,
        "table_caption": caption,
    }
    # The grid's per-cell metadata and merges, as Wagtail saves them.
    if cell is not None:
        value["cell"] = cell
    if merge is not None:
        value["mergeCells"] = merge
    return [{"type": "table", "value": value}]


@override_settings(FEATURE_FLAGS=_feature_flags())
class ContentPageTableRenderingTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root = self.site.root_page.specific

    def _render(self, blocks) -> str:
        page = self.root.add_child(
            instance=ContentPage(
                title="Contextual challenges",
                slug="contextual-challenges",
                body=[*text_body("<p>Before the table.</p>"), *blocks],
            )
        )
        page.save_revision().publish()
        return self.client.get(page.url).content.decode()

    def test_a_table_renders_as_a_govuk_table(self):
        html = self._render(
            _table([["Grade", "Level"], ["SCS1", "Deputy director"]], header_row=True)
        )

        self.assertIn('<table class="govuk-table">', html)
        self.assertIn('<thead class="govuk-table__head">', html)
        self.assertIn('scope="col"', html)
        self.assertIn("Deputy director", html)

    def test_a_wide_table_scrolls_instead_of_the_page(self):
        html = self._render(_table([["a", "b", "c", "d"], ["1", "2", "3", "4"]]))

        self.assertIn('class="table-scroll"', html)
        self.assertIn('role="region"', html)
        self.assertIn('tabindex="0"', html)

    def test_a_caption_names_the_table_and_its_scroll_region(self):
        html = self._render(
            _table([["a", "b"]], caption="Indicative grades")
        )

        self.assertIn(
            '<caption class="govuk-table__caption govuk-table__caption--m">'
            "Indicative grades</caption>",
            html,
        )
        self.assertIn('aria-label="Indicative grades"', html)

    def test_a_table_with_no_caption_still_names_its_region(self):
        """A region with no accessible name is a region a screen reader skips."""
        html = self._render(_table([["a", "b"]]))

        self.assertIn('aria-label="Table"', html)

    def test_a_first_column_of_headers_is_marked_up_as_one(self):
        """Ant asked for the challenge tables reformatted as vertical tables."""
        html = self._render(
            _table([["Level 1", "Works alone"], ["Level 2", "Leads"]], header_col=True)
        )

        self.assertIn('scope="row"', html)
        self.assertIn("Level 1", html)

    def test_cell_text_is_escaped(self):
        """A table is where a paste from a document arrives, markup and all."""
        html = self._render(_table([["<script>alert(1)</script>", "b"]]))

        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_text_before_a_table_renders_above_it(self):
        html = self._render(_table([["a", "b"]]))

        self.assertLess(html.index("Before the table."), html.index("govuk-table"))

    def test_text_blocks_and_tables_interleave(self):
        """A table part-way down a page, with text either side of it."""
        html = self._render(
            [
                {"type": "text", "value": "<p>Above the table.</p>"},
                *_table([["a", "b"]]),
                {"type": "text", "value": "<p>Below the table.</p>"},
            ]
        )

        self.assertLess(html.index("Above the table."), html.index("govuk-table"))
        self.assertLess(html.index("govuk-table"), html.index("Below the table."))

    def test_a_page_without_a_table_has_no_scroll_region(self):
        html = self._render([])

        self.assertIn("Before the table.", html)
        self.assertNotIn("table-scroll", html)

    def test_a_centred_cell_is_centred(self):
        """The grid saves its own class names, which style nothing on GOV.UK."""
        html = self._render(
            _table(
                [["Grade", "Level"], ["SCS1", "Deputy director"]],
                header_row=True,
                cell=[{"row": 1, "col": 1, "className": "htCenter htMiddle"}],
            )
        )

        self.assertIn(
            '<td class="govuk-table__cell app-table-cell--align-centre"', html
        )
        self.assertNotIn("htCenter", html)

    def test_a_right_aligned_header_is_right_aligned(self):
        html = self._render(
            _table(
                [["Grade", "Level"], ["SCS1", "Deputy director"]],
                header_row=True,
                cell=[{"row": 0, "col": 1, "className": "htRight"}],
            )
        )

        self.assertIn('class="govuk-table__header app-table-cell--align-right"', html)

    def test_alignment_lands_on_the_cell_it_was_set_on(self):
        """Body rows count from the header, as Wagtail's own cell tags do."""
        html = self._render(
            _table(
                [["Grade", "Level"], ["SCS1", "Deputy director"], ["SCS2", "Director"]],
                header_row=True,
                cell=[{"row": 2, "col": 0, "className": "htRight"}],
            )
        )

        aligned = html.index("app-table-cell--align-right")
        self.assertLess(html.index("Deputy director"), aligned)
        self.assertLess(aligned, html.index("SCS2"))

    def test_vertical_and_justified_alignment_add_nothing(self):
        html = self._render(
            _table(
                [["a", "b"]],
                cell=[{"row": 0, "col": 0, "className": "htJustify htBottom"}],
            )
        )

        self.assertNotIn("app-table-cell--align", html)
        self.assertNotIn("htJustify", html)

    def test_merged_cells_span_and_leave_out_the_cells_they_cover(self):
        html = self._render(
            _table(
                [["Spans two", "Covered by the merge"], ["c", "d"]],
                cell=[{"row": 0, "col": 1, "hidden": True}],
                merge=[{"row": 0, "col": 0, "rowspan": 1, "colspan": 2}],
            )
        )

        self.assertIn('rowspan="1" colspan="2">Spans two</td>', html)
        self.assertNotIn("Covered by the merge", html)

    def test_a_merged_header_spans_the_header_row(self):
        html = self._render(
            _table(
                [["Levels", "Hidden heading"], ["1", "2"]],
                header_row=True,
                cell=[{"row": 0, "col": 1, "hidden": True}],
                merge=[{"row": 0, "col": 0, "rowspan": 1, "colspan": 2}],
            )
        )

        self.assertIn('rowspan="1" colspan="2">Levels</th>', html)
        self.assertNotIn("Hidden heading", html)


class GovukTableBlockOptionsTests(SimpleTestCase):
    def test_the_grid_offers_alignment_and_merging(self):
        options = GovukTableBlock().table_options

        self.assertIn("alignment", options["contextMenu"])
        self.assertIn("mergeCells", options["contextMenu"])
        self.assertIs(options["mergeCells"], True)
        # On top of Wagtail's own menu, not instead of it.
        self.assertIn("row_above", options["contextMenu"])
        self.assertEqual(options["language"], "en-US")


@override_settings(FEATURE_FLAGS=_feature_flags())
class ContentPageTableExportTests(TestCase):
    """A table has to survive a round trip through an export file."""

    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root = self.site.root_page.specific
        self.page = self.root.add_child(
            instance=ContentPage(
                title="Contextual challenges",
                slug="contextual-challenges",
                body=[
                    *text_body("<p>Before the table.</p>"),
                    *_table([["Grade", "Level"]], header_row=True),
                ],
            )
        )
        self.page.save_revision().publish()
        self.user = get_user_model().objects.create_superuser(
            username="cutover", email="cutover@example.gov.uk", password="unused"
        )

    def test_a_table_survives_an_export_and_import(self):
        payload = build_page_export_payload(
            site=self.site, pages=[self.page], user=self.user
        )

        self.page.body = []
        self.page.save_revision().publish()

        import_pages_from_payload(payload=payload, site=self.site, user=self.user)

        reloaded = ContentPage.objects.get(pk=self.page.pk)
        self.assertEqual(
            [block.block_type for block in reloaded.body], ["text", "table"]
        )

    def test_a_file_from_before_block_bodies_is_refused_whole(self):
        """Its HTML would arrive as an empty stream and blank the page."""
        payload = {
            "format": PAGE_EXPORT_FORMAT,
            "pages": [
                {
                    "model": "govuk.ContentPage",
                    "settings": {
                        "slug": "contextual-challenges",
                        "title": "Contextual challenges",
                    },
                    "fields": {"body": "<p>Rewritten by the old file.</p>"},
                }
            ],
        }

        result = import_pages_from_payload(
            payload=payload, site=self.site, user=self.user
        )

        self.assertTrue(
            any("exported before page content became blocks" in e for e in result.errors),
            result.errors,
        )
        reloaded = ContentPage.objects.get(pk=self.page.pk)
        self.assertEqual(
            [block.block_type for block in reloaded.body], ["text", "table"]
        )

    def test_a_file_with_the_old_body_blocks_field_is_refused_too(self):
        """Exported between the table block and block bodies: body and body_blocks."""
        payload = {
            "format": PAGE_EXPORT_FORMAT,
            "pages": [
                {
                    "model": "govuk.ContentPage",
                    "settings": {"slug": "new-page", "title": "New page"},
                    "fields": {"body": "", "body_blocks": []},
                }
            ],
        }

        result = import_pages_from_payload(
            payload=payload, site=self.site, user=self.user
        )

        self.assertTrue(result.errors)
        self.assertFalse(ContentPage.objects.filter(slug="new-page").exists())


class RawHtmlTableScrollTests(TestCase):
    """The other route a table arrives by, which is still open to editors.

    main.css has carried the scrollable region since f29e990 and nothing ever
    applied the class, so a hand-written table still dragged the page sideways.
    """

    def test_a_pasted_table_is_given_a_scroll_region(self):
        wrapped = _wrap_table_in_scroll_region("<table><tr><td>a</td></tr></table>")

        self.assertTrue(wrapped.startswith('<div class="table-scroll"'))
        self.assertIn('role="region"', wrapped)
        self.assertIn('aria-label="Table"', wrapped)

    def test_the_region_takes_its_name_from_the_caption(self):
        wrapped = _wrap_table_in_scroll_region(
            "<table><caption>Grades</caption><tr><td>a</td></tr></table>"
        )

        self.assertIn('aria-label="Grades"', wrapped)

    def test_a_caption_holding_markup_cannot_break_out_of_the_attribute(self):
        wrapped = _wrap_table_in_scroll_region(
            '<table><caption>a" onclick="alert(1)</caption></table>'
        )

        self.assertNotIn('onclick="alert(1)"', wrapped)

    def test_a_table_already_in_a_scroll_region_is_not_wrapped_twice(self):
        """The contextual challenges page hand-wrote the wrapper into its HTML.

        Six of its tables arrive already inside a table-scroll div, so wrapping
        on sight would nest one scrolling region inside another and give the
        keyboard two stops to reach the same table.
        """
        html = (
            '<div class="table-scroll" role="region" tabindex="0" '
            'aria-label="Levels of digital and data transformation">'
            "<table><tr><td>a</td></tr></table></div>"
        )

        self.assertEqual(_wrap_table_in_scroll_region(html), html)

    def test_html_that_is_not_a_table_is_left_alone(self):
        html = "<p>Just a paragraph.</p>"

        self.assertEqual(_wrap_table_in_scroll_region(html), html)

    def test_a_table_reaches_a_page_through_the_embed_and_is_wrapped(self):
        encoded = _encode_raw_html("<table><tr><td>a</td></tr></table>")
        [rendered] = RawHtmlEmbedHandler.expand_db_attributes_many([{"html": encoded}])

        self.assertIn('class="table-scroll"', rendered)
        self.assertIn("<table>", rendered)
