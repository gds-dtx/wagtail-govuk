from datetime import date, timedelta
from unittest import skipUnless

from django.contrib.postgres.search import SearchRank
from django.db import connection
from django.db.models import QuerySet
from django.db.models.lookups import GreaterThan
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from wagtail.models import Page, Site

from govuk.models import (
    ContentDiscoverySettings,
    ContentDiscoverySource,
    ContentPage,
    ExternalContentItem,
    GovukChangelogEntry,
    GovukRole,
    GovukSkill,
    GovukTag,
    RolePage,
    SectionPage,
    SkillsAZPage,
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


class SearchBackendSkillTests(TestCase):
    """Skills are snippets, so nothing reaches them through the page tree."""

    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root_page = self.site.root_page.specific

    def _add_skills_index(self):
        skills_page = self.root_page.add_child(
            instance=SkillsAZPage(title="Skills A-Z", slug="skills-az")
        )
        skills_page.save_revision().publish()
        return skills_page.specific

    def _result_for_title(self, results, title: str):
        return next((item for item in results if item.title == title), None)

    def test_a_skill_is_found_by_its_name(self):
        skills_page = self._add_skills_index()
        skill = GovukSkill.objects.create(
            title="Prototyping",
            body="<p>Building throwaway versions to test an idea.</p>",
        )

        page = search_backend.search(
            "prototyping", filters={"site": self.site}, page=1
        )
        result = self._result_for_title(page.object_list, "Prototyping")

        self.assertIsNotNone(result)
        self.assertEqual(result.url, f"{skills_page.url}#{skill.slug}")

    def test_a_skill_is_found_by_the_wording_of_its_level_points(self):
        """The level points hold most of a skill's text, and they are the part
        someone is most likely to half-remember."""
        self._add_skills_index()
        GovukSkill.objects.create(
            title="Systems design",
            body="<p>Designing whole systems.</p>",
            working_points=[
                {"type": "point", "value": "identify interdependencies in a service"},
            ],
        )

        page = search_backend.search(
            "interdependencies", filters={"site": self.site}, page=1
        )

        self.assertIsNotNone(
            self._result_for_title(page.object_list, "Systems design")
        )

    def test_a_skill_matching_only_the_streamfield_json_is_not_returned(self):
        """The database filter reads the raw JSON, so it also matches the keys
        the editor never typed. Scoring is what keeps those out."""
        self._add_skills_index()
        GovukSkill.objects.create(
            title="Systems design",
            body="<p>Designing whole systems.</p>",
            working_points=[
                {"type": "point", "value": "identify interdependencies in a service"},
            ],
        )

        page = search_backend.search("point", filters={"site": self.site}, page=1)

        self.assertIsNone(self._result_for_title(page.object_list, "Systems design"))

    def test_skills_are_left_out_when_the_site_has_no_skills_index(self):
        """Without the index page there is nowhere for a result to link to."""
        GovukSkill.objects.create(title="Prototyping", body="<p>Prototyping.</p>")

        page = search_backend.search(
            "prototyping", filters={"site": self.site}, page=1
        )

        self.assertIsNone(self._result_for_title(page.object_list, "Prototyping"))

    def test_a_page_named_exactly_as_searched_beats_a_skill_that_quotes_it(self):
        """Someone typing a role name in full wants that role, not a skill whose
        longer name happens to contain it."""
        self._add_skills_index()
        role_page = self.root_page.add_child(
            instance=ContentPage(
                title="Business analyst", slug="business-analyst", body=""
            )
        )
        role_page.save_revision().publish()
        GovukSkill.objects.create(
            title="Enterprise architecture (business analyst)",
            body="<p>Architecture for a business analyst.</p>",
        )

        page = search_backend.search(
            "business analyst", filters={"site": self.site}, page=1
        )
        titles = [item.title for item in page.object_list]

        self.assertIn("Business analyst", titles)
        self.assertIn("Enterprise architecture (business analyst)", titles)
        self.assertLess(
            titles.index("Business analyst"),
            titles.index("Enterprise architecture (business analyst)"),
        )

    def test_a_role_beats_a_skill_whose_name_and_wording_both_quote_it(self):
        """The case a boost alone could not carry.

        Four of the framework's roles are named as a skill is, one word apart:
        the skill "Business architecture" quotes "Business architect" in its
        name, its description and its level points, and three fields of a near
        match outscored the role's one exact one.
        """
        self._add_skills_index()
        role_page = self.root_page.add_child(
            instance=ContentPage(
                title="Business architect", slug="business-architect", body=""
            )
        )
        role_page.save_revision().publish()
        # Aged, as the framework's own role pages are: published a year ago and
        # edited when the wording changes, so no recency boost stands in for the
        # ranking under test.
        published_at = timezone.now() - timedelta(days=400)
        Page.objects.filter(pk=role_page.pk).update(
            first_published_at=published_at,
            last_published_at=published_at,
            latest_revision_created_at=published_at,
        )
        GovukSkill.objects.create(
            title="Business architecture",
            body="<p>What a business architect does for an organisation.</p>",
            working_points=[
                {"type": "point", "value": "work alongside a business architect"},
            ],
        )

        page = search_backend.search(
            "Business architect", filters={"site": self.site}, page=1
        )
        titles = [item.title for item in page.object_list]

        self.assertIn("Business architecture", titles)
        self.assertEqual(titles[0], "Business architect")

    def test_being_named_exactly_does_not_lift_an_external_result_over_ours(self):
        """The site's own pages still come first: the promise is about content
        this service holds, not about every feed it reads."""
        self._add_skills_index()
        settings = ContentDiscoverySettings.for_site(self.site)
        source = ContentDiscoverySource.objects.create(
            settings=settings,
            sort_order=0,
            name="Named source",
            url="https://example.gov.uk/named-feed.xml",
        )
        ExternalContentItem.objects.create(
            source=source,
            url="https://example.gov.uk/feed-item-one",
            title="Delivery manager",
            summary="An article from elsewhere.",
            updated_at=timezone.now() - timedelta(days=500),
            hidden=False,
        )
        internal_page = self.root_page.add_child(
            instance=ContentPage(
                title="Delivery manager guidance",
                slug="delivery-manager-guidance",
                body="",
            )
        )
        internal_page.save_revision().publish()

        page = search_backend.search(
            "Delivery manager", filters={"site": self.site}, page=1
        )
        titles = [item.title for item in page.object_list]

        self.assertIn("Delivery manager", titles)
        self.assertEqual(titles[0], "Delivery manager guidance")

    def test_a_skill_is_dated_by_its_own_changelog(self):
        self._add_skills_index()
        skill = GovukSkill.objects.create(title="Prototyping", body="<p>x</p>")
        GovukChangelogEntry.objects.create(
            skill=skill, date=date(2026, 3, 4), note="<p>Rewritten.</p>"
        )

        page = search_backend.search(
            "prototyping", filters={"site": self.site}, page=1
        )
        result = self._result_for_title(page.object_list, "Prototyping")

        self.assertEqual(result.last_updated.date(), date(2026, 3, 4))

    def test_a_skill_nobody_has_dated_carries_no_date(self):
        """Rather than the index page's, which is the day an editor last saved
        a page the skill has nothing to do with."""
        self._add_skills_index()
        GovukSkill.objects.create(title="Prototyping", body="<p>x</p>")

        page = search_backend.search(
            "prototyping", filters={"site": self.site}, page=1
        )

        self.assertIsNone(
            self._result_for_title(page.object_list, "Prototyping").last_updated
        )

    def test_publishing_the_index_does_not_lift_every_skill_up_the_results(self):
        """Dated by the page, all 185 skills would count as changed the day it
        was last saved and take the recency boost that goes with it, putting a
        skill that mentions the word once above a page written about it.
        """
        self._add_skills_index()
        older = self.root_page.add_child(
            instance=ContentPage(
                title="Guidance for delivery teams",
                slug="guidance",
                search_description="Advice on digital ways of working.",
            )
        )
        older.save_revision().publish()
        ContentPage.objects.filter(pk=older.pk).update(
            first_published_at=timezone.now() - timedelta(days=400),
            last_published_at=timezone.now() - timedelta(days=400),
            latest_revision_created_at=timezone.now() - timedelta(days=400),
        )
        GovukSkill.objects.create(
            title="Systems design",
            body="<p>Designing whole systems.</p>",
            working_points=[
                {"type": "point", "value": "work with digital colleagues"}
            ],
        )

        page = search_backend.search("digital", filters={"site": self.site}, page=1)
        titles = [item.title for item in page.object_list]

        self.assertLess(
            titles.index("Guidance for delivery teams"),
            titles.index("Systems design"),
        )


class SearchBackendBreadcrumbTests(TestCase):
    """Results share their ancestors, so they should not each go and fetch them."""

    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root_page = self.site.root_page.specific
        self.section = self.root_page.add_child(
            instance=SectionPage(title="Data roles", slug="data-roles")
        )
        self.section.save_revision().publish()
        self.pages = []
        for index in range(1, 5):
            page = self.section.add_child(
                instance=ContentPage(
                    title=f"Breadcrumb sample role {index}",
                    slug=f"breadcrumb-sample-role-{index}",
                    body="",
                )
            )
            page.save_revision().publish()
            self.pages.append(page)

    def test_a_result_carries_the_pages_above_it(self):
        request = RequestFactory().get("/search/")

        results = search_backend.search(
            "breadcrumb sample role",
            filters={"site": self.site, "request": request},
            page=1,
        )
        sample = next(
            item for item in results.object_list if item.title.startswith("Breadcrumb")
        )

        self.assertEqual(
            [crumb["title"] for crumb in sample.breadcrumbs],
            [self.root_page.title, self.section.title],
        )

    def test_the_pages_above_are_fetched_once_a_request_not_once_a_result(self):
        request = RequestFactory().get("/search/")
        site_root = self.site.root_page

        with CaptureQueriesContext(connection) as first:
            search_backend._page_breadcrumbs(
                self.pages[0], request=request, site_root=site_root
            )
        with CaptureQueriesContext(connection) as rest:
            for page in self.pages[1:]:
                search_backend._page_breadcrumbs(
                    page, request=request, site_root=site_root
                )

        self.assertGreater(len(first.captured_queries), 0)
        self.assertEqual(len(rest.captured_queries), 0)

    def test_a_second_request_reads_the_titles_as_they_are_now(self):
        """The cache is the request's, so an edit between requests still shows."""
        site_root = self.site.root_page
        search_backend._page_breadcrumbs(
            self.pages[0], request=RequestFactory().get("/search/"), site_root=site_root
        )

        self.section.title = "Renamed data roles"
        self.section.save_revision().publish()

        crumbs = search_backend._page_breadcrumbs(
            self.pages[0], request=RequestFactory().get("/search/"), site_root=site_root
        )

        self.assertEqual(crumbs[-1]["title"], "Renamed data roles")

    def test_the_breadcrumbs_are_the_same_without_a_request(self):
        with_request = search_backend._page_breadcrumbs(
            self.pages[0],
            request=RequestFactory().get("/search/"),
            site_root=self.site.root_page,
        )
        without_request = search_backend._page_breadcrumbs(
            self.pages[0], request=None, site_root=self.site.root_page
        )

        self.assertEqual(
            [crumb["title"] for crumb in without_request],
            [crumb["title"] for crumb in with_request],
        )

    def test_a_result_that_includes_itself_ends_with_itself(self):
        crumbs = search_backend._page_breadcrumbs(
            self.pages[0],
            request=RequestFactory().get("/search/"),
            site_root=self.site.root_page,
            include_page=True,
        )

        self.assertEqual(
            [crumb["title"] for crumb in crumbs],
            [self.root_page.title, self.section.title, self.pages[0].title],
        )


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


@override_settings(
    FEATURE_FLAGS={
        "SKILLS": True,
        "ORGANISATIONS": False,
        "PEOPLE_FINDER": False,
        "FEEDBACK": False,
    }
)
class SearchBackendRolePageDescriptionTests(TestCase):
    """A role result should say what the role is, as a skill result does.

    A RolePage has no hero intro and no search description -- the words are on
    the GovukRole snippet it selects -- so a role came back from a search as a
    bare title beside skills that carried full descriptions.
    """

    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root_page = self.site.root_page.specific

        self.role = GovukRole.objects.create(
            slug="product-manager",
            title="Product manager",
            body="<p>A product manager is responsible for the quality of "
            "their products.</p>",
        )
        self.page = self._role_page("product-manager", "Product manager", self.role)

    def _role_page(self, slug: str, title: str, *roles) -> RolePage:
        page = self.root_page.add_child(
            instance=RolePage(
                title=title,
                slug=slug,
                selected_roles=[
                    {"type": "role", "value": role.pk} for role in roles
                ],
            )
        )
        page.save_revision().publish()
        return page.specific

    def _result_for_url(self, query: str, url: str):
        page = search_backend.search(query, filters={"site": self.site}, page=1)
        return next((item for item in page.object_list if item.url == url), None)

    def test_a_role_result_carries_the_roles_own_description(self):
        result = self._result_for_url("product manager", self.page.url)

        self.assertIsNotNone(result)
        self.assertEqual(
            result.search_description,
            "A product manager is responsible for the quality of their products.",
        )

    def test_the_description_is_plain_text(self):
        """It is rendered into a <p>, so markup would show as markup."""
        result = self._result_for_url("product manager", self.page.url)

        self.assertNotIn("<p>", result.search_description)

    def test_a_page_that_has_its_own_description_keeps_it(self):
        self.page.search_description = "Set by an editor"
        self.page.save_revision().publish()

        result = self._result_for_url("product manager", self.page.url)

        self.assertEqual(result.search_description, "Set by an editor")

    def test_the_first_selected_role_is_the_one_that_describes_the_page(self):
        """Role pages that group several roles lead with the first."""
        second = GovukRole.objects.create(
            slug="delivery-manager",
            title="Delivery manager",
            body="<p>Second role.</p>",
        )
        page = self._role_page("grouped", "Grouped roles", self.role, second)

        result = self._result_for_url("grouped", page.url)

        self.assertEqual(
            result.search_description,
            "A product manager is responsible for the quality of their products.",
        )

    def test_a_role_page_selecting_nothing_is_still_returned(self):
        page = self._role_page("empty-role-page", "Empty role page")

        result = self._result_for_url("empty role page", page.url)

        self.assertIsNotNone(result)
        self.assertEqual(result.search_description, "")

    def test_every_role_page_costs_one_query_between_them(self):
        """Not one per page.

        The framework has 52 role pages and a broad search can return many of
        them at once, so a query apiece is the difference between a search
        page and a slow one. get_selected_role_ids reads the stored JSON
        rather than resolving the chooser, which is what allows the batch.
        """
        roles = [
            GovukRole.objects.create(
                slug=f"described-role-{index}",
                title=f"Described role {index}",
                body=f"<p>Body of described role {index}.</p>",
            )
            for index in range(5)
        ]
        for index, role in enumerate(roles):
            self._role_page(f"described-role-{index}", f"Described role {index}", role)

        with CaptureQueriesContext(connection) as queries:
            results = search_backend.search(
                "described role", filters={"site": self.site}, page=1
            )
            self.assertEqual(len(results.object_list), 5)

        role_queries = [
            query["sql"]
            for query in queries.captured_queries
            if "govuk_govukrole" in query["sql"] and "govuk_govukrole_tags" not in query["sql"]
        ]
        self.assertEqual(len(role_queries), 1, role_queries)


def _feature_flags(*, skills_enabled: bool) -> dict[str, bool]:
    return {
        "SKILLS": skills_enabled,
        "ORGANISATIONS": False,
        "PEOPLE_FINDER": False,
        "FEEDBACK": False,
    }


@override_settings(FEATURE_FLAGS=_feature_flags(skills_enabled=False))
class SearchWithoutTheFrameworkTests(TestCase):
    """Search is the one route that reaches snippets without the page tree.

    Roles and skills are the Capability Framework's, and its page types are not
    creatable on a site with the flag off -- but the search backend queried the
    snippet tables regardless of the flag, so leftover or shared rows would
    surface on a site that has no framework to explain them.
    """

    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root_page = self.site.root_page.specific

    def test_a_skill_is_not_returned(self):
        skills_page = self.root_page.add_child(
            instance=SkillsAZPage(title="Skills A-Z", slug="skills-az")
        )
        skills_page.save_revision().publish()
        GovukSkill.objects.create(
            title="Prototyping",
            body="<p>Building throwaway versions to test an idea.</p>",
        )

        results = search_backend.search(
            "prototyping", filters={"site": self.site}, page=1
        )

        self.assertEqual(
            [item for item in results.object_list if item.title == "Prototyping"],
            [],
        )

    def test_a_role_page_borrows_no_description_from_a_role(self):
        role = GovukRole.objects.create(
            slug="product-manager",
            title="Product manager",
            body="<p>A product manager is responsible for their products.</p>",
        )
        page = self.root_page.add_child(
            instance=RolePage(
                title="Product manager",
                slug="product-manager",
                selected_roles=[{"type": "role", "value": role.pk}],
            )
        )
        page.save_revision().publish()

        results = search_backend.search(
            "product manager", filters={"site": self.site}, page=1
        )
        result = next(
            (item for item in results.object_list if item.url == page.specific.url),
            None,
        )

        self.assertIsNotNone(result)
        self.assertEqual(result.search_description, "")

    def test_the_role_table_is_not_queried_at_all(self):
        """Not merely filtered out afterwards -- not read."""
        role = GovukRole.objects.create(
            slug="delivery-manager", title="Delivery manager", body="<p>Body.</p>"
        )
        page = self.root_page.add_child(
            instance=RolePage(
                title="Delivery manager",
                slug="delivery-manager",
                selected_roles=[{"type": "role", "value": role.pk}],
            )
        )
        page.save_revision().publish()

        with CaptureQueriesContext(connection) as queries:
            search_backend.search(
                "delivery manager", filters={"site": self.site}, page=1
            )

        self.assertEqual(
            [
                query["sql"]
                for query in queries.captured_queries
                if "govuk_govukskill" in query["sql"]
                or (
                    "govuk_govukrole" in query["sql"]
                    and "govuk_govukrole_tags" not in query["sql"]
                )
            ],
            [],
        )
