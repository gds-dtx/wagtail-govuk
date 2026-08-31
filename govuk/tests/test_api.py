from unittest.mock import patch

from django.test import TestCase, override_settings
from wagtail.models import Site

from govuk.api import DEFAULT_API_REPOSITORY_URL, _get_api_version
from govuk.models import (
    ContentDiscoverySettings,
    ContentDiscoverySource,
    ContentPage,
    ExternalContentItem,
    ExternalContentItemTag,
    GovukTag,
    RolePage,
)


def _feature_flags(*, skills_enabled: bool) -> dict[str, bool]:
    return {
        "SKILLS": skills_enabled,
        "ORGANISATIONS": False,
        "PEOPLE_FINDER": False,
        "FEEDBACK": False,
    }


class ApiMetaAssertionsMixin:
    def assert_api_meta(self, payload):
        self.assertIn("meta", payload)
        self.assertEqual(payload["meta"]["repository_url"], DEFAULT_API_REPOSITORY_URL)
        self.assertEqual(payload["meta"]["version"], _get_api_version())


class ApiRootAndWagtailEndpointTests(ApiMetaAssertionsMixin, TestCase):
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
        self.assertTrue(body["endpoints"]["health"].endswith("/api/health/"))
        self.assert_api_meta(body)

    def test_pages_listing_is_available_without_v2_prefix(self):
        response = self.client.get("/api/pages/")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assert_api_meta(body)
        self.assertIn("items", body)

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
        self.assert_api_meta(body)


