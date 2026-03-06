from django.test import TestCase
from wagtail.models import Site

from govuk.models import GovukTag, SectionPage


class SectionPageTagOptionsTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root_page = self.site.root_page.specific
        self.alpha_tag = GovukTag.objects.create(slug="alpha", name="Alpha")
        self.beta_tag = GovukTag.objects.create(slug="beta", name="Beta")

    def _rows_with_two_tagged_cards(self):
        return [
            (
                "row",
                {
                    "heading": "Services",
                    "cards": [
                        {
                            "title": "Alpha card",
                            "image": None,
                            "image_fit": "cover",
                            "text": "<p>Alpha summary</p>",
                            "link": {},
                            "tags": [self.alpha_tag],
                        },
                        {
                            "title": "Beta card",
                            "image": None,
                            "image_fit": "cover",
                            "text": "<p>Beta summary</p>",
                            "link": {},
                            "tags": [self.beta_tag],
                        },
                    ],
                },
            )
        ]

    def test_enable_tag_filter_filters_section_cards(self):
        page = self.root_page.add_child(
            instance=SectionPage(
                title="Get involved",
                slug="get-involved",
                rows=self._rows_with_two_tagged_cards(),
                enable_tag_filter=True,
                free_text="",
            )
        )
        page.save_revision().publish()

        response = self.client.get(page.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Filter by tag")
        self.assertContains(response, "Alpha card")
        self.assertContains(response, "Beta card")

        filtered_response = self.client.get(page.url, {"tag": "alpha"})
        self.assertEqual(filtered_response.status_code, 200)
        self.assertContains(filtered_response, "Alpha card")
        self.assertNotContains(filtered_response, "Beta card")

    def test_enable_tag_display_controls_card_tag_labels(self):
        page = self.root_page.add_child(
            instance=SectionPage(
                title="Services",
                slug="services",
                rows=self._rows_with_two_tagged_cards(),
                enable_tag_filter=False,
                enable_tag_display=False,
                free_text="",
            )
        )
        page.save_revision().publish()

        response_without_tag_display = self.client.get(page.url)
        self.assertEqual(response_without_tag_display.status_code, 200)
        self.assertNotContains(
            response_without_tag_display,
            '<strong class="govuk-tag govuk-tag--grey">Alpha</strong>',
            html=True,
        )

        page.enable_tag_display = True
        page.save_revision().publish()

        response_with_tag_display = self.client.get(page.url)
        self.assertEqual(response_with_tag_display.status_code, 200)
        self.assertContains(
            response_with_tag_display,
            '<strong class="govuk-tag govuk-tag--grey">Alpha</strong>',
            html=True,
        )
