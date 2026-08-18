from datetime import timedelta
from unittest import skipUnless

from django.contrib.postgres.search import SearchRank
from django.db import connection
from django.db.models import QuerySet
from django.db.models.lookups import GreaterThan
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.utils import timezone
from wagtail.models import Page, Site

from govuk.models import (
    ContentPage,
    ContentDiscoverySettings,
    ContentDiscoverySource,
    ExternalContentItem,
    GovukTag,
    SectionPage,
    TagListingsPage,
)
from govuk.search_backend import UNMATCHED_RANK, search_backend


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


class SearchBackendExternalContentVisibilityTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        settings = ContentDiscoverySettings.for_site(self.site)
        self.source = ContentDiscoverySource.objects.create(
            settings=settings,
            sort_order=0,
            name="Visibility source",
            url="https://example.gov.uk/visibility-feed.xml",
        )
        self.public_item = ExternalContentItem.objects.create(
            source=self.source,
            url="https://example.gov.uk/visibility-public-item",
            title="Private external visibility public",
            hidden=False,
            private=False,
        )
        self.private_item = ExternalContentItem.objects.create(
            source=self.source,
            url="https://example.gov.uk/visibility-private-item",
            title="Private external visibility restricted",
            hidden=False,
            private=True,
        )
        self.hidden_private_item = ExternalContentItem.objects.create(
            source=self.source,
            url="https://example.gov.uk/visibility-hidden-private-item",
            title="Private external visibility hidden",
            hidden=True,
            private=True,
        )

    def test_external_results_exclude_private_items_when_public_filter_is_enabled(self):
        page = search_backend.search(
            "private external visibility",
            filters={"site": self.site, "public": True},
            page=1,
        )
        urls = {item.url for item in page.object_list}

        self.assertIn(self.public_item.url, urls)
        self.assertNotIn(self.private_item.url, urls)
        self.assertNotIn(self.hidden_private_item.url, urls)

    def test_external_results_include_private_items_when_public_filter_is_disabled(self):
        page = search_backend.search(
            "private external visibility",
            filters={"site": self.site, "public": False},
            page=1,
        )
        urls = {item.url for item in page.object_list}

        self.assertIn(self.public_item.url, urls)
        self.assertIn(self.private_item.url, urls)
        self.assertNotIn(self.hidden_private_item.url, urls)


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
                hero_title="Hero intro display title",
                hero_intro="<p>Hero intro summary</p>",
                search_description="Meta summary",
                body="",
            )
        )
        page.save_revision().publish()

        result = self._result_for_url("hero intro precedence", page.url)

        self.assertIsNotNone(result)
        self.assertEqual(result.title, "Hero intro display title")
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
                hero_title="Tagged hero display title",
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
        self.assertEqual(result.title, "Tagged hero display title")
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
                hero_title="Tag listings hero display title",
                hero_intro="<p>Tag listings hero summary</p>",
                search_description="Tag listings meta summary",
                free_text="",
            )
        )
        page.save_revision().publish()

        result = self._result_for_url("tag listings hero precedence", page.url)

        self.assertIsNotNone(result)
        self.assertEqual(result.title, "Tag listings hero display title")
        self.assertEqual(result.search_description, "Tag listings hero summary")


class SearchBackendInternalPriorityAndRecencyTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root_page = self.site.root_page.specific

        settings = ContentDiscoverySettings.for_site(self.site)
        self.source = ContentDiscoverySource.objects.create(
            settings=settings,
            sort_order=0,
            name="Priority source",
            url="https://example.gov.uk/priority-feed.xml",
        )

    def _result_for_url(self, results, url: str):
        return next((item for item in results if item.url == url), None)

    def test_internal_results_rank_ahead_of_external_results(self):
        query = "priority ranking query"
        internal_page = self.root_page.add_child(
            instance=ContentPage(
                title="Priority ranking query guidance",
                slug="priority-ranking-query-guidance",
                body="",
            )
        )
        internal_page.save_revision().publish()

        external_item = ExternalContentItem.objects.create(
            source=self.source,
            url="https://example.gov.uk/priority-ranking-query-guidance",
            title="Priority ranking query guidance",
            summary="External matching item",
            hidden=False,
        )

        page = search_backend.search(query, filters={"site": self.site}, page=1)
        result_urls = [item.url for item in page.object_list]

        self.assertIn(internal_page.url, result_urls)
        self.assertIn(external_item.url, result_urls)
        self.assertLess(
            result_urls.index(internal_page.url),
            result_urls.index(external_item.url),
        )

    def test_newer_internal_results_rank_ahead_of_older_internal_results(self):
        query = "internal recency ranking"
        old_page = self.root_page.add_child(
            instance=ContentPage(
                title="Internal recency ranking guidance",
                slug="internal-recency-ranking-guidance-old",
                body="",
            )
        )
        old_page.save_revision().publish()

        new_page = self.root_page.add_child(
            instance=ContentPage(
                title="Internal recency ranking guidance",
                slug="internal-recency-ranking-guidance-new",
                body="",
            )
        )
        new_page.save_revision().publish()

        now = timezone.now()
        old_timestamp = now - timedelta(days=500)
        Page.objects.filter(pk=old_page.pk).update(
            latest_revision_created_at=old_timestamp,
            last_published_at=old_timestamp,
            first_published_at=old_timestamp,
        )
        Page.objects.filter(pk=new_page.pk).update(
            latest_revision_created_at=now - timedelta(days=2),
            last_published_at=now - timedelta(days=2),
            first_published_at=now - timedelta(days=2),
        )

        page = search_backend.search(query, filters={"site": self.site}, page=1)
        result_urls = [item.url for item in page.object_list]

        self.assertIn(old_page.url, result_urls)
        self.assertIn(new_page.url, result_urls)
        self.assertLess(
            result_urls.index(new_page.url),
            result_urls.index(old_page.url),
        )

        old_result = self._result_for_url(page.object_list, old_page.url)
        new_result = self._result_for_url(page.object_list, new_page.url)
        self.assertIsNotNone(old_result)
        self.assertIsNotNone(new_result)
        self.assertGreater(new_result.score, old_result.score)


class SearchBackendPostgresRankTests(SimpleTestCase):
    """A rank above zero is not the same thing as a match.

    PostgreSQL reads a query of more than one word as an AND of the words and
    floors the rank of a row that failed it at 1e-20 rather than returning the
    zero it means. Everything the four full-text searches read is then above
    zero, so the site answers a search for words it does not hold with every
    page it has. SQLite matches on the text itself, which is why neither a
    local run nor CI has ever shown it.
    """

    def _rank_floor(self, queryset: QuerySet) -> float:
        conditions = [
            node
            for node in queryset.query.where.children
            if isinstance(node, GreaterThan) and isinstance(node.lhs, SearchRank)
        ]
        self.assertEqual(len(conditions), 1, "expected one rank condition")
        return conditions[0].rhs

    def test_a_page_search_asks_for_more_than_an_unmatched_rank(self):
        queryset = search_backend._search_pages_postgres(
            Page.objects.all(), "two words"
        )

        self.assertEqual(self._rank_floor(queryset), UNMATCHED_RANK)

    def test_a_section_card_search_asks_for_more_than_an_unmatched_rank(self):
        queryset = search_backend._search_sections_postgres(
            SectionPage.objects.all(), "two words"
        )

        self.assertEqual(self._rank_floor(queryset), UNMATCHED_RANK)

    def test_a_hero_search_asks_for_more_than_an_unmatched_rank(self):
        queryset = search_backend._search_hero_postgres(
            ContentPage.objects.all(), "two words"
        )

        self.assertEqual(self._rank_floor(queryset), UNMATCHED_RANK)

    def test_an_external_content_search_asks_for_more_than_an_unmatched_rank(self):
        queryset = search_backend._search_external_content_postgres(
            ExternalContentItem.objects.all(), "two words"
        )

        self.assertEqual(self._rank_floor(queryset), UNMATCHED_RANK)


@skipUnless(
    connection.vendor == "postgresql",
    "the full-text search this covers is PostgreSQL's",
)
class SearchBackendPostgresResultTests(TestCase):
    """What a PostgreSQL instance answers, which is what dev and production are."""

    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root_page = self.site.root_page.specific
        self.page = self.root_page.add_child(
            instance=ContentPage(
                title="Accessibility specialist",
                slug="accessibility-specialist",
                body="",
            )
        )
        self.page.save_revision().publish()
        self.other_page = self.root_page.add_child(
            instance=ContentPage(
                title="Delivery manager", slug="delivery-manager", body=""
            )
        )
        self.other_page.save_revision().publish()

    def _search(self, query: str):
        return search_backend.search(
            query,
            filters={"site": self.site, "request": RequestFactory().get("/search/")},
            page=1,
        )

    def test_words_the_site_does_not_hold_find_nothing(self):
        results = self._search("quantum widget")

        self.assertEqual(list(results.object_list), [])

    def test_a_page_named_by_two_of_its_words_is_still_found(self):
        results = self._search("accessibility specialist")

        self.assertEqual(
            [item.title for item in results.object_list], ["Accessibility specialist"]
        )


