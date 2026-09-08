"""Tables on a simple content page -- CS32-3527.

"Tables can be added to the page" was the last unticked box on that ticket
while everything around it was done. Wagtail's rich text has no table feature,
so the only route was hand-written HTML in a raw HTML embed, which is not a
formatting option a content designer has. ContentPage.body_blocks gives them a
grid instead.

Two things are checked beyond "a table appears". It has to be a GOV.UK table,
because the rest of the service is, and it has to scroll rather than drag the
page sideways: four columns of full sentences cannot be made to fit 320px, and
a page that overflows fails WCAG 1.4.10 Reflow. The service is at zero axe
violations and no overflow at 320px, and a new field is the obvious way to
lose that.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from wagtail.models import Site

from govuk.models import ContentPage
from govuk.page_import_export import (
    PAGE_EXPORT_FORMAT,
    build_page_export_payload,
    import_pages_from_payload,
)
from govuk.wagtail_hooks import (
    RawHtmlEmbedHandler,
    _encode_raw_html,
    _wrap_table_in_scroll_region,
)


def _feature_flags() -> dict[str, bool]:
    return {
        "SKILLS": True,
        "ORGANISATIONS": False,
        "PEOPLE_FINDER": False,
        "FEEDBACK": False,
    }


def _table(
    data, *, header_row=False, header_col=False, caption=""
) -> list[dict]:
    return [
        {
            "type": "table",
            "value": {
                "data": data,
                "first_row_is_table_header": header_row,
                "first_col_is_header": header_col,
                "table_caption": caption,
            },
        }
    ]


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
                body="<p>Before the table.</p>",
                body_blocks=blocks,
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

    def test_the_body_field_still_renders_above_the_blocks(self):
        html = self._render(_table([["a", "b"]]))

        self.assertLess(html.index("Before the table."), html.index("govuk-table"))

    def test_text_blocks_and_tables_interleave(self):
        """A table part-way down a page: the body moves into a Text block."""
        html = self._render(
            [
                {"type": "text", "value": "<p>Above the table.</p>"},
                *_table([["a", "b"]]),
                {"type": "text", "value": "<p>Below the table.</p>"},
            ]
        )

        self.assertLess(html.index("Above the table."), html.index("govuk-table"))
        self.assertLess(html.index("govuk-table"), html.index("Below the table."))

    def test_a_page_with_no_blocks_renders_unchanged(self):
        html = self._render([])

        self.assertIn("Before the table.", html)
        self.assertNotIn("table-scroll", html)


@override_settings(FEATURE_FLAGS=_feature_flags())
class ContentPageTableExportTests(TestCase):
    """A new field on the page model is a new way for an export to lose content.

    The verified export was taken before this field existed, and it is what
    production will be built from, so both directions matter: a table has to
    survive a round trip, and a file that predates the field must leave it
    alone rather than blanking it.
    """

    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root = self.site.root_page.specific
        self.page = self.root.add_child(
            instance=ContentPage(
                title="Contextual challenges",
                slug="contextual-challenges",
                body="<p>Before the table.</p>",
                body_blocks=_table([["Grade", "Level"]], header_row=True),
            )
        )
        self.page.save_revision().publish()

    def test_a_table_survives_an_export_and_import(self):
        user = get_user_model().objects.create_superuser(
            username="cutover", email="cutover@example.gov.uk", password="unused"
        )
        payload = build_page_export_payload(
            site=self.site, pages=[self.page], user=user
        )

        self.page.body_blocks = []
        self.page.save_revision().publish()

        import_pages_from_payload(payload=payload, site=self.site, user=user)

        reloaded = ContentPage.objects.get(pk=self.page.pk)
        self.assertEqual(len(reloaded.body_blocks), 1)
        self.assertEqual(reloaded.body_blocks[0].block_type, "table")

    def test_a_file_taken_before_the_field_existed_leaves_it_alone(self):
        user = get_user_model().objects.create_superuser(
            username="cutover", email="cutover@example.gov.uk", password="unused"
        )
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

        import_pages_from_payload(payload=payload, site=self.site, user=user)

        reloaded = ContentPage.objects.get(pk=self.page.pk)
        self.assertIn("Rewritten by the old file", reloaded.body)
        self.assertEqual(len(reloaded.body_blocks), 1)


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
