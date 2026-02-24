from django.test import TestCase
from wagtail.models import Site

from govuk.models import (
    ContentDiscoverySettings,
    ContentDiscoverySource,
    ExternalContentItem,
    ExternalContentItemTag,
    GovukTag,
)


class ApiRootAndWagtailEndpointTests(TestCase):
    def test_api_root_lists_versionless_wagtail_and_externalcontent_endpoints(self):
        response = self.client.get("/api/")

        self.assertEqual(response.status_code, 200)
        body = response.json()

        self.assertNotIn("versions", body)
        self.assertIn("endpoints", body)
        self.assertTrue(body["endpoints"]["pages"]["listing"].endswith("/api/pages/"))
        self.assertTrue(
            body["endpoints"]["externalcontent"]["sources"].endswith(
                "/api/externalcontent/sources/"
            )
        )
        self.assertTrue(
            body["endpoints"]["externalcontent"]["items"].endswith(
                "/api/externalcontent/items/"
            )
        )

    def test_pages_listing_is_available_without_v2_prefix(self):
        response = self.client.get("/api/pages/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("meta", response.json())
        self.assertIn("items", response.json())

    def test_externalcontent_root_lists_sources_and_items_urls(self):
        response = self.client.get("/api/externalcontent/")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("endpoints", body)
        self.assertTrue(
            body["endpoints"]["sources"].endswith("/api/externalcontent/sources/")
        )
        self.assertTrue(
            body["endpoints"]["items"].endswith("/api/externalcontent/items/")
        )


class ExternalContentApiTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.discovery_settings = ContentDiscoverySettings.for_site(self.site)

        self.tag_alpha = GovukTag.objects.create(slug="alpha", name="Alpha")
        self.tag_beta = GovukTag.objects.create(slug="beta", name="Beta")

        self.source_one = ContentDiscoverySource.objects.create(
            settings=self.discovery_settings,
            sort_order=1,
            name="Source One",
            url="https://example.gov.uk/source-one.xml",
        )
        self.source_two = ContentDiscoverySource.objects.create(
            settings=self.discovery_settings,
            sort_order=2,
            name="Source Two",
            url="https://example.gov.uk/source-two.xml",
        )

        self.item_alpha = ExternalContentItem.objects.create(
            source=self.source_one,
            url="https://example.gov.uk/articles/alpha",
            title="Alpha content",
            hidden=False,
        )
        ExternalContentItemTag.objects.create(
            content_object=self.item_alpha,
            tag=self.tag_alpha,
        )

        self.item_beta_source_two = ExternalContentItem.objects.create(
            source=self.source_two,
            url="https://example.gov.uk/articles/beta-two",
            title="Beta content source two",
            hidden=False,
        )
        ExternalContentItemTag.objects.create(
            content_object=self.item_beta_source_two,
            tag=self.tag_beta,
        )

        self.item_beta_source_one = ExternalContentItem.objects.create(
            source=self.source_one,
            url="https://example.gov.uk/articles/beta-one",
            title="Beta content source one",
            hidden=False,
        )
        ExternalContentItemTag.objects.create(
            content_object=self.item_beta_source_one,
            tag=self.tag_beta,
        )

        self.hidden_item = ExternalContentItem.objects.create(
            source=self.source_one,
            url="https://example.gov.uk/articles/hidden",
            title="Hidden content",
            hidden=True,
        )
        ExternalContentItemTag.objects.create(
            content_object=self.hidden_item,
            tag=self.tag_alpha,
        )

    def test_items_endpoint_supports_tag_and_source_filters(self):
        alpha_response = self.client.get("/api/externalcontent/items/", {"tag": "alpha"})
        self.assertEqual(alpha_response.status_code, 200)
        alpha_urls = {item["url"] for item in alpha_response.json()["items"]}
        self.assertEqual(alpha_urls, {self.item_alpha.url})

        source_response = self.client.get(
            "/api/externalcontent/items/",
            {"source": str(self.source_two.id)},
        )
        self.assertEqual(source_response.status_code, 200)
        source_urls = {item["url"] for item in source_response.json()["items"]}
        self.assertEqual(source_urls, {self.item_beta_source_two.url})

        combined_response = self.client.get(
            "/api/externalcontent/items/",
            {"tag": "beta", "source": str(self.source_one.id)},
        )
        self.assertEqual(combined_response.status_code, 200)
        combined_urls = {item["url"] for item in combined_response.json()["items"]}
        self.assertEqual(combined_urls, {self.item_beta_source_one.url})

    def test_sources_endpoint_supports_tag_and_source_filters(self):
        alpha_response = self.client.get("/api/externalcontent/sources/", {"tag": "alpha"})
        self.assertEqual(alpha_response.status_code, 200)
        alpha_ids = {item["id"] for item in alpha_response.json()["items"]}
        self.assertEqual(alpha_ids, {self.source_one.id})

        source_response = self.client.get(
            "/api/externalcontent/sources/",
            {"source": str(self.source_two.id)},
        )
        self.assertEqual(source_response.status_code, 200)
        source_ids = {item["id"] for item in source_response.json()["items"]}
        self.assertEqual(source_ids, {self.source_two.id})

        combined_response = self.client.get(
            "/api/externalcontent/sources/",
            {"tag": "beta", "source": str(self.source_one.id)},
        )
        self.assertEqual(combined_response.status_code, 200)
        combined_ids = {item["id"] for item in combined_response.json()["items"]}
        self.assertEqual(combined_ids, {self.source_one.id})

    def test_sources_and_items_endpoints_are_paginated(self):
        for idx in range(3, 19):
            ContentDiscoverySource.objects.create(
                settings=self.discovery_settings,
                sort_order=idx,
                name=f"Source {idx}",
                url=f"https://example.gov.uk/source-{idx}.xml",
            )

        for idx in range(1, 17):
            ExternalContentItem.objects.create(
                source=self.source_one,
                url=f"https://example.gov.uk/articles/page-{idx}",
                title=f"Paginated item {idx}",
                hidden=False,
            )

        sources_page_one = self.client.get("/api/externalcontent/sources/")
        self.assertEqual(sources_page_one.status_code, 200)
        sources_payload = sources_page_one.json()
        self.assertEqual(sources_payload["meta"]["total_count"], 18)
        self.assertEqual(len(sources_payload["items"]), 15)
        self.assertIsNotNone(sources_payload["meta"]["next"])

        sources_page_two = self.client.get("/api/externalcontent/sources/", {"page": 2})
        self.assertEqual(sources_page_two.status_code, 200)
        self.assertEqual(len(sources_page_two.json()["items"]), 3)

        items_page_one = self.client.get("/api/externalcontent/items/")
        self.assertEqual(items_page_one.status_code, 200)
        items_payload = items_page_one.json()
        self.assertEqual(items_payload["meta"]["total_count"], 19)
        self.assertEqual(len(items_payload["items"]), 15)
        self.assertIsNotNone(items_payload["meta"]["next"])

        items_page_two = self.client.get("/api/externalcontent/items/", {"page": 2})
        self.assertEqual(items_page_two.status_code, 200)
        self.assertEqual(len(items_page_two.json()["items"]), 4)
