"""A content page body built from blocks: text, cards and banners in any order.

The body is one stream an editor arranges, so the page has to lay out whatever
order it is given. Where the page has no sidebar a card row or banner runs
across the full width, as a section page's rows do, with the text between them
in the usual column; beside heading navigation or the role navigation there is
no room to break out, and every block stays in the column.
"""

import re
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from wagtail.blocks import StructBlockValidationError
from wagtail.models import Site
from wagtail.test.utils.form_data import (
    nested_form_data,
    querydict_from_html,
    rich_text,
    streamfield,
)

from govuk.models import ContentPage, FrameworkContentPage, GovukTag
from govuk.models.blocks import BannerBlock, BodyRawHtmlBlock
from govuk.models.raw_html import sanitise_raw_html
from govuk.page_import_export import (
    PAGE_EXPORT_FORMAT,
    build_page_export_payload,
    import_pages_from_payload,
)
from govuk.search_backend import search_backend
from govuk.tests.framework_helpers import make_framework_main_page


def _feature_flags() -> dict[str, bool]:
    return {
        "SKILLS": True,
        "FEEDBACK": False,
    }


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


class _PageTestCase(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root = self.site.root_page.specific

    def _publish(self, blocks, **fields) -> ContentPage:
        page = self.root.add_child(
            instance=ContentPage(
                title="Get involved", slug="get-involved", body=blocks, **fields
            )
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
        """The policy blocks style attributes, so the colour is a style sheet
        with the response's nonce, and the nonce is in the policy."""
        page = self._publish(
            [
                {
                    "type": "banner",
                    "value": _banner(style="custom", colour="#ca357c"),
                }
            ]
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

    @override_settings(FEATURE_FLAGS=_feature_flags())
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


@override_settings(FEATURE_FLAGS=_feature_flags())
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


class BannerBlockTests(SimpleTestCase):
    def _value(self, **overrides):
        block = BannerBlock()
        return block, block.to_python(_banner(**overrides))

    def test_a_button_needs_somewhere_to_go(self):
        block, value = self._value(button_external_url="")

        with self.assertRaises(StructBlockValidationError) as raised:
            block.clean(value)
        self.assertIn("button_page", raised.exception.block_errors)

    def test_somewhere_to_go_needs_button_text(self):
        block, value = self._value(button_text="")

        with self.assertRaises(StructBlockValidationError) as raised:
            block.clean(value)
        self.assertIn("button_text", raised.exception.block_errors)

    def test_a_banner_may_have_no_button(self):
        block, value = self._value(button_text="", button_external_url="")

        block.clean(value)
        html = block.render(value, context={"full_width": True})

        self.assertNotIn("govuk-button", html)

    def test_a_brand_banner_has_an_inverse_button(self):
        block, value = self._value()

        html = block.render(value, context={"full_width": True})

        self.assertIn("govuk-button govuk-button--inverse", html)
        self.assertIn('href="https://example.gov.uk/join"', html)

    def test_a_light_banner_has_a_plain_button(self):
        block, value = self._value(style="light")

        html = block.render(value, context={"full_width": True})

        self.assertIn("body-banner--light", html)
        self.assertNotIn("govuk-button--inverse", html)

    def test_a_stored_style_cannot_reach_the_class_attribute(self):
        """A page import writes stream data without running the block's validation."""
        block, value = self._value(style='light" onclick="alert(1)')

        html = block.render(value, context={"full_width": True})

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

    def test_a_banner_needs_a_heading_text_or_html(self):
        block, value = self._value(heading="", text="", raw_html="")

        with self.assertRaises(StructBlockValidationError) as raised:
            block.clean(value)
        self.assertIn("heading", raised.exception.block_errors)

    def test_raw_html_sits_between_the_text_and_the_button(self):
        block, value = self._value(raw_html='<figure class="hero__logo">Crest</figure>')

        html = block.render(value, context={"full_width": True})

        self.assertLess(html.index("body-banner__text"), html.index("hero__logo"))
        self.assertLess(html.index("hero__logo"), html.index("govuk-button"))

    def test_raw_html_is_cleaned_when_rendered(self):
        """A page import writes stream data without running clean."""
        block, value = self._value(raw_html="<p>Kept</p><script>go()</script>")

        html = block.render(value, context={"full_width": True})

        self.assertIn("<p>Kept</p>", html)
        self.assertNotIn("<script>", html)

    def test_a_custom_style_needs_a_colour(self):
        block, value = self._value(style="custom", colour="")

        with self.assertRaises(StructBlockValidationError) as raised:
            block.clean(value)
        self.assertIn("colour", raised.exception.block_errors)

    def test_a_custom_colour_must_be_hex(self):
        block, value = self._value(style="custom", colour="pink")

        with self.assertRaises(StructBlockValidationError) as raised:
            block.clean(value)
        self.assertIn("colour", raised.exception.block_errors)

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
        block, value = self._value(style="custom", colour="#fd0")

        html = block.render(value, context={"full_width": True})

        self.assertIn("body-banner--colour-ffdd00", html)
        self.assertNotIn("body-banner--inverse", html)
        self.assertNotIn("govuk-button--inverse", html)

    def test_a_stored_colour_cannot_reach_the_style_sheet(self):
        """A page import writes stream data without running the block's validation."""
        block, value = self._value(
            style="custom", colour="red}body{display:none"
        )

        html = block.render(value, context={"full_width": True})

        self.assertNotIn("<style", html)
        self.assertNotIn("display:none", html)
        self.assertIn("body-banner--brand", html)

    def test_an_inverse_button_takes_the_background_as_its_text_colour(self):
        block, value = self._value(style="custom", colour="#ca357c")

        html = block.render(value, context={"full_width": True})

        self.assertIn(
            ".body-banner--colour-ca357c .govuk-button--inverse:not(:focus),"
            ".body-banner--colour-ca357c .govuk-button--inverse:hover,"
            ".body-banner--colour-ca357c .govuk-button--inverse:active"
            "{color:#ca357c}",
            html,
        )

    def test_a_plain_button_keeps_its_own_text_colour(self):
        block, value = self._value(style="custom", colour="#fd0")

        html = block.render(value, context={"full_width": True})

        self.assertNotIn(".govuk-button--inverse", html)


def _list_block(values) -> dict:
    """Form data for a ListBlock, which wagtail.test.utils has no helper for."""
    data: dict = {"count": len(values)}
    for index, value in enumerate(values):
        data[str(index)] = {"value": value, "deleted": "", "order": index, "id": ""}
    return data


class RawHtmlAllowlistTests(SimpleTestCase):
    """What a Raw HTML block keeps, and what it takes out.

    The content security policy already blocks most of what is removed; these
    are the things it does not cover, and the things it would block anyway.
    """

    def test_scripts_go_with_their_content(self):
        self.assertEqual(
            sanitise_raw_html("<script>alert(1)</script><p>Kept.</p>"), "<p>Kept.</p>"
        )

    def test_a_script_from_this_site_goes_too(self):
        """The policy allows this site's own scripts, the admin's among them."""
        self.assertEqual(
            sanitise_raw_html('<script src="/static/wagtailadmin/js/core.js"></script>'),
            "",
        )

    def test_a_refresh_cannot_send_readers_elsewhere(self):
        """The policy does not govern where a page navigates to."""
        self.assertEqual(
            sanitise_raw_html(
                '<meta http-equiv="refresh" content="0;url=https://elsewhere.example">'
            ),
            "",
        )

    def test_links_to_stylesheets_and_other_hosts_go(self):
        self.assertEqual(
            sanitise_raw_html(
                '<link rel="dns-prefetch" href="//elsewhere.example">'
                '<link rel="stylesheet" href="/static/wagtailadmin/css/core.css">'
            ),
            "",
        )

    def test_styles_and_handlers_go_and_classes_stay(self):
        self.assertEqual(
            sanitise_raw_html(
                '<div class="app-callout" style="color: red" onclick="go()">Note</div>'
            ),
            '<div class="app-callout">Note</div>',
        )

    def test_govuk_frontend_markup_stays(self):
        markup = (
            '<div class="govuk-accordion" data-module="govuk-accordion" id="faq" '
            'aria-label="Questions"><button type="button">Open</button></div>'
        )

        self.assertEqual(sanitise_raw_html(markup), markup)

    def test_govuk_typography_markup_stays(self):
        """Headings at every level: how many h1s a page has is the editor's call."""
        markup = (
            '<span class="govuk-caption-xl">Caption</span>'
            '<h1 class="govuk-heading-xl">Heading</h1>'
            '<div class="govuk-panel govuk-panel--confirmation">'
            '<h1 class="govuk-panel__title">Done</h1></div>'
        )

        self.assertEqual(sanitise_raw_html(markup), markup)

    def test_a_start_button_keeps_its_arrow(self):
        markup = (
            '<a href="/start" role="button" draggable="false" '
            'class="govuk-button govuk-button--start" data-module="govuk-button">'
            'Start now<svg class="govuk-button__start-icon" '
            'xmlns="http://www.w3.org/2000/svg" width="17.5" height="19" '
            'viewBox="0 0 33 40" aria-hidden="true" focusable="false">'
            '<path fill="currentColor" d="M0 0h13l20 20-20 20H0l20-20z"></path>'
            "</svg></a>"
        )

        self.assertEqual(sanitise_raw_html(markup), markup)

    def test_an_icon_cannot_reach_outside_its_drawing(self):
        self.assertEqual(
            sanitise_raw_html(
                '<svg onload="go()"><script>go()</script>'
                '<use href="https://elsewhere.example/icons.svg#a"/>'
                "<foreignObject><p>Inside</p></foreignObject>"
                '<path d="M0 0" onclick="go()" style="fill: red"/></svg>'
            ),
            '<svg><use></use><path d="M0 0"></path></svg>',
        )

    def test_a_drawing_with_gradients_clipping_markers_filters_and_text_stays(self):
        drawing = (
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
        )

        self.assertEqual(sanitise_raw_html(drawing), drawing)

    def test_a_drawing_references_only_itself(self):
        """A reference to another file loads it; one to an id in the page does not."""
        self.assertEqual(
            sanitise_raw_html(
                '<svg><use href="/media/icons.svg#a"></use>'
                '<use xlink:href="https://elsewhere.example/i.svg#a"></use>'
                '<rect fill="url(https://elsewhere.example/p.svg#g)" '
                "filter=\"url( '/f.svg#f')\" width=\"1\"></rect></svg>"
            ),
            '<svg><use></use><use></use><rect width="1"></rect></svg>',
        )

    def test_animation_cannot_rewrite_a_link(self):
        """<animate attributeName="href" to="javascript:..."> is a known way in."""
        self.assertEqual(
            sanitise_raw_html(
                '<svg><a href="#top">'
                '<animate attributeName="href" to="javascript:go()"></animate>'
                '<set attributeName="href" to="javascript:go()"></set>'
                "<text>Top</text></a></svg>"
            ),
            '<svg><a href="#top"><text>Top</text></a></svg>',
        )

    def test_a_drawing_cannot_load_images(self):
        self.assertEqual(
            sanitise_raw_html(
                '<svg><image href="/media/a.png"></image><filter id="f">'
                '<feImage href="https://elsewhere.example/a.png"></feImage></filter></svg>'
            ),
            '<svg><filter id="f"></filter></svg>',
        )

    def test_script_urls_go_and_links_keep_their_rel(self):
        self.assertEqual(
            sanitise_raw_html(
                '<a href="javascript:go()">Bad</a> '
                '<a href="/guidance/" rel="external" target="_blank">Good</a>'
            ),
            '<a>Bad</a> <a href="/guidance/" rel="external" target="_blank">Good</a>',
        )

    def test_unclosed_markup_cannot_take_the_page_with_it(self):
        self.assertEqual(
            sanitise_raw_html("</main><div><p>Unclosed"), "<div><p>Unclosed</p></div>"
        )

    def test_frames_forms_and_style_sheets_go(self):
        self.assertEqual(
            sanitise_raw_html(
                '<iframe src="https://elsewhere.example"></iframe>'
                '<form action="/"><input name="password"></form>'
                "<style>p { display: none; }</style>"
            ),
            "",
        )

    def test_a_table_keeps_its_structure(self):
        self.assertEqual(
            sanitise_raw_html(
                '<table><caption>Grades</caption><tr><th scope="row">SCS1</th>'
                '<td colspan="2">Deputy director</td></tr></table>'
            ),
            '<table><caption>Grades</caption><tbody><tr><th scope="row">SCS1</th>'
            '<td colspan="2">Deputy director</td></tr></tbody></table>',
        )

    def test_saving_the_block_cleans_it(self):
        """So what the editor sees after saving is what is published."""
        cleaned = BodyRawHtmlBlock().clean(
            '<p class="app-note">Kept.</p><script>alert(1)</script>'
        )

        self.assertEqual(str(cleaned), '<p class="app-note">Kept.</p>')


class RawHtmlRenderingTests(_PageTestCase):
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


class ContentPageBlockAdminTests(_PageTestCase):
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
        page = ContentPage.objects.get(pk=self.page.pk)
        self.assertEqual(
            [block.block_type for block in page.body],
            ["text", "cards", "banner"],
        )
        self.assertEqual(page.body[1].value["cards"][0]["title"], "Alpha card")

    def test_raw_html_can_be_published(self):
        response = self._post([("raw_html", "<p class=\"app-note\">Hand written.</p>")])

        self.assertEqual(response.status_code, 302)
        page = ContentPage.objects.get(pk=self.page.pk)
        self.assertEqual([block.block_type for block in page.body], ["raw_html"])
        self.assertEqual(
            str(page.body[0].value), '<p class="app-note">Hand written.</p>'
        )

    def test_raw_html_is_stored_cleaned(self):
        self._post(
            [("raw_html", '<p onclick="go()">Hand written.</p><script>go()</script>')]
        )

        page = ContentPage.objects.get(pk=self.page.pk)
        self.assertEqual(str(page.body[0].value), "<p>Hand written.</p>")

    def test_a_banner_button_with_nowhere_to_go_is_refused(self):
        response = self._post([("banner", self._banner_form(button_external_url=""))])

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Choose a page or enter a URL for the button.")
        self.assertEqual(len(ContentPage.objects.get(pk=self.page.pk).body), 0)


class ContentPageCardSearchTests(_PageTestCase):
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


class ContentPageCardImportExportTests(_PageTestCase):
    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_superuser(
            username="cutover", email="cutover@example.gov.uk", password="unused"
        )

    def test_a_tagged_card_row_survives_an_export_and_import(self):
        tag = GovukTag.objects.create(slug="alpha", name="Alpha")
        page = self._publish([_cards(_card("Alpha card", tags=[tag]))])
        payload = build_page_export_payload(site=self.site, pages=[page], user=self.user)

        page.body = []
        page.save_revision().publish()

        import_pages_from_payload(payload=payload, site=self.site, user=self.user)

        reloaded = ContentPage.objects.get(pk=page.pk)
        [card] = reloaded.body[0].value["cards"]
        self.assertEqual(card["title"], "Alpha card")
        self.assertEqual(list(card["tags"]), [tag])

    def test_card_tags_named_in_the_file_are_resolved_to_this_sites_tags(self):
        payload = {
            "format": PAGE_EXPORT_FORMAT,
            "tags": [{"slug": "new-tag", "name": "New tag"}],
            "pages": [
                {
                    "model": "govuk.ContentPage",
                    "settings": {"slug": "tagged", "title": "Tagged"},
                    "fields": {
                        "body": [
                            {
                                "type": "cards",
                                "value": {
                                    "heading": "",
                                    "cards": [
                                        _item(
                                            {
                                                "title": "Tagged card",
                                                "image": None,
                                                "image_fit": "cover",
                                                "text": "",
                                                "link": {
                                                    "title": "More",
                                                    "page": None,
                                                    "external_url": "",
                                                },
                                                "tags": [_item("New tag")],
                                            }
                                        )
                                    ],
                                },
                            }
                        ]
                    },
                }
            ],
        }

        import_pages_from_payload(payload=payload, site=self.site, user=self.user)

        page = ContentPage.objects.get(slug="tagged")
        raw_tag = page.body.raw_data[0]["value"]["cards"][0]["value"]["tags"][0]
        self.assertEqual(GovukTag.objects.get(pk=raw_tag["value"]).slug, "new-tag")


class BannerColourPolicyTests(SimpleTestCase):
    def test_the_admin_style_policy_has_no_nonce(self):
        """A nonce would make the browser ignore 'unsafe-inline', which the
        admin's own inline styles need."""
        from django.conf import settings
        from django.utils.csp import CSP

        self.assertIn(CSP.NONCE, settings.SECURE_CSP["style-src"])
        self.assertNotIn(CSP.NONCE, settings.SECURE_CSP_ADMIN["style-src"])
        self.assertIn(CSP.UNSAFE_INLINE, settings.SECURE_CSP_ADMIN["style-src"])