class PagesApiSerializerTests(ApiMetaAssertionsMixin, TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root_page = self.site.root_page.specific
        self.tag_alpha = GovukTag.objects.create(slug="alpha", name="Alpha")
        self.tag_beta = GovukTag.objects.create(slug="beta", name="Beta")

    def _page_item_by_slug(self, *, slug: str) -> dict:
        response = self.client.get("/api/pages/", {"type": "govuk.ContentPage"})
        self.assertEqual(response.status_code, 200)
        items = response.json()["items"]
        for item in items:
            if item["meta"]["slug"] == slug:
                return item
        self.fail(f"Could not find page item with slug '{slug}'.")

    def test_pages_api_uses_hero_title_hero_intro_and_tag_slugs(self):
        page = self.root_page.add_child(
            instance=ContentPage(
                title="Fallback page title",
                slug="pages-api-hero-values",
                hero_title="Hero page title",
                hero_intro="<p>Hero page intro</p>",
                search_description="Meta page summary",
                body="",
            )
        )
        page.tags.add(self.tag_beta, self.tag_alpha)
        page.save_revision().publish()

        page_item = self._page_item_by_slug(slug="pages-api-hero-values")

        self.assertEqual(page_item["title"], "Hero page title")
        self.assertEqual(page_item["description"], "Hero page intro")
        self.assertEqual(page_item["tags"], ["alpha", "beta"])

    def test_pages_api_uses_hero_fields_in_generic_listing(self):
        page = self.root_page.add_child(
            instance=ContentPage(
                title="Generic listing fallback title",
                slug="pages-api-generic-listing-hero-values",
                hero_title="Generic listing hero title",
                hero_intro="<p>Generic listing hero intro</p>",
                search_description="Generic listing meta summary",
                body="",
            )
        )
        page.save_revision().publish()

        response = self.client.get("/api/pages/")
        self.assertEqual(response.status_code, 200)
        page_item = next(
            (
                item
                for item in response.json()["items"]
                if item["meta"]["slug"] == "pages-api-generic-listing-hero-values"
            ),
            None,
        )
        self.assertIsNotNone(page_item)
        self.assert_api_meta(response.json())
        self.assertEqual(page_item["title"], "Generic listing hero title")
        self.assertEqual(page_item["description"], "Generic listing hero intro")

    def test_pages_api_description_falls_back_to_meta_then_null(self):
        meta_page = self.root_page.add_child(
            instance=ContentPage(
                title="Meta description page",
                slug="pages-api-meta-description",
                hero_title="",
                hero_intro="",
                search_description="Meta-only summary",
                body="",
            )
        )
        meta_page.save_revision().publish()

        empty_page = self.root_page.add_child(
            instance=ContentPage(
                title="No description page",
                slug="pages-api-no-description",
                hero_title="",
                hero_intro="",
                search_description="",
                body="",
            )
        )
        empty_page.save_revision().publish()

        meta_item = self._page_item_by_slug(slug="pages-api-meta-description")
        empty_item = self._page_item_by_slug(slug="pages-api-no-description")

        self.assertEqual(meta_item["title"], "Meta description page")
        self.assertEqual(meta_item["description"], "Meta-only summary")
        self.assertEqual(meta_item["tags"], [])
        self.assertEqual(empty_item["description"], None)
        self.assertEqual(empty_item["tags"], [])

    def test_pages_api_merges_tags_with_settings_tagged_items(self):
        page = self.root_page.add_child(
            instance=ContentPage(
                title="Merged tag page",
                slug="pages-api-merged-tags",
                body="",
            )
        )
        page.tags.add(self.tag_alpha)
        page.tagged_items.create(tag=self.tag_beta)
        page.save_revision().publish()

        response = self.client.get("/api/pages/")
        self.assertEqual(response.status_code, 200)
        self.assert_api_meta(response.json())
        page_item = next(
            (
                item
                for item in response.json()["items"]
                if item["meta"]["slug"] == "pages-api-merged-tags"
            ),
            None,
        )
        self.assertIsNotNone(page_item)
        self.assertEqual(page_item["tags"], ["alpha", "beta"])

    def test_pages_detail_includes_common_api_meta(self):
        page = self.root_page.add_child(
            instance=ContentPage(
                title="Page detail metadata",
                slug="pages-api-detail-meta",
                body="",
            )
        )
        page.save_revision().publish()

        response = self.client.get(f"/api/pages/{page.id}/")
        self.assertEqual(response.status_code, 200)
        self.assert_api_meta(response.json())


class PagesApiWithoutTheFrameworkTests(TestCase):
    """The pages API is public: ``WagtailPages`` sets ``permission_classes``.

    ``AuthenticatedAPIViewSetMixin`` would require a login, but ``AllowAny``
    overrides it, so anything this endpoint lists is published to anyone who
    asks. A ``RolePage`` that reached a site without the framework through the
    page import 404s when fetched, so listing it would publish a set of titles
    and detail URLs that none of them open.
    """

    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root_page = self.site.root_page.specific

        self.content_page = self.root_page.add_child(
            instance=ContentPage(title="Ordinary page", slug="ordinary-page", body="")
        )
        self.content_page.save_revision().publish()

        self.role_page = self.root_page.add_child(
            instance=RolePage(title="Data analyst", slug="data-analyst", body="")
        )
        self.role_page.save_revision().publish()

    def _listed_slugs(self, params=None):
        response = self.client.get("/api/pages/", params or {})
        self.assertEqual(response.status_code, 200)
        return {item["meta"]["slug"] for item in response.json()["items"]}

    @override_settings(FEATURE_FLAGS=_feature_flags(skills_enabled=True))
    def test_the_framework_site_still_lists_its_role_pages(self):
        self.assertIn("data-analyst", self._listed_slugs())
        self.assertEqual(
            self.client.get(f"/api/pages/{self.role_page.id}/").status_code, 200
        )

    @override_settings(FEATURE_FLAGS=_feature_flags(skills_enabled=False))
    def test_a_role_page_is_not_published_by_the_listing(self):
        slugs = self._listed_slugs()

        self.assertNotIn("data-analyst", slugs)
        self.assertIn("ordinary-page", slugs)

    @override_settings(FEATURE_FLAGS=_feature_flags(skills_enabled=False))
    def test_asking_for_the_type_by_name_returns_nothing_rather_than_the_pages(self):
        """The type filter is the obvious way to go looking for them."""
        self.assertEqual(self._listed_slugs({"type": "govuk.RolePage"}), set())

    @override_settings(FEATURE_FLAGS=_feature_flags(skills_enabled=False))
    def test_the_detail_route_does_not_serve_one_either(self):
        """``get_queryset`` backs the detail view too, so this is one guard."""
        self.assertEqual(
            self.client.get(f"/api/pages/{self.role_page.id}/").status_code, 404
        )


class ExternalContentApiTests(ApiMetaAssertionsMixin, TestCase):
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

    def test_a_filter_int_cannot_read_matches_nothing_rather_than_raising(self):
        """"²" is a digit to str.isdigit and not a number to int.

        The filters parsed the one and tested with the other, so "?tag=²"
        answered 500 where "?tag=nonsense" already answered an empty list.
        """
        for url in ("/api/externalcontent/items/", "/api/externalcontent/sources/"):
            for parameter in ("tag", "source"):
                with self.subTest(url=url, parameter=parameter):
                    response = self.client.get(url, {parameter: "²"})

                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json()["items"], [])

    def test_a_filter_of_more_digits_than_int_reads_matches_nothing(self):
        """Reading the digits is not enough on its own to reach ``int``.

        Python refuses a run of more than 4,300 digits, so a filter of 4,301
        of them is decimal all the way down and was still a 500 -- on
        PostgreSQL as much as on SQLite, since ``int`` objects before either
        engine is asked anything.
        """
        for url in ("/api/externalcontent/items/", "/api/externalcontent/sources/"):
            for parameter in ("tag", "source"):
                with self.subTest(url=url, parameter=parameter):
                    response = self.client.get(url, {parameter: "1" * 4301})

                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json()["items"], [])

    def test_a_filter_larger_than_a_row_id_matches_nothing(self):
        """SQLite raises rather than matching nothing past the largest id."""
        for url in ("/api/externalcontent/items/", "/api/externalcontent/sources/"):
            for parameter in ("tag", "source"):
                with self.subTest(url=url, parameter=parameter):
                    response = self.client.get(url, {parameter: str(2**63)})

                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json()["items"], [])

    def test_a_nul_in_a_filter_is_dropped_rather_than_sent_to_the_database(self):
        """PostgreSQL refuses a string literal carrying a NUL outright.

        SQLite takes it and matches nothing, which is why the tests and CI
        were quiet while "?tag=%00" answered 500 on dev and production. The
        NUL leaves an empty filter, which is the filter that was always asked
        for and means no filter at all.
        """
        for url in ("/api/externalcontent/items/", "/api/externalcontent/sources/"):
            unfiltered = self.client.get(url).json()["items"]
            for parameter in ("tag", "source"):
                with self.subTest(url=url, parameter=parameter):
                    response = self.client.get(url, {parameter: "\x00"})

                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json()["items"], unfiltered)

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
        self.assert_api_meta(sources_payload)
        self.assertEqual(sources_payload["meta"]["total_count"], 18)
        self.assertEqual(len(sources_payload["items"]), 15)
        self.assertIsNotNone(sources_payload["meta"]["next"])

        sources_page_two = self.client.get("/api/externalcontent/sources/", {"page": 2})
        self.assertEqual(sources_page_two.status_code, 200)
        self.assertEqual(len(sources_page_two.json()["items"]), 3)

        items_page_one = self.client.get("/api/externalcontent/items/")
        self.assertEqual(items_page_one.status_code, 200)
        items_payload = items_page_one.json()
        self.assert_api_meta(items_payload)
        self.assertEqual(items_payload["meta"]["total_count"], 19)
        self.assertEqual(len(items_payload["items"]), 15)
        self.assertIsNotNone(items_payload["meta"]["next"])

        items_page_two = self.client.get("/api/externalcontent/items/", {"page": 2})
        self.assertEqual(items_page_two.status_code, 200)
        self.assertEqual(len(items_page_two.json()["items"]), 4)


class HealthApiTests(ApiMetaAssertionsMixin, TestCase):
    def test_health_endpoint_reports_database_ok(self):
        response = self.client.get("/api/health/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["health"]["database"], "ok")
        self.assert_api_meta(payload)

    def test_health_endpoint_returns_503_when_database_check_fails(self):
        with patch("govuk.api._check_database_health", return_value=False):
            response = self.client.get("/api/health/")
        self.assertEqual(response.status_code, 503)
        payload = response.json()
        self.assertEqual(payload["health"]["database"], "error")
        self.assert_api_meta(payload)
