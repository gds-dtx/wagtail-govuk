"""A content page body built from blocks: text, tables, cards, banners and raw HTML.

The body is one stream an editor arranges, so the page has to lay out whatever
order it is given. Where the page has no sidebar a card row or banner runs
across the full width, with the text between them in the usual column; beside
heading navigation or the role navigation there is no room to break out, and
every block stays in the column.

A table has to be a GOV.UK table and scroll rather than drag the page
sideways: four columns of full sentences cannot fit 320px, and a page that
overflows fails WCAG 1.4.10 Reflow.
"""

import json
import re
from uuid import uuid4

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils.csp import CSP
from wagtail.blocks import StructBlockValidationError
from wagtail.models import Site
from wagtail.test.utils.form_data import (
    nested_form_data,
    querydict_from_html,
    rich_text,
    streamfield,
)

from govuk.models import ContentPage, FrameworkContentPage, GovukTag
from govuk.models.blocks import (
    BannerBlock,
    BodyRawHtmlBlock,
    GovukTableBlock,
    SizedTableBlock,
)
from govuk.models.raw_html import sanitise_raw_html
from govuk.page_import_export import (
    PAGE_EXPORT_FORMAT,
    build_page_export_payload,
    import_pages_from_payload,
)
from govuk.search_backend import search_backend
from govuk.tests.helpers import feature_flags, make_framework_main_page
from govuk.wagtail_hooks import _wrap_table_in_scroll_region

FRAMEWORK_FLAGS = feature_flags(skills=True)


def _text(html: str) -> dict:
    return {"type": "text", "value": html}


def _item(value) -> dict:
    # A ListBlock reads {"type": "item", "value": ...} as an item only when it
    # carries an id too; without one the whole dict is taken as the value.
    return {"type": "item", "value": value, "id": str(uuid4())}


def _card(title: str, *, tags=(), url: str = "") -> dict:
    return _item(
        {
            "title": title,
            "image": None,
            "image_fit": "cover",
            "text": f"<p>{title} summary</p>",
            "link": {"title": "Find out more", "page": None, "external_url": url},
            "tags": [_item(tag.pk) for tag in tags],
        }
    )


def _cards(*cards, heading: str = "") -> dict:
    return {"type": "cards", "value": {"heading": heading, "cards": list(cards)}}


def _banner(**overrides) -> dict:
    value = {
        "heading": "Join the community",
        "text": "<p>Meet people doing the same work.</p>",
        "button_text": "Sign up",
        "button_page": None,
        "button_external_url": "https://example.gov.uk/join",
        "style": "brand",
    }
    value.update(overrides)
    return value


def _grid(
    data, *, header_row=False, header_col=False, caption="", cell=None, merge=None
) -> dict:
    value = {
        "data": data,
        "first_row_is_table_header": header_row,
        "first_col_is_header": header_col,
        "table_caption": caption,
    }
    # The grid's per-cell metadata and merges.
    if cell is not None:
        value["cell"] = cell
    if merge is not None:
        value["mergeCells"] = merge
    return value


def _table_value(data, *, widths=(), **grid) -> dict:
    return {"table": _grid(data, **grid), "column_widths": [_item(width) for width in widths]}


def _table(data, *, widths=(), **grid) -> list[dict]:
    return [{"type": "table", "value": _table_value(data, widths=widths, **grid)}]


def _list_block(values) -> dict:
    """Form data for a ListBlock, which wagtail.test.utils has no helper for."""
    data: dict = {"count": len(values)}
    for index, value in enumerate(values):
        data[str(index)] = {"value": value, "deleted": "", "order": index, "id": ""}
    return data


