"""A content page body built from blocks: text, cards and banners in any order.

The body is one stream an editor arranges, so the page has to lay out whatever
order it is given. Where the page has no sidebar a card row or banner runs
across the full width, as a section page's rows do, with the text between them
in the usual column; beside heading navigation or the role navigation there is
no room to break out, and every block stays in the column.
"""

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
from govuk.models.blocks import BannerBlock
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
        fields.setdefault("body", "")
        page = self.root.add_child(
            instance=ContentPage(
                title="Get involved", slug="get-involved", body_blocks=blocks, **fields
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

    def test_the_body_field_still_comes_first(self):
        html = self._html(
            self._publish(
                [_cards(_card("Alpha card"))], body="<p>The page body.</p>"
            )
        )

        self.assertLess(html.index("The page body."), html.index("Alpha card"))

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

        self.assertIn("body-banner body-banner--brand body-band", html)
        self.assertIn("Join the community", html)

    def test_a_page_holding_only_blocks_renders_them(self):
        html = self._html(self._publish([_text("<p>Only a block.</p>")]))

        self.assertIn("Only a block.", html)

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
                body="",
                body_blocks=[_cards(_card("Alpha card"))],
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


def _list_block(values) -> dict:
    """Form data for a ListBlock, which wagtail.test.utils has no helper for."""
    data: dict = {"count": len(values)}
    for index, value in enumerate(values):
        data[str(index)] = {"value": value, "deleted": "", "order": index, "id": ""}
    return data


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
        data.update(nested_form_data({"body_blocks": streamfield(blocks)}))
        data["body"] = rich_text("")
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
            [block.block_type for block in page.body_blocks],
            ["text", "cards", "banner"],
        )
        self.assertEqual(page.body_blocks[1].value["cards"][0]["title"], "Alpha card")

    def test_a_banner_button_with_nowhere_to_go_is_refused(self):
        response = self._post([("banner", self._banner_form(button_external_url=""))])

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Choose a page or enter a URL for the button.")
        self.assertEqual(len(ContentPage.objects.get(pk=self.page.pk).body_blocks), 0)


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

        page.body_blocks = []
        page.save_revision().publish()

        import_pages_from_payload(payload=payload, site=self.site, user=self.user)

        reloaded = ContentPage.objects.get(pk=page.pk)
        [card] = reloaded.body_blocks[0].value["cards"]
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
                        "body_blocks": [
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
        raw_tag = page.body_blocks.raw_data[0]["value"]["cards"][0]["value"]["tags"][0]
        self.assertEqual(GovukTag.objects.get(pk=raw_tag["value"]).slug, "new-tag")