class SearchBackendSourceFilterTests(TestCase):
    """A source the site cannot read is no source, not a 500.

    ``str.isdigit`` is true of "²" and "₂" and ``int`` then refuses them, so
    "/search/?query=data&source=²" raised where every other unreadable source
    fell back to showing all of them.
    """

    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root_page = self.site.root_page.specific
        self.page = self.root_page.add_child(
            instance=ContentPage(
                title="Accessibility specialist",
                slug="accessibility-specialist",
                body="",
            )
        )
        self.page.save_revision().publish()

    def _search(self, source: str):
        return search_backend.search(
            "accessibility",
            filters={
                "site": self.site,
                "request": RequestFactory().get("/search/"),
                "source": source,
            },
            page=1,
        )

    def test_a_source_of_superscript_two_is_read_as_no_source(self):
        results = self._search("²")

        self.assertEqual(
            [item.title for item in results.object_list], ["Accessibility specialist"]
        )
        self.assertEqual(results.selected_source_id, "")

    def test_a_source_that_is_no_number_at_all_is_read_the_same_way(self):
        results = self._search("abc")

        self.assertEqual(
            [item.title for item in results.object_list], ["Accessibility specialist"]
        )
        self.assertEqual(results.selected_source_id, "")

    def test_a_source_numbered_in_another_script_is_still_a_number(self):
        self.assertEqual(search_backend._normalised_source_filter("٣"), "3")

    def test_a_source_of_more_digits_than_int_reads_matches_nothing(self):
        """Reading the digits is not enough on its own to reach ``int``.

        Python refuses a run of more than 4,300 digits, so a source of 4,301 of
        them is decimal all the way down and was still a 500 -- on PostgreSQL
        as much as on SQLite, since it is ``int`` that objects, not the engine.
        """
        results = self._search("1" * 4301)

        self.assertEqual(
            [item.title for item in results.object_list], ["Accessibility specialist"]
        )
        self.assertEqual(results.selected_source_id, "")

    def test_a_source_larger_than_a_row_id_goes_the_same_way(self):
        largest = 2**63 - 1
        self.assertEqual(
            search_backend._normalised_source_filter(str(largest)), str(largest)
        )
        self.assertEqual(search_backend._normalised_source_filter(str(largest + 1)), "")


class SearchBackendNulQueryTests(TestCase):
    """A NUL in the query is dropped rather than handed to the database.

    PostgreSQL refuses a string literal carrying one, so "/search/?query=%00"
    answered 500 on dev and production. SQLite takes it, which is why the
    suite and CI never saw it: these tests read the query the backend built
    rather than waiting for an engine to object to it.
    """

    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root_page = self.site.root_page.specific
        self.page = self.root_page.add_child(
            instance=ContentPage(
                title="Accessibility specialist",
                slug="accessibility-specialist",
                body="",
            )
        )
        self.page.save_revision().publish()

    def _search(self, query: str):
        return search_backend.search(
            query,
            filters={"site": self.site, "request": RequestFactory().get("/search/")},
            page=1,
        )

    def test_a_query_of_nothing_but_a_nul_is_the_empty_state(self):
        results = self._search("\x00")

        self.assertEqual(list(results.object_list), [])
        self.assertEqual(results.paginator.count, 0)

    def test_a_nul_among_the_words_leaves_the_words(self):
        results = self._search("accessibility\x00")

        self.assertEqual(
            [item.title for item in results.object_list], ["Accessibility specialist"]
        )

    def test_the_query_the_backend_searches_for_holds_no_nul(self):
        self.assertEqual(search_backend._clean_query("data\x00 "), "data")
        self.assertEqual(search_backend._clean_query("\x00"), "")
        self.assertEqual(search_backend._clean_query(None), "")

