from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from wagtail.models import Site

from govuk.models import (
    ContentPage,
    ContentDiscoverySettings,
    ContentDiscoverySource,
    ExternalContentItem,
    GovukTag,
    SectionPage,
    TagListingsPage,
)
from govuk.search_backend import search_backend


class SearchBackendExternalContentRankingTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        settings = ContentDiscoverySettings.for_site(self.site)
        self.source = ContentDiscoverySource.objects.create(
            settings=settings,
            sort_order=0,
            name="Default source",
            url="https://example.gov.uk/feed.xml",
        )

    def test_recently_updated_external_content_ranks_higher(self):
        query = "recent signal query"
        now = timezone.now()
        old_item = ExternalContentItem.objects.create(
            source=self.source,
            url="https://example.gov.uk/old",
            title="Recent signal query guidance",
            updated_at=now - timedelta(days=500),
            hidden=False,
        )
        new_item = ExternalContentItem.objects.create(
            source=self.source,
            url="https://example.gov.uk/new",
            title="Recent signal query guidance",
            updated_at=now - timedelta(days=2),
            hidden=False,
        )

        page = search_backend.search(query, page=1)
        urls_in_order = [result.url for result in page.object_list]

        self.assertIn(old_item.url, urls_in_order)
        self.assertIn(new_item.url, urls_in_order)
        self.assertEqual(urls_in_order[0], new_item.url)

    def test_source_name_and_tags_have_lower_impact_than_title_and_recency(self):
        query = "low weight source tag query"
        now = timezone.now()
        matching_tag = GovukTag.objects.create(
            slug="low-weight-source-tag-query",
            name="Low weight source tag query",
        )
        settings = ContentDiscoverySettings.for_site(self.site)
        source_match_source = ContentDiscoverySource.objects.create(
            settings=settings,
            sort_order=1,
            name="Low weight source tag query",
            url="https://example.gov.uk/source-match-feed.xml",
        )
        title_match_source = ContentDiscoverySource.objects.create(
            settings=settings,
            sort_order=2,
            name="Neutral source",
            url="https://example.gov.uk/neutral-feed.xml",
        )

        source_match_item = ExternalContentItem.objects.create(
            source=source_match_source,
            url="https://example.gov.uk/source-tag-match",
            title="Unrelated",
            summary="No direct title match",
            updated_at=now - timedelta(days=500),
            hidden=False,
        )
        source_match_item.tags.add(matching_tag)

        title_match_item = ExternalContentItem.objects.create(
            source=title_match_source,
            url="https://example.gov.uk/title-match",
            title="Low weight source tag query bulletin",
            summary="A direct title match",
            updated_at=now - timedelta(days=3),
            hidden=False,
        )

        page = search_backend.search(query, page=1)
        urls_in_order = [result.url for result in page.object_list]

        self.assertIn(source_match_item.url, urls_in_order)
        self.assertIn(title_match_item.url, urls_in_order)
        self.assertEqual(urls_in_order[0], title_match_item.url)


class SearchBackendDescriptionFallbackTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root_page = self.site.root_page.specific

    def _result_for_url(self, query: str, url: str):
        page = search_backend.search(query, filters={"site": self.site}, page=1)
        return next((item for item in page.object_list if item.url == url), None)

    def test_page_results_prefer_hero_intro_over_search_description(self):
        page = self.root_page.add_child(
            instance=ContentPage(
                title="Hero intro precedence page",
                slug="hero-intro-precedence-page",
                hero_intro="<p>Hero intro summary</p>",
                search_description="Meta summary",
                body="",
            )
        )
        page.save_revision().publish()

        result = self._result_for_url("hero intro precedence", page.url)

        self.assertIsNotNone(result)
        self.assertEqual(result.search_description, "Hero intro summary")

    def test_page_results_fall_back_to_meta_description_when_hero_is_blank(self):
        page = self.root_page.add_child(
            instance=ContentPage(
                title="Meta fallback page",
                slug="meta-fallback-page",
                hero_intro="",
                search_description="Meta only summary",
                body="",
            )
        )
        page.save_revision().publish()

        result = self._result_for_url("meta fallback", page.url)

        self.assertIsNotNone(result)
        self.assertEqual(result.search_description, "Meta only summary")

    def test_tag_results_prefer_hero_intro_over_search_description(self):
        tag = GovukTag.objects.create(slug="backend-hero-tag", name="Backend hero tag")
        page = self.root_page.add_child(
            instance=SectionPage(
                title="Tagged hero section",
                slug="tagged-hero-section",
                hero_intro="<p>Section hero summary</p>",
                search_description="Section meta summary",
                rows=[],
                free_text="",
            )
        )
        page.tags.add(tag)
        page.save_revision().publish()

        result = self._result_for_url("backend hero tag", page.url)

        self.assertIsNotNone(result)
        self.assertEqual(result.search_description, "Section hero summary")

    def test_tag_listings_page_results_prefer_hero_intro_over_search_description(self):
        parent = self.root_page.add_child(
            instance=ContentPage(
                title="Tag listings parent",
                slug="tag-listings-parent",
                body="",
            )
        )
        parent.save_revision().publish()
        page = parent.add_child(
            instance=TagListingsPage(
                title="Tag listings hero precedence",
                slug="tag-listings-hero-precedence",
                hero_intro="<p>Tag listings hero summary</p>",
                search_description="Tag listings meta summary",
                free_text="",
            )
        )
        page.save_revision().publish()

        result = self._result_for_url("tag listings hero precedence", page.url)

        self.assertIsNotNone(result)
        self.assertEqual(result.search_description, "Tag listings hero summary")
