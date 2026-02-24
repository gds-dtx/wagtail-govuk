from django.test import TestCase
from wagtail.models import Site

from govuk.models import (
    ContentDiscoverySettings,
    ContentDiscoverySource,
    ContentPage,
    ExternalContentItem,
    ExternalContentItemTag,
    GovukTag,
    SectionPage,
    TagListingsPage,
)


class TagListingsPageQuerysetTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root_page = self.site.root_page.specific

        self.alpha_tag = GovukTag.objects.create(slug="alpha", name="Alpha")
        self.beta_tag = GovukTag.objects.create(slug="beta", name="Beta")
        self.gamma_tag = GovukTag.objects.create(slug="gamma", name="Gamma")

        self.listings_page = self.root_page.add_child(
            instance=TagListingsPage(title="Tagged listings", slug="tagged-listings")
        )
        self.listings_page.tags.add(self.alpha_tag, self.beta_tag)
        self.listings_page.save_revision().publish()
        self.listings_page = self.listings_page.specific

        self.content_page_alpha = self.root_page.add_child(
            instance=ContentPage(title="Alpha page", slug="alpha-page", body="")
        )
        self.content_page_alpha.tags.add(self.alpha_tag)
        self.content_page_alpha.save_revision().publish()

        self.section_page_beta = self.root_page.add_child(
            instance=SectionPage(
                title="Beta section",
                slug="beta-section",
                hero_intro="",
                rows=[],
                free_text="",
            )
        )
        self.section_page_beta.tags.add(self.beta_tag)
        self.section_page_beta.save_revision().publish()

        self.gamma_page = self.root_page.add_child(
            instance=ContentPage(title="Gamma page", slug="gamma-page", body="")
        )
        self.gamma_page.tags.add(self.gamma_tag)
        self.gamma_page.save_revision().publish()

        discovery_settings = ContentDiscoverySettings.for_site(self.site)
        self.source_one = ContentDiscoverySource.objects.create(
            settings=discovery_settings,
            sort_order=1,
            name="Source One",
            url="https://example.gov.uk/source-one.xml",
        )
        self.source_two = ContentDiscoverySource.objects.create(
            settings=discovery_settings,
            sort_order=2,
            name="Source Two",
            url="https://example.gov.uk/source-two.xml",
        )

        self.external_alpha = ExternalContentItem.objects.create(
            source=self.source_one,
            url="https://example.gov.uk/alpha",
            title="Alpha external",
            hidden=False,
        )
        ExternalContentItemTag.objects.create(
            content_object=self.external_alpha,
            tag=self.alpha_tag,
        )

        self.external_beta = ExternalContentItem.objects.create(
            source=self.source_two,
            url="https://example.gov.uk/beta",
            title="Beta external",
            hidden=False,
        )
        ExternalContentItemTag.objects.create(
            content_object=self.external_beta,
            tag=self.beta_tag,
        )

        self.external_gamma = ExternalContentItem.objects.create(
            source=self.source_one,
            url="https://example.gov.uk/gamma",
            title="Gamma external",
            hidden=False,
        )
        ExternalContentItemTag.objects.create(
            content_object=self.external_gamma,
            tag=self.gamma_tag,
        )

    def test_get_listing_queryset_combines_external_and_tagged_pages(self):
        items = self.listings_page.get_listing_queryset()
        urls = {item["url"] for item in items}

        self.assertIn(self.external_alpha.url, urls)
        self.assertIn(self.external_beta.url, urls)
        self.assertIn(self.content_page_alpha.url, urls)
        self.assertIn(self.section_page_beta.url, urls)

        self.assertNotIn(self.external_gamma.url, urls)
        self.assertNotIn(self.gamma_page.url, urls)

    def test_get_listing_queryset_applies_selected_tag_to_external_and_pages(self):
        items = self.listings_page.get_listing_queryset(selected_tag_id=self.beta_tag.id)
        urls = {item["url"] for item in items}

        self.assertEqual(urls, {self.external_beta.url, self.section_page_beta.url})

    def test_get_listing_queryset_applies_selected_source_to_external_only(self):
        items = self.listings_page.get_listing_queryset(
            selected_source_id=self.source_one.id
        )
        urls = {item["url"] for item in items}

        self.assertEqual(urls, {self.external_alpha.url})
        self.assertTrue(all(item["source"] is not None for item in items))

    def test_tag_filter_page_response_includes_external_and_internal_results(self):
        self.listings_page.enable_tag_filter = True
        self.listings_page.save_revision().publish()

        response = self.client.get(self.listings_page.url, {"tag": self.beta_tag.slug})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Beta external")
        self.assertContains(response, "Beta section")
        self.assertNotContains(response, "Alpha external")
        self.assertNotContains(response, "Alpha page")
