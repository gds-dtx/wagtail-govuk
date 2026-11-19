from datetime import date, timedelta
from unittest import skipUnless

from django.contrib.auth.models import Group
from django.contrib.postgres.search import SearchRank
from django.db import connection
from django.db.models.lookups import GreaterThan
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from django.utils.text import slugify
from wagtail.models import Page, PageViewRestriction, Site

from govuk.models import (
    ContentDiscoverySettings,
    ContentDiscoverySource,
    ContentPage,
    ExternalContentItem,
    FrameworkMainPage,
    FrameworkSkillsPage,
    GovukChangelogEntry,
    GovukRole,
    GovukSkill,
    GovukTag,
    TagListingsPage,
)
from govuk.search_backend import UNMATCHED_RANK, search_backend
from govuk.tests.helpers import feature_flags, make_framework_main_page, role_url


class SearchBackendTestCase(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root_page = self.site.root_page.specific

    def _publish(self, title, *, parent=None, model=ContentPage, **fields):
        fields.setdefault("slug", slugify(title))
        page = (parent or self.root_page).add_child(instance=model(title=title, **fields))
        page.save_revision().publish()
        return page

    def _search(self, query, **filters):
        return search_backend.search(query, filters={"site": self.site, **filters}, page=1)

    def _add_discovery_source(self, name="Default source", url="https://example.gov.uk/feed.xml", sort_order=0):
        return ContentDiscoverySource.objects.create(
            settings=ContentDiscoverySettings.for_site(self.site),
            sort_order=sort_order,
            name=name,
            url=url,
        )


class OnePageSearchTestCase(SearchBackendTestCase):
    """A site holding one page, searched with a request, as the results page does."""

    def setUp(self):
        super().setUp()
        self.page = self._publish("Accessibility specialist", body="")

    def _search(self, query, **filters):
        return super()._search(query, request=RequestFactory().get("/search/"), **filters)

    def assert_titles(self, results, titles):
        self.assertEqual([item.title for item in results.object_list], titles)

class SearchBackendExternalContentRankingTests(SearchBackendTestCase):
    def setUp(self):
        super().setUp()
        self.source = self._add_discovery_source()

    def test_recently_updated_external_content_ranks_higher(self):
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

        page = search_backend.search("recent signal query", page=1)
        urls_in_order = [result.url for result in page.object_list]

        self.assertIn(old_item.url, urls_in_order)
        self.assertIn(new_item.url, urls_in_order)
        self.assertEqual(urls_in_order[0], new_item.url)

    def test_source_name_and_tags_have_lower_impact_than_title_and_recency(self):
        now = timezone.now()
        matching_tag = GovukTag.objects.create(
            slug="low-weight-source-tag-query",
            name="Low weight source tag query",
        )
        source_match_source = self._add_discovery_source(
            "Low weight source tag query", "https://example.gov.uk/source-match-feed.xml", sort_order=1
        )
        title_match_source = self._add_discovery_source(
            "Neutral source", "https://example.gov.uk/neutral-feed.xml", sort_order=2
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

        page = search_backend.search("low weight source tag query", page=1)
        urls_in_order = [result.url for result in page.object_list]

        self.assertIn(source_match_item.url, urls_in_order)
        self.assertIn(title_match_item.url, urls_in_order)
        self.assertEqual(urls_in_order[0], title_match_item.url)


class SearchBackendExternalContentVisibilityTests(SearchBackendTestCase):
    def setUp(self):
        super().setUp()
        source = self._add_discovery_source("Visibility source", "https://example.gov.uk/visibility-feed.xml")
        self.public_item = ExternalContentItem.objects.create(
            source=source,
            url="https://example.gov.uk/visibility-public-item",
            title="Private external visibility public",
            hidden=False,
            private=False,
        )
        self.private_item = ExternalContentItem.objects.create(
            source=source,
            url="https://example.gov.uk/visibility-private-item",
            title="Private external visibility restricted",
            hidden=False,
            private=True,
        )
        self.hidden_private_item = ExternalContentItem.objects.create(
            source=source,
            url="https://example.gov.uk/visibility-hidden-private-item",
            title="Private external visibility hidden",
            hidden=True,
            private=True,
        )

    def test_private_items_are_shown_only_without_the_public_filter_and_hidden_ones_never(self):
        for public, private_shown in ((True, False), (False, True)):
            with self.subTest(public=public):
                urls = {item.url for item in self._search("private external visibility", public=public).object_list}

                self.assertIn(self.public_item.url, urls)
                self.assertEqual(self.private_item.url in urls, private_shown)
                self.assertNotIn(self.hidden_private_item.url, urls)


class SearchBackendDescriptionFallbackTests(SearchBackendTestCase):
    def _result_for_url(self, query: str, url: str):
        return next((item for item in self._search(query).object_list if item.url == url), None)

    def test_page_results_prefer_hero_intro_over_search_description(self):
        page = self._publish(
            "Hero intro precedence page",
            hero_title="Hero intro display title",
            hero_intro="<p>Hero intro summary</p>",
            search_description="Meta summary",
            body="",
        )

        result = self._result_for_url("hero intro precedence", page.url)

        self.assertIsNotNone(result)
        self.assertEqual(result.title, "Hero intro display title")
        self.assertEqual(result.search_description, "Hero intro summary")

    def test_page_results_fall_back_to_meta_description_when_hero_is_blank(self):
        page = self._publish("Meta fallback page", hero_intro="", search_description="Meta only summary", body="")

        result = self._result_for_url("meta fallback", page.url)

        self.assertIsNotNone(result)
        self.assertEqual(result.search_description, "Meta only summary")

    def test_tag_results_prefer_hero_intro_over_search_description(self):
        tag = GovukTag.objects.create(slug="backend-hero-tag", name="Backend hero tag")
        page = self.root_page.add_child(
            instance=ContentPage(
                title="Tagged hero section",
                slug="tagged-hero-section",
                hero_title="Tagged hero display title",
                hero_intro="<p>Section hero summary</p>",
                search_description="Section meta summary",
            )
        )
        page.tags.add(tag)
        page.save_revision().publish()

        result = self._result_for_url("backend hero tag", page.url)

        self.assertIsNotNone(result)
        self.assertEqual(result.title, "Tagged hero display title")
        self.assertEqual(result.search_description, "Section hero summary")

    def test_tag_listings_page_results_prefer_hero_intro_over_search_description(self):
        parent = self._publish("Tag listings parent", body="")
        page = self._publish(
            "Tag listings hero precedence",
            parent=parent,
            model=TagListingsPage,
            hero_title="Tag listings hero display title",
            hero_intro="<p>Tag listings hero summary</p>",
            search_description="Tag listings meta summary",
            free_text="",
        )

        result = self._result_for_url("tag listings hero precedence", page.url)

        self.assertIsNotNone(result)
        self.assertEqual(result.title, "Tag listings hero display title")
        self.assertEqual(result.search_description, "Tag listings hero summary")


class SearchBackendInternalPriorityAndRecencyTests(SearchBackendTestCase):
    def test_internal_results_rank_ahead_of_external_results(self):
        internal_page = self._publish("Priority ranking query guidance", body="")
        external_item = ExternalContentItem.objects.create(
            source=self._add_discovery_source("Priority source", "https://example.gov.uk/priority-feed.xml"),
            url="https://example.gov.uk/priority-ranking-query-guidance",
            title="Priority ranking query guidance",
            summary="External matching item",
            hidden=False,
        )

        result_urls = [item.url for item in self._search("priority ranking query").object_list]

        self.assertIn(internal_page.url, result_urls)
        self.assertIn(external_item.url, result_urls)
        self.assertLess(result_urls.index(internal_page.url), result_urls.index(external_item.url))

    def test_newer_internal_results_rank_ahead_of_older_internal_results(self):
        old_page = self._publish(
            "Internal recency ranking guidance", slug="internal-recency-ranking-guidance-old", body=""
        )
        new_page = self._publish(
            "Internal recency ranking guidance", slug="internal-recency-ranking-guidance-new", body=""
        )
        now = timezone.now()
        for page, published_at in ((old_page, now - timedelta(days=500)), (new_page, now - timedelta(days=2))):
            Page.objects.filter(pk=page.pk).update(
                latest_revision_created_at=published_at,
                last_published_at=published_at,
                first_published_at=published_at,
            )

        results = self._search("internal recency ranking").object_list
        result_urls = [item.url for item in results]

        self.assertIn(old_page.url, result_urls)
        self.assertIn(new_page.url, result_urls)
        self.assertLess(result_urls.index(new_page.url), result_urls.index(old_page.url))
        old_result = next(item for item in results if item.url == old_page.url)
        new_result = next(item for item in results if item.url == new_page.url)
        self.assertGreater(new_result.score, old_result.score)


class SearchBackendSkillTests(SearchBackendTestCase):
    """Skills are snippets, so nothing reaches them through the page tree."""

    def _add_skills_index(self):
        return self._publish("Skills A-Z", slug="skills-az", model=FrameworkSkillsPage).specific

    def _result_for_title(self, query: str, title: str):
        return next((item for item in self._search(query).object_list if item.title == title), None)

    def _titles(self, query: str):
        return [item.title for item in self._search(query).object_list]

    def _add_systems_design(self, point="identify interdependencies in a service"):
        return GovukSkill.objects.create(
            title="Systems design",
            body="<p>Designing whole systems.</p>",
            working_points=[{"type": "point", "value": point}],
        )

    def _age(self, page, days=400):
        published_at = timezone.now() - timedelta(days=days)
        Page.objects.filter(pk=page.pk).update(
            first_published_at=published_at,
            last_published_at=published_at,
            latest_revision_created_at=published_at,
        )

    def test_a_skill_is_found_by_its_name(self):
        skills_page = self._add_skills_index()
        skill = GovukSkill.objects.create(
            title="Prototyping", body="<p>Building throwaway versions to test an idea.</p>"
        )

        result = self._result_for_title("prototyping", "Prototyping")

        self.assertIsNotNone(result)
        self.assertEqual(result.url, f"{skills_page.url}#{skill.slug}")

    def test_a_skill_result_carries_no_description(self):
        """The title and the "Skill" label are the whole result."""
        self._add_skills_index()
        GovukSkill.objects.create(title="Prototyping", body="<p>Building throwaway versions to test an idea.</p>")

        result = self._result_for_title("prototyping", "Prototyping")

        self.assertEqual(result.result_type, "Skill")
        self.assertEqual(result.search_description, "")

    def test_a_skill_is_found_by_the_wording_of_its_level_points(self):
        """The level points hold most of a skill's text, and are what someone half-remembers."""
        self._add_skills_index()
        self._add_systems_design()

        self.assertIsNotNone(self._result_for_title("interdependencies", "Systems design"))

    def test_a_skill_matching_only_the_streamfield_json_is_not_returned(self):
        """The database filter reads the raw JSON, keys included; scoring is what keeps those out."""
        self._add_skills_index()
        self._add_systems_design()

        self.assertIsNone(self._result_for_title("point", "Systems design"))

    def test_skills_are_left_out_when_the_site_has_no_skills_index(self):
        """Without the index page there is nowhere for a result to link to."""
        GovukSkill.objects.create(title="Prototyping", body="<p>Prototyping.</p>")

        self.assertIsNone(self._result_for_title("prototyping", "Prototyping"))

    def test_a_page_named_exactly_as_searched_beats_a_skill_that_quotes_it(self):
        """Someone typing a role name in full wants that role, not a skill whose name contains it."""
        self._add_skills_index()
        self._publish("Business analyst", body="")
        GovukSkill.objects.create(
            title="Enterprise architecture (business analyst)",
            body="<p>Architecture for a business analyst.</p>",
        )

        titles = self._titles("business analyst")

        self.assertIn("Business analyst", titles)
        self.assertIn("Enterprise architecture (business analyst)", titles)
        self.assertLess(titles.index("Business analyst"), titles.index("Enterprise architecture (business analyst)"))

    def test_a_role_beats_a_skill_whose_name_and_wording_both_quote_it(self):
        """The case a boost alone could not carry.

        Four of the framework's roles are named as a skill is, one word apart:
        the skill "Business architecture" quotes "Business architect" in its
        name, its description and its level points, and three fields of a near
        match outscored the role's one exact one.
        """
        self._add_skills_index()
        role_page = self._publish("Business architect", body="")
        # Aged, as the framework's own role pages are, so no recency boost
        # stands in for the ranking under test.
        self._age(role_page)
        GovukSkill.objects.create(
            title="Business architecture",
            body="<p>What a business architect does for an organisation.</p>",
            working_points=[{"type": "point", "value": "work alongside a business architect"}],
        )

        titles = self._titles("Business architect")

        self.assertIn("Business architecture", titles)
        self.assertEqual(titles[0], "Business architect")

    def test_being_named_exactly_does_not_lift_an_external_result_over_ours(self):
        """The promise is about content this service holds, not about every feed it reads."""
        self._add_skills_index()
        ExternalContentItem.objects.create(
            source=self._add_discovery_source("Named source", "https://example.gov.uk/named-feed.xml"),
            url="https://example.gov.uk/feed-item-one",
            title="Delivery manager",
            summary="An article from elsewhere.",
            updated_at=timezone.now() - timedelta(days=500),
            hidden=False,
        )
        self._publish("Delivery manager guidance", body="")

        titles = self._titles("Delivery manager")

        self.assertIn("Delivery manager", titles)
        self.assertEqual(titles[0], "Delivery manager guidance")

    def test_a_skill_is_dated_by_its_own_changelog(self):
        self._add_skills_index()
        skill = GovukSkill.objects.create(title="Prototyping", body="<p>x</p>")
        GovukChangelogEntry.objects.create(skill=skill, date=date(2026, 3, 4), note="<p>Rewritten.</p>")

        result = self._result_for_title("prototyping", "Prototyping")

        self.assertEqual(result.last_updated.date(), date(2026, 3, 4))

    def test_a_skill_nobody_has_dated_carries_no_date(self):
        """Rather than the index page's, the day an editor last saved a page unrelated to the skill."""
        self._add_skills_index()
        GovukSkill.objects.create(title="Prototyping", body="<p>x</p>")

        self.assertIsNone(self._result_for_title("prototyping", "Prototyping").last_updated)

    def test_publishing_the_index_does_not_lift_every_skill_up_the_results(self):
        """Dated by the page, every skill would take the recency boost the day it was saved."""
        self._add_skills_index()
        older = self._publish(
            "Guidance for delivery teams", slug="guidance", search_description="Advice on digital ways of working."
        )
        self._age(older)
        self._add_systems_design("work with digital colleagues")

        titles = self._titles("digital")

        self.assertLess(titles.index("Guidance for delivery teams"), titles.index("Systems design"))


class SearchBackendBreadcrumbTests(SearchBackendTestCase):
    """Results share their ancestors, so they should not each go and fetch them."""

    def setUp(self):
        super().setUp()
        self.section = self._publish("Data roles")
        self.pages = [
            self._publish(f"Breadcrumb sample role {index}", parent=self.section, body="") for index in range(1, 5)
        ]

    def _breadcrumbs(self, page, request="new", **kwargs):
        if request == "new":
            request = RequestFactory().get("/search/")
        return search_backend._page_breadcrumbs(page, request=request, site_root=self.site.root_page, **kwargs)

    def test_a_result_carries_the_pages_above_it(self):
        results = self._search("breadcrumb sample role", request=RequestFactory().get("/search/"))
        sample = next(item for item in results.object_list if item.title.startswith("Breadcrumb"))

        self.assertEqual(
            [crumb["title"] for crumb in sample.breadcrumbs],
            [self.root_page.title, self.section.title],
        )

    def test_the_pages_above_are_fetched_once_a_request_not_once_a_result(self):
        request = RequestFactory().get("/search/")

        with CaptureQueriesContext(connection) as first:
            self._breadcrumbs(self.pages[0], request)
        with CaptureQueriesContext(connection) as rest:
            for page in self.pages[1:]:
                self._breadcrumbs(page, request)

        self.assertGreater(len(first.captured_queries), 0)
        self.assertEqual(len(rest.captured_queries), 0)

    def test_a_second_request_reads_the_titles_as_they_are_now(self):
        """The cache is the request's, so an edit between requests still shows."""
        self._breadcrumbs(self.pages[0])

        self.section.title = "Renamed data roles"
        self.section.save_revision().publish()

        self.assertEqual(self._breadcrumbs(self.pages[0])[-1]["title"], "Renamed data roles")

    def test_the_breadcrumbs_are_the_same_without_a_request(self):
        self.assertEqual(
            [crumb["title"] for crumb in self._breadcrumbs(self.pages[0], None)],
            [crumb["title"] for crumb in self._breadcrumbs(self.pages[0])],
        )

    def test_a_result_that_includes_itself_ends_with_itself(self):
        crumbs = self._breadcrumbs(self.pages[0], include_page=True)

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

    def test_each_full_text_search_asks_for_more_than_an_unmatched_rank(self):
        for search, queryset in (
            (search_backend._search_pages_postgres, Page.objects.all()),
            (search_backend._search_card_pages_postgres, ContentPage.objects.all()),
            (search_backend._search_hero_postgres, ContentPage.objects.all()),
            (search_backend._search_external_content_postgres, ExternalContentItem.objects.all()),
        ):
            with self.subTest(search.__name__):
                conditions = [
                    node
                    for node in search(queryset, "two words").query.where.children
                    if isinstance(node, GreaterThan) and isinstance(node.lhs, SearchRank)
                ]

                self.assertEqual(len(conditions), 1, "expected one rank condition")
                self.assertEqual(conditions[0].rhs, UNMATCHED_RANK)


@skipUnless(connection.vendor == "postgresql", "the full-text search this covers is PostgreSQL's")
class SearchBackendPostgresResultTests(OnePageSearchTestCase):
    """What a PostgreSQL instance answers, which is what dev and production are."""

    def setUp(self):
        super().setUp()
        self._publish("Delivery manager", body="")

    def test_words_the_site_does_not_hold_find_nothing(self):
        self.assert_titles(self._search("quantum widget"), [])

    def test_a_page_named_by_two_of_its_words_is_still_found(self):
        self.assert_titles(self._search("accessibility specialist"), ["Accessibility specialist"])


class SearchBackendSourceFilterTests(OnePageSearchTestCase):
    """A source the site cannot read is no source, not a 500."""

    def test_an_unreadable_source_is_read_as_no_source(self):
        for label, source in (
            # str.isdigit is true of "²" and int then refuses it.
            ("superscript two", "²"),
            ("no number at all", "abc"),
            # Python refuses a run of more than 4,300 digits, on any engine.
            ("more digits than int reads", "1" * 4301),
        ):
            with self.subTest(label):
                results = self._search("accessibility", source=source)

                self.assert_titles(results, ["Accessibility specialist"])
                self.assertEqual(results.selected_source_id, "")

    def test_a_source_numbered_in_another_script_is_still_a_number(self):
        self.assertEqual(search_backend._normalised_source_filter("٣"), "3")

    def test_a_source_larger_than_a_row_id_goes_the_same_way(self):
        largest = 2**63 - 1
        self.assertEqual(search_backend._normalised_source_filter(str(largest)), str(largest))
        self.assertEqual(search_backend._normalised_source_filter(str(largest + 1)), "")


class SearchBackendNulQueryTests(OnePageSearchTestCase):
    """A NUL in the query is dropped rather than handed to the database.

    PostgreSQL refuses a string literal carrying one, so "/search/?query=%00"
    answered 500 on dev and production. SQLite takes it, so these read the
    query the backend built rather than waiting for an engine to object.
    """

    def test_a_query_of_nothing_but_a_nul_is_the_empty_state(self):
        results = self._search("\x00")

        self.assertEqual(list(results.object_list), [])
        self.assertEqual(results.paginator.count, 0)

    def test_a_nul_among_the_words_leaves_the_words(self):
        self.assert_titles(self._search("accessibility\x00"), ["Accessibility specialist"])

    def test_the_query_the_backend_searches_for_holds_no_nul(self):
        self.assertEqual(search_backend._clean_query("data\x00 "), "data")
        self.assertEqual(search_backend._clean_query("\x00"), "")
        self.assertEqual(search_backend._clean_query(None), "")


class SearchBackendWithoutARequestTests(SearchBackendTestCase):
    """``public=False`` leaves who may see what to the caller; with no request, nothing restricted is let through."""

    def test_nothing_restricted_comes_back_without_a_request(self):
        self._publish("Requestless search open page", body="")
        shut_page = self._publish("Requestless search shut page", body="")
        shut_page.view_restrictions.create(restriction_type=PageViewRestriction.GROUPS).groups.add(
            Group.objects.create(name="Requestless search board")
        )

        titles = {item.title for item in self._search("requestless search", public=False).object_list}

        self.assertIn("Requestless search open page", titles)
        self.assertNotIn("Requestless search shut page", titles)


@override_settings(FEATURE_FLAGS=feature_flags(skills=True))
class SearchBackendRolesAreResultsTests(SearchBackendTestCase):
    """A role has no page of its own, but the backend indexes the role snippets directly."""

    def setUp(self):
        super().setUp()
        self.role = GovukRole.objects.create(
            slug="product-manager",
            title="Product manager",
            family="Digital",
            body="<p>A product manager is responsible for the quality of their products.</p>",
        )
        self.role_url = role_url(make_framework_main_page(self.root_page), self.role)

    def _result_for_role(self, query: str):
        return next((item for item in self._search(query).object_list if item.url == self.role_url), None)

    def test_a_role_is_returned_as_a_search_result_linking_to_its_route(self):
        result = self._result_for_role("product manager")

        self.assertIsNotNone(result)
        self.assertEqual(result.title, "Product manager")
        self.assertIn("responsible for the quality", result.search_description)

    def test_a_role_result_is_labelled_as_a_role(self):
        self.assertEqual(self._result_for_role("product manager").result_type, "Role")

    def test_a_family_search_surfaces_its_roles(self):
        """The family is a weak match, so a search for it finds the role."""
        self.assertIsNotNone(self._result_for_role("Digital"))


@override_settings(FEATURE_FLAGS=feature_flags(skills=False))
class SearchWithoutTheFrameworkTests(SearchBackendTestCase):
    """Search is the one route that reaches snippets without the page tree.

    The backend queried the snippet tables regardless of the flag, so leftover
    or shared rows surfaced on a site with no framework to explain them.
    """

    def test_a_skill_is_not_returned(self):
        self._publish("Skills A-Z", slug="skills-az", model=FrameworkSkillsPage)
        GovukSkill.objects.create(title="Prototyping", body="<p>Building throwaway versions to test an idea.</p>")

        results = self._search("prototyping")

        self.assertEqual([item for item in results.object_list if item.title == "Prototyping"], [])

    def test_a_role_is_not_a_search_result(self):
        """The role's route 404s here, so listing it would be a dead link.

        A framework main page can be in the tree without ever having been
        creatable here: the import makes pages for any model it can resolve.
        """
        role = GovukRole.objects.create(
            slug="product-manager",
            title="Product manager",
            body="<p>A product manager is responsible for their products.</p>",
        )
        served_role_url = role_url(make_framework_main_page(self.root_page), role)

        results = self._search("product manager")

        self.assertIsNone(next((item for item in results.object_list if item.url == served_role_url), None))
        self.assertEqual(self.client.get(served_role_url).status_code, 404)

    def test_the_role_table_is_not_queried_at_all(self):
        GovukRole.objects.create(slug="delivery-manager", title="Delivery manager", body="<p>Body.</p>")
        make_framework_main_page(self.root_page)

        with CaptureQueriesContext(connection) as queries:
            self._search("delivery manager")

        self.assertEqual(
            [
                query["sql"]
                for query in queries.captured_queries
                if "govuk_govukskill" in query["sql"]
                or ("govuk_govukrole" in query["sql"] and "govuk_govukrole_tags" not in query["sql"])
            ],
            [],
        )


@override_settings(FEATURE_FLAGS=feature_flags(skills=True))
class SearchFindsRolesWhenTheMainPageIsTheHomePageTests(TestCase):
    """On the Capability Framework the framework main page *is* the site's home page.

    The site filter looked below the root, so the main page was never found and
    no role was a search result on the one site that has roles.
    """

    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        main_page = make_framework_main_page(Page.objects.get(depth=1), slug="framework")
        self.site.root_page = main_page
        self.site.save()
        self.addCleanup(Site.clear_site_root_paths_cache)
        self.main_page = FrameworkMainPage.objects.get(pk=main_page.pk)
        self.role = GovukRole.objects.create(
            slug="data-analyst",
            title="Data analyst",
            family="Data",
            body="<p>A data analyst collects, manages and shares data.</p>",
        )

    def test_a_role_is_found_and_links_to_its_live_url(self):
        page = search_backend.search("data analyst", filters={"site": self.site}, page=1)

        result = next((item for item in page.object_list if item.result_type == "Role"), None)
        self.assertIsNotNone(result, [item.title for item in page.object_list])
        self.assertEqual(result.title, "Data analyst")
        self.assertEqual(result.url, "/role/data-analyst/")