class _PageTestCase(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root = self.site.root_page.specific

    def _publish(self, blocks, slug="get-involved", **fields) -> ContentPage:
        page = self.root.add_child(
            instance=ContentPage(title="Get involved", slug=slug, body=blocks, **fields)
        )
        page.save_revision().publish()
        return page

    def _html(self, page, **params) -> str:
        response = self.client.get(page.url, params)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()


class ContentPageBlockLayoutTests(_PageTestCase):
    def test_without_a_sidebar_a_card_row_is_a_band_between_the_text(self):
        html = self._html(
            self._publish(
                [
                    _text("<p>Before the cards.</p>"),
                    _cards(_card("Alpha card")),
                    _text("<p>After the cards.</p>"),
                ]
            )
        )

        band = html.index('class="section-cards-container body-band"')
        self.assertLess(html.index("Before the cards."), band)
        self.assertLess(band, html.index("Alpha card"))
        self.assertLess(html.index("Alpha card"), html.index("After the cards."))
        # The text either side is in a width container of its own, not the band.
        self.assertEqual(html.count("govuk-width-container body-segment"), 2)

    def test_beside_heading_navigation_a_card_row_stays_in_the_column(self):
        html = self._html(
            self._publish(
                [_text("<h2>Services</h2>"), _cards(_card("Alpha card"))],
                enable_free_text_heading_navigation=True,
            )
        )

        self.assertIn('class="section-cards-container body-panel"', html)
        self.assertNotIn("body-band", html)
        # Inside the one source the heading navigation reads.
        self.assertLess(
            html.index("data-auto-heading-source"), html.index("Alpha card")
        )

    def test_a_banner_without_a_sidebar_spans_the_page(self):
        html = self._html(self._publish([{"type": "banner", "value": _banner()}]))

        self.assertIn(
            "body-banner body-banner--brand body-banner--inverse body-band", html
        )
        self.assertIn("Join the community", html)

    def test_a_custom_colour_banner_carries_its_style_sheet_nonce(self):
        """The CSP blocks style attributes, so the colour is a nonced style sheet."""
        page = self._publish(
            [{"type": "banner", "value": _banner(style="custom", colour="#ca357c")}]
        )

        response = self.client.get(page.url)
        html = response.content.decode()
        nonce = re.search(r"<style nonce=\"([^\"]+)\">", html).group(1)

        self.assertIn(f"'nonce-{nonce}'", response.headers["Content-Security-Policy"])
        self.assertIn(".body-banner--colour-ca357c{background-color:#ca357c}", html)
        self.assertNotIn('style="', html)

    def test_a_page_holding_only_blocks_renders_them(self):
        html = self._html(self._publish([_text("<p>Only a block.</p>")]))

        self.assertIn("Only a block.", html)

    def test_raw_html_is_put_on_the_page_as_written(self):
        html = self._html(
            self._publish(
                [
                    _text("<p>Before.</p>"),
                    {
                        "type": "raw_html",
                        "value": '<dl class="app-summary"><dt>Term</dt><dd>Detail</dd></dl>',
                    },
                ]
            )
        )

        self.assertIn('<dl class="app-summary"><dt>Term</dt><dd>Detail</dd></dl>', html)
        # In the text column with the text around it, not a band of its own,
        # inside the container main.js gives rich text's classes in.
        column = html.index('class="govuk-body rich-text-content"')
        self.assertLess(column, html.index('<dl class="app-summary">'))
        self.assertNotIn("body-band", html)

    def test_html_stored_without_cleaning_is_cleaned_on_the_way_out(self):
        """An import writes stream data without running the block's clean."""
        html = self._html(
            self._publish(
                [
                    {
                        "type": "raw_html",
                        "value": (
                            '<meta http-equiv="refresh" content="0;url=https://elsewhere.example">'
                            '<p class="app-note">Kept.</p><script>alert("raw")</script>'
                        ),
                    }
                ]
            )
        )

        self.assertIn('<p class="app-note">Kept.</p>', html)
        self.assertNotIn("elsewhere.example", html)
        self.assertNotIn('alert("raw")', html)

    def test_a_raw_html_table_scrolls_like_a_table_block(self):
        html = self._html(
            self._publish(
                [
                    {
                        "type": "raw_html",
                        "value": "<table><caption>Grades</caption><tr><td>a</td></tr></table>",
                    }
                ]
            )
        )

        self.assertIn(
            '<div class="table-scroll" tabindex="0" role="region" aria-label="Grades">'
            "<table><caption>Grades</caption>",
            html,
        )

    def test_text_blocks_are_not_wrapped_in_a_div_each(self):
        html = self._html(self._publish([_text("<p>Plain.</p>")]))

        self.assertNotIn("block-text", html)

    @override_settings(FEATURE_FLAGS=FRAMEWORK_FLAGS)
    def test_a_csv_link_in_a_text_block_becomes_an_attachment(self):
        html = self._html(
            self._publish(
                [
                    _text(
                        '<p><a href="/download/roles.csv">'
                        "Role content - Capability Framework (CSV)</a></p>"
                    )
                ]
            )
        )

        self.assertIn('<section class="gem-c-attachment', html)


@override_settings(FEATURE_FLAGS=FRAMEWORK_FLAGS)
class FrameworkContentPageBlockLayoutTests(TestCase):
    """A framework content page always carries the role navigation.

    That is forced in its own get_context, after the shared framework context
    is built, which is why the layout is worked out at render time.
    """

    def test_a_card_row_stays_in_the_column_beside_the_role_navigation(self):
        root = Site.objects.get(is_default_site=True).root_page.specific
        main_page = make_framework_main_page(root)
        page = main_page.add_child(
            instance=FrameworkContentPage(
                title="Roadmap",
                slug="roadmap",
                body=[_cards(_card("Alpha card"))],
            )
        )
        page.save_revision().publish()

        html = self.client.get(page.url).content.decode()

        self.assertIn("Alpha card", html)
        self.assertIn('class="section-cards-container body-panel"', html)
        self.assertNotIn("body-band", html)


class ContentPageCardTagTests(_PageTestCase):
    def setUp(self):
        super().setUp()
        self.alpha = GovukTag.objects.create(slug="alpha", name="Alpha")
        self.beta = GovukTag.objects.create(slug="beta", name="Beta")

    def _blocks(self):
        return [
            _text("<p>Before the cards.</p>"),
            _cards(_card("Alpha card", tags=[self.alpha]), heading="Alpha row"),
            _cards(_card("Beta card", tags=[self.beta]), heading="Beta row"),
            _text("<p>After the cards.</p>"),
        ]

    def test_the_filter_narrows_the_cards_and_leaves_the_text_alone(self):
        page = self._publish(self._blocks(), enable_tag_filter=True)

        html = self._html(page, tag="alpha")

        self.assertIn("Alpha card", html)
        self.assertNotIn("Beta card", html)
        self.assertIn("Before the cards.", html)
        self.assertIn("After the cards.", html)

    def test_a_row_the_filter_empties_is_dropped_with_its_heading(self):
        page = self._publish(self._blocks(), enable_tag_filter=True)

        html = self._html(page, tag="alpha")

        self.assertIn("Alpha row", html)
        self.assertNotIn("Beta row", html)

    def test_the_filter_sits_above_the_first_row_of_cards(self):
        html = self._html(self._publish(self._blocks(), enable_tag_filter=True))

        self.assertLess(html.index("Before the cards."), html.index("Filter by tag"))
        self.assertLess(html.index("Filter by tag"), html.index("Alpha card"))
        self.assertEqual(html.count("Filter by tag"), 1)

    def test_a_tag_in_the_query_does_nothing_where_the_filter_is_off(self):
        html = self._html(self._publish(self._blocks()), tag="alpha")

        self.assertNotIn("Filter by tag", html)
        self.assertIn("Beta card", html)

    def test_tag_display_controls_the_card_tag_labels(self):
        page = self._publish(self._blocks())
        label = '<strong class="govuk-tag govuk-tag--grey">Alpha</strong>'

        self.assertNotIn(label, self._html(page))

        page.enable_tag_display = True
        page.save_revision().publish()

        self.assertIn(label, self._html(page))

    def test_a_card_in_a_content_page_body_is_a_search_result(self):
        self._publish(
            [
                _cards(
                    _card(
                        "Apprenticeship scheme",
                        url="https://example.gov.uk/apprenticeships",
                    )
                )
            ]
        )

        results = search_backend.search("Apprenticeship", page=1).object_list

        self.assertIn(
            ("Apprenticeship scheme", "https://example.gov.uk/apprenticeships"),
            [(result.title, result.url) for result in results],
        )


class BannerBlockTests(SimpleTestCase):
    def _value(self, **overrides):
        block = BannerBlock()
        return block, block.to_python(_banner(**overrides))

    def _render(self, **overrides) -> str:
        block, value = self._value(**overrides)
        return block.render(value, context={"full_width": True})

    def test_an_incomplete_banner_is_refused(self):
        cases = (
            # what is wrong, the change, the field the error is on
            ("a button needs somewhere to go", {"button_external_url": ""}, "button_page"),
            ("somewhere to go needs button text", {"button_text": ""}, "button_text"),
            (
                "a banner needs a heading, text or html",
                {"heading": "", "text": "", "raw_html": ""},
                "heading",
            ),
            ("a custom style needs a colour", {"style": "custom", "colour": ""}, "colour"),
            ("a custom colour must be hex", {"style": "custom", "colour": "pink"}, "colour"),
        )
        for label, overrides, field in cases:
            with self.subTest(label):
                block, value = self._value(**overrides)

                with self.assertRaises(StructBlockValidationError) as raised:
                    block.clean(value)
                self.assertIn(field, raised.exception.block_errors)

    def test_a_banner_may_have_no_button(self):
        block, value = self._value(button_text="", button_external_url="")

        block.clean(value)
        html = block.render(value, context={"full_width": True})

        self.assertNotIn("govuk-button", html)

    def test_a_brand_banner_has_an_inverse_button(self):
        html = self._render()

        self.assertIn("govuk-button govuk-button--inverse", html)
        self.assertIn('href="https://example.gov.uk/join"', html)

    def test_a_light_banner_has_a_plain_button(self):
        html = self._render(style="light")

        self.assertIn("body-banner--light", html)
        self.assertNotIn("govuk-button--inverse", html)

    def test_a_stored_style_cannot_reach_the_class_attribute(self):
        """A page import writes stream data without running the block's validation."""
        html = self._render(style='light" onclick="alert(1)')

        self.assertNotIn("onclick", html)
        self.assertIn("body-banner--brand", html)

    def test_beside_a_sidebar_a_banner_is_a_panel_in_the_column(self):
        block, value = self._value()

        html = block.render(value, context={})

        self.assertIn("body-panel", html)
        self.assertNotIn("govuk-width-container", html)

    def test_a_banner_may_have_no_heading(self):
        block, value = self._value(heading="")

        block.clean(value)
        html = block.render(value, context={"full_width": True})

        self.assertNotIn("<h2", html)
        self.assertIn("Meet people doing the same work.", html)

    def test_raw_html_sits_between_the_text_and_the_button(self):
        html = self._render(raw_html='<figure class="hero__logo">Crest</figure>')

        self.assertLess(html.index("body-banner__text"), html.index("hero__logo"))
        self.assertLess(html.index("hero__logo"), html.index("govuk-button"))

    def test_across_the_page_only_the_heading_and_text_are_two_thirds(self):
        html = self._render(raw_html='<figure class="hero__logo">Crest</figure>')

        column = html.index("govuk-grid-column-two-thirds")
        column_end = html.index("</div>", html.index("body-banner__text"))
        column_end = html.index("</div>", column_end + 1)
        self.assertLess(column, html.index("body-banner__heading"))
        self.assertLess(html.index("body-banner__text"), column_end)
        self.assertLess(column_end, html.index("body-banner__html"))
        self.assertLess(column_end, html.index("govuk-button"))

    def test_html_alone_has_no_two_thirds_column(self):
        html = self._render(
            heading="",
            text="",
            raw_html='<div class="govuk-grid-row"><div class="govuk-grid-column-one-third">Side</div></div>',
        )

        self.assertNotIn("govuk-grid-column-two-thirds", html)
        self.assertIn("govuk-width-container", html)
        self.assertIn("govuk-grid-column-one-third", html)

    def test_raw_html_is_cleaned_when_rendered(self):
        """A page import writes stream data without running clean."""
        html = self._render(raw_html="<p>Kept</p><script>go()</script>")

        self.assertIn("<p>Kept</p>", html)
        self.assertNotIn("<script>", html)

    def test_a_dark_custom_colour_has_white_text_and_an_inverse_button(self):
        block, value = self._value(style="custom", colour="#CA357C")

        block.clean(value)
        html = block.render(value, context={"full_width": True, "csp_nonce": "n0nce"})

        self.assertIn(
            '<style nonce="n0nce">'
            ".body-banner--colour-ca357c{background-color:#ca357c}",
            html,
        )
        self.assertIn(
            "body-banner body-banner--custom body-banner--inverse "
            "body-banner--colour-ca357c body-band",
            html,
        )
        self.assertIn("govuk-button--inverse", html)

    def test_a_pale_custom_colour_has_dark_text_and_a_plain_button(self):
        html = self._render(style="custom", colour="#fd0")

        self.assertIn("body-banner--colour-ffdd00", html)
        self.assertNotIn("body-banner--inverse", html)
        self.assertNotIn("govuk-button--inverse", html)

    def test_a_stored_colour_cannot_reach_the_style_sheet(self):
        """A page import writes stream data without running the block's validation."""
        html = self._render(style="custom", colour="red}body{display:none")

        self.assertNotIn("<style", html)
        self.assertNotIn("display:none", html)
        self.assertIn("body-banner--brand", html)

    def test_an_inverse_button_takes_the_background_as_its_text_colour(self):
        html = self._render(style="custom", colour="#ca357c")

        self.assertIn(
            ".body-banner--colour-ca357c .govuk-button--inverse:not(:focus),"
            ".body-banner--colour-ca357c .govuk-button--inverse:hover,"
            ".body-banner--colour-ca357c .govuk-button--inverse:active"
            "{color:#ca357c}",
            html,
        )

    def test_a_plain_button_keeps_its_own_text_colour(self):
        self.assertNotIn(".govuk-button--inverse", self._render(style="custom", colour="#fd0"))

    def test_the_admin_style_policy_has_no_nonce(self):
        """A nonce would make browsers ignore the 'unsafe-inline' the admin needs."""
        self.assertIn(CSP.NONCE, settings.SECURE_CSP["style-src"])
        self.assertNotIn(CSP.NONCE, settings.SECURE_CSP_ADMIN["style-src"])
        self.assertIn(CSP.UNSAFE_INLINE, settings.SECURE_CSP_ADMIN["style-src"])


class RawHtmlAllowlistTests(SimpleTestCase):
    """What a Raw HTML block keeps, and what it takes out.

    The content security policy already blocks most of what is removed; these
    are the things it does not cover, and the things it would block anyway.
    """

    def test_what_has_no_place_on_a_page_is_removed_whole(self):
        cases = {
            "scripts go with their content": (
                "<script>alert(1)</script><p>Kept.</p>",
                "<p>Kept.</p>",
            ),
            # The policy allows this site's own scripts, the admin's among them.
            "a script from this site": (
                '<script src="/static/wagtailadmin/js/core.js"></script>',
                "",
            ),
            # The policy does not govern where a page navigates to.
            "a refresh to elsewhere": (
                '<meta http-equiv="refresh" content="0;url=https://elsewhere.example">',
                "",
            ),
            "links to stylesheets and other hosts": (
                '<link rel="dns-prefetch" href="//elsewhere.example">'
                '<link rel="stylesheet" href="/static/wagtailadmin/css/core.css">',
                "",
            ),
            "frames, forms and style sheets": (
                '<iframe src="https://elsewhere.example"></iframe>'
                '<form action="/"><input name="password"></form>'
                "<style>p { display: none; }</style>",
                "",
            ),
        }
        for label, (markup, expected) in cases.items():
            with self.subTest(label):
                self.assertEqual(sanitise_raw_html(markup), expected)

    def test_govuk_markup_and_drawings_are_kept_as_written(self):
        cases = {
            "govuk frontend": (
                '<div class="govuk-accordion" data-module="govuk-accordion" id="faq" '
                'aria-label="Questions"><button type="button">Open</button></div>'
            ),
            # Headings at every level: how many h1s a page has is the editor's call.
            "govuk typography": (
                '<span class="govuk-caption-xl">Caption</span>'
                '<h1 class="govuk-heading-xl">Heading</h1>'
                '<div class="govuk-panel govuk-panel--confirmation">'
                '<h1 class="govuk-panel__title">Done</h1></div>'
            ),
            "a start button keeps its arrow": (
                '<a href="/start" role="button" draggable="false" '
                'class="govuk-button govuk-button--start" data-module="govuk-button">'
                'Start now<svg class="govuk-button__start-icon" '
                'xmlns="http://www.w3.org/2000/svg" width="17.5" height="19" '
                'viewBox="0 0 33 40" aria-hidden="true" focusable="false">'
                '<path fill="currentColor" d="M0 0h13l20 20-20 20H0l20-20z"></path>'
                "</svg></a>"
            ),
            "a drawing with gradients, clipping, markers, filters and text": (
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100" '
                'role="img" aria-labelledby="chart-title">'
                '<title id="chart-title">Chart</title><desc>Two bars</desc><defs>'
                '<linearGradient id="g" x1="0" y2="1" gradientUnits="objectBoundingBox">'
                '<stop offset="0" stop-color="#1d70b8" stop-opacity="0.9"></stop>'
                "</linearGradient>"
                '<linearGradient id="g2" xlink:href="#g" gradientTransform="rotate(90)">'
                "</linearGradient>"
                '<clipPath id="c"><rect width="200" height="100" rx="4"></rect></clipPath>'
                '<marker id="m" markerWidth="6" markerHeight="6" refX="3" refY="3" '
                'orient="auto"><circle cx="3" cy="3" r="2"></circle></marker>'
                '<filter id="f"><feDropShadow dx="1" dy="1" stdDeviation="1" '
                'flood-opacity="0.4"></feDropShadow></filter>'
                '<symbol id="bar" viewBox="0 0 10 10"><rect width="10" height="10"></rect>'
                '</symbol><path id="curve" d="M10 90 Q100 10 190 90"></path></defs>'
                '<g clip-path="url(#c)" filter="url(#f)">'
                '<use href="#bar" x="20" y="40" width="40" height="60" fill="url(#g)"></use>'
                '<line x1="10" y1="95" x2="190" y2="95" stroke="#0b0c0c" '
                'stroke-dasharray="4 2" marker-end="url(#m)"></line></g>'
                '<text font-size="8" text-anchor="middle">'
                '<textPath href="#curve" startOffset="50%">Along the curve</textPath>'
                "</text></svg>"
            ),
        }
        for label, markup in cases.items():
            with self.subTest(label):
                self.assertEqual(sanitise_raw_html(markup), markup)

    def test_what_is_taken_out_of_markup_that_stays(self):
        cases = {
            "styles and handlers go, classes stay": (
                '<div class="app-callout" style="color: red" onclick="go()">Note</div>',
                '<div class="app-callout">Note</div>',
            ),
            "an icon cannot reach outside its drawing": (
                '<svg onload="go()"><script>go()</script>'
                '<use href="https://elsewhere.example/icons.svg#a"/>'
                "<foreignObject><p>Inside</p></foreignObject>"
                '<path d="M0 0" onclick="go()" style="fill: red"/></svg>',
                '<svg><use></use><path d="M0 0"></path></svg>',
            ),
            # A reference to another file loads it; one to an id in the page does not.
            "a drawing references only itself": (
                '<svg><use href="/media/icons.svg#a"></use>'
                '<use xlink:href="https://elsewhere.example/i.svg#a"></use>'
                '<rect fill="url(https://elsewhere.example/p.svg#g)" '
                "filter=\"url( '/f.svg#f')\" width=\"1\"></rect></svg>",
                '<svg><use></use><use></use><rect width="1"></rect></svg>',
            ),
            # <animate attributeName="href" to="javascript:..."> is a known way in.
            "animation cannot rewrite a link": (
                '<svg><a href="#top">'
                '<animate attributeName="href" to="javascript:go()"></animate>'
                '<set attributeName="href" to="javascript:go()"></set>'
                "<text>Top</text></a></svg>",
                '<svg><a href="#top"><text>Top</text></a></svg>',
            ),
            "a drawing cannot load images": (
                '<svg><image href="/media/a.png"></image><filter id="f">'
                '<feImage href="https://elsewhere.example/a.png"></feImage></filter></svg>',
                '<svg><filter id="f"></filter></svg>',
            ),
            "script urls go and links keep their rel": (
                '<a href="javascript:go()">Bad</a> '
                '<a href="/guidance/" rel="external" target="_blank">Good</a>',
                '<a>Bad</a> <a href="/guidance/" rel="external" target="_blank">Good</a>',
            ),
            "unclosed markup cannot take the page with it": (
                "</main><div><p>Unclosed",
                "<div><p>Unclosed</p></div>",
            ),
            "a table keeps its structure": (
                '<table><caption>Grades</caption><tr><th scope="row">SCS1</th>'
                '<td colspan="2">Deputy director</td></tr></table>',
                '<table><caption>Grades</caption><tbody><tr><th scope="row">SCS1</th>'
                '<td colspan="2">Deputy director</td></tr></tbody></table>',
            ),
        }
        for label, (markup, expected) in cases.items():
            with self.subTest(label):
                self.assertEqual(sanitise_raw_html(markup), expected)

    def test_saving_the_block_cleans_it(self):
        """So what the editor sees after saving is what is published."""
        cleaned = BodyRawHtmlBlock().clean(
            '<p class="app-note">Kept.</p><script>alert(1)</script>'
        )

        self.assertEqual(str(cleaned), '<p class="app-note">Kept.</p>')


@override_settings(FEATURE_FLAGS=FRAMEWORK_FLAGS)
class ContentPageTableRenderingTests(_PageTestCase):
    def _render(self, blocks, slug="contextual-challenges") -> str:
        return self._html(self._publish([_text("<p>Before the table.</p>"), *blocks], slug=slug))

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
        html = self._render(_table([["a", "b"]], caption="Indicative grades"))

        self.assertIn(
            '<caption class="govuk-table__caption govuk-table__caption--m">'
            "Indicative grades</caption>",
            html,
        )
        self.assertIn('aria-label="Indicative grades"', html)

    def test_a_table_with_no_caption_still_names_its_region(self):
        """A region with no accessible name is a region a screen reader skips."""
        self.assertIn('aria-label="Table"', self._render(_table([["a", "b"]])))

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
        html = self._render(
            [
                _text("<p>Above the table.</p>"),
                *_table([["a", "b"]]),
                _text("<p>Below the table.</p>"),
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

    # Widths are a <col> per column, so they apply with or without a header row.

    def test_each_width_is_a_col_of_its_own(self):
        html = self._render(
            _table([["a", "b", "c"]], widths=["one-half", "auto", "one-quarter"])
        )

        self.assertIn(
            '<colgroup><col class="app-table-col--one-half"><col>'
            '<col class="app-table-col--one-quarter"></colgroup>',
            html.replace("\n", "").replace("    ", ""),
        )

    def test_no_colgroup_without_a_width_to_set(self):
        for index, widths in enumerate(((), ("auto", "auto"))):
            with self.subTest(widths=widths):
                html = self._render(_table([["a", "b"]], widths=widths), slug=f"widths-{index}")

                self.assertNotIn("<colgroup>", html)

    def test_widths_beyond_the_last_column_are_left_out(self):
        """An import writes stream data without the validation that refuses them."""
        html = self._render(
            _table([["a", "b"]], widths=["one-quarter", "one-quarter", "one-half"])
        )

        self.assertEqual(html.count("<col "), 2)
        self.assertNotIn("app-table-col--one-half", html)

    def test_a_stored_width_cannot_reach_the_class_attribute(self):
        html = self._render(_table([["a", "b"]], widths=['half" onclick="alert(1)']))

        self.assertNotIn("onclick", html)
        self.assertNotIn("<colgroup>", html)

    def test_a_table_stored_before_widths_still_renders(self):
        """The grid's value on its own, as every table was saved before."""
        html = self._render(
            [{"type": "table", "value": _grid([["Grade", "Level"]], header_row=True)}]
        )

        self.assertIn('<table class="govuk-table">', html)
        self.assertIn("Grade", html)
        self.assertNotIn("<colgroup>", html)


class TableBlockTests(SimpleTestCase):
    def test_the_grid_offers_alignment_and_merging(self):
        options = GovukTableBlock().table_options

        self.assertIn("alignment", options["contextMenu"])
        self.assertIn("mergeCells", options["contextMenu"])
        self.assertIs(options["mergeCells"], True)
        # On top of Wagtail's own menu, not instead of it.
        self.assertIn("row_above", options["contextMenu"])
        self.assertEqual(options["language"], "en-US")

    def test_widths_that_fit_are_accepted(self):
        cases = (
            ["one-half", "one-quarter", "one-quarter"],
            # 1/3 + 1/3 + 1/3 is not quite 1 in floating point.
            ["one-third", "one-third", "one-third"],
        )
        for widths in cases:
            with self.subTest(widths=widths):
                block = SizedTableBlock()
                block.clean(block.to_python(_table_value([["a", "b", "c"]], widths=widths)))

    def test_widths_that_do_not_fit_are_refused(self):
        cases = (
            ([["a", "b"]], ["one-quarter", "one-quarter", "one-quarter"], "The table has 2 columns"),
            ([["a", "b", "c"]], ["one-half", "one-half", "one-quarter"], "more than the whole table"),
        )
        for data, widths, message in cases:
            with self.subTest(message):
                block = SizedTableBlock()
                with self.assertRaises(StructBlockValidationError) as raised:
                    block.clean(block.to_python(_table_value(data, widths=widths)))

                self.assertIn("column_widths", raised.exception.block_errors)
                self.assertIn(
                    message,
                    str(raised.exception.block_errors["column_widths"].non_block_errors),
                )

    def test_a_table_stored_before_widths_reads_as_one_without_them(self):
        value = SizedTableBlock().to_python(_grid([["a", "b"]]))

        self.assertEqual(value["table"]["data"], [["a", "b"]])
        self.assertEqual(list(value["column_widths"]), [])

    def test_it_is_stored_with_its_widths_from_then_on(self):
        block = SizedTableBlock()
        stored = block.get_prep_value(block.to_python(_grid([["a", "b"]])))

        self.assertEqual(stored["table"]["data"], [["a", "b"]])
        self.assertEqual(stored["column_widths"], [])


class RawHtmlTableScrollTests(SimpleTestCase):
    """The other route a table arrives by: a Raw HTML block.

    main.css has carried the scrollable region since f29e990 and nothing ever
    applied the class, so a hand-written table still dragged the page sideways.
    The block's template applies it through this function.
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
        """Wrapping on sight would nest one scrolling region inside another.

        The contextual challenges page hand-wrote the wrapper round six of its
        tables, and the keyboard would get two stops to reach each.
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


class ContentPageBodyAdminTests(_PageTestCase):
    """The blocks reach a page through the form the admin serves.

    A StreamField's inputs are built by its JavaScript, so they are not in the
    served HTML and are written here as the browser would send them.
    """

    def setUp(self):
        super().setUp()
        user = get_user_model().objects.create_superuser(
            username="editor", email="editor@example.gov.uk", password="unused"
        )
        self.client.force_login(user)
        self.page = self._publish([])

    def _post(self, blocks):
        edit_url = reverse("wagtailadmin_pages:edit", args=[self.page.pk])
        data = querydict_from_html(
            self.client.get(edit_url).content.decode(), form_id="page-edit-form"
        )
        data.update(nested_form_data({"body": streamfield(blocks)}))
        data["action-publish"] = "action-publish"
        return self.client.post(edit_url, data=data)

    def _banner_form(self, **overrides) -> dict:
        value = {
            "heading": "Join the community",
            "text": rich_text("<p>Meet people.</p>"),
            "button_text": "Sign up",
            "button_page": "",
            "button_external_url": "https://example.gov.uk/join",
            "style": "brand",
        }
        value.update(overrides)
        return value

    def _post_table(self, widths):
        table = {
            # The grid posts its value as JSON in a hidden input, with the
            # header choice its editor always sends.
            "table": json.dumps(
                {
                    **_grid([["Grade", "Level"], ["SCS1", "Director"]]),
                    "table_header_choice": "row",
                }
            ),
            "column_widths": _list_block(widths),
        }
        return self._post([("table", table)])

    def _body(self):
        return ContentPage.objects.get(pk=self.page.pk).body

    def test_text_cards_and_a_banner_can_be_published(self):
        card = {
            "title": "Alpha card",
            "image": "",
            "image_fit": "cover",
            "text": rich_text("<p>Alpha summary</p>"),
            "link": {"title": "More", "page": "", "external_url": ""},
            "tags": _list_block([]),
        }

        response = self._post(
            [
                ("text", rich_text("<p>Intro.</p>")),
                ("cards", {"heading": "Services", "cards": _list_block([card])}),
                ("banner", self._banner_form()),
            ]
        )

        self.assertEqual(response.status_code, 302)
        body = self._body()
        self.assertEqual([block.block_type for block in body], ["text", "cards", "banner"])
        self.assertEqual(body[1].value["cards"][0]["title"], "Alpha card")

    def test_raw_html_can_be_published(self):
        response = self._post([("raw_html", "<p class=\"app-note\">Hand written.</p>")])

        self.assertEqual(response.status_code, 302)
        body = self._body()
        self.assertEqual([block.block_type for block in body], ["raw_html"])
        self.assertEqual(str(body[0].value), '<p class="app-note">Hand written.</p>')

    def test_raw_html_is_stored_cleaned(self):
        self._post(
            [("raw_html", '<p onclick="go()">Hand written.</p><script>go()</script>')]
        )

        self.assertEqual(str(self._body()[0].value), "<p>Hand written.</p>")

    def test_a_banner_button_with_nowhere_to_go_is_refused(self):
        response = self._post([("banner", self._banner_form(button_external_url=""))])

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Choose a page or enter a URL for the button.")
        self.assertEqual(len(self._body()), 0)

    def test_column_widths_can_be_published(self):
        response = self._post_table(["one-quarter", "three-quarters"])

        self.assertEqual(response.status_code, 302)
        table = self._body()[0].value
        self.assertEqual(list(table["column_widths"]), ["one-quarter", "three-quarters"])
        self.assertEqual(table["table"]["data"][1], ["SCS1", "Director"])

    def test_widths_for_columns_the_table_does_not_have_are_refused(self):
        response = self._post_table(["one-quarter", "one-quarter", "one-quarter"])

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "The table has 2 columns")


class _ImportExportTestCase(_PageTestCase):
    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_superuser(
            username="cutover", email="cutover@example.gov.uk", password="unused"
        )

    def _round_trip(self, page) -> ContentPage:
        """Export the page, blank it, import the file back, and reload it."""
        payload = build_page_export_payload(site=self.site, pages=[page], user=self.user)
        page.body = []
        page.save_revision().publish()

        import_pages_from_payload(payload=payload, site=self.site, user=self.user)

        return ContentPage.objects.get(pk=page.pk)


class ContentPageCardImportExportTests(_ImportExportTestCase):
    def test_a_tagged_card_row_survives_an_export_and_import(self):
        tag = GovukTag.objects.create(slug="alpha", name="Alpha")

        reloaded = self._round_trip(self._publish([_cards(_card("Alpha card", tags=[tag]))]))

        [card] = reloaded.body[0].value["cards"]
        self.assertEqual(card["title"], "Alpha card")
        self.assertEqual(list(card["tags"]), [tag])

    def test_card_tags_named_in_the_file_are_resolved_to_this_sites_tags(self):
        card = _item(
            {
                "title": "Tagged card",
                "image": None,
                "image_fit": "cover",
                "text": "",
                "link": {"title": "More", "page": None, "external_url": ""},
                "tags": [_item("New tag")],
            }
        )
        payload = {
            "format": PAGE_EXPORT_FORMAT,
            "tags": [{"slug": "new-tag", "name": "New tag"}],
            "pages": [
                {
                    "model": "govuk.ContentPage",
                    "settings": {"slug": "tagged", "title": "Tagged"},
                    "fields": {
                        "body": [{"type": "cards", "value": {"heading": "", "cards": [card]}}]
                    },
                }
            ],
        }

        import_pages_from_payload(payload=payload, site=self.site, user=self.user)

        page = ContentPage.objects.get(slug="tagged")
        raw_tag = page.body.raw_data[0]["value"]["cards"][0]["value"]["tags"][0]
        self.assertEqual(GovukTag.objects.get(pk=raw_tag["value"]).slug, "new-tag")


@override_settings(FEATURE_FLAGS=FRAMEWORK_FLAGS)
class ContentPageTableImportExportTests(_ImportExportTestCase):
    """A table has to survive a round trip through an export file."""

    def setUp(self):
        super().setUp()
        self.page = self._publish(
            [
                _text("<p>Before the table.</p>"),
                *_table([["Grade", "Level"]], header_row=True),
            ]
        )

    def _block_types(self):
        return [block.block_type for block in ContentPage.objects.get(pk=self.page.pk).body]

    def test_a_table_survives_an_export_and_import(self):
        reloaded = self._round_trip(self.page)

        self.assertEqual([block.block_type for block in reloaded.body], ["text", "table"])

    def test_a_file_from_before_block_bodies_is_refused_whole(self):
        """Its HTML would arrive as an empty stream and blank the page."""
        payload = {
            "format": PAGE_EXPORT_FORMAT,
            "pages": [
                {
                    "model": "govuk.ContentPage",
                    "settings": {"slug": self.page.slug, "title": self.page.title},
                    "fields": {"body": "<p>Rewritten by the old file.</p>"},
                }
            ],
        }

        result = import_pages_from_payload(payload=payload, site=self.site, user=self.user)

        self.assertTrue(
            any("exported before page content became blocks" in e for e in result.errors),
            result.errors,
        )
        self.assertEqual(self._block_types(), ["text", "table"])

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

        result = import_pages_from_payload(payload=payload, site=self.site, user=self.user)

        self.assertTrue(result.errors)
        self.assertFalse(ContentPage.objects.filter(slug="new-page").exists())
