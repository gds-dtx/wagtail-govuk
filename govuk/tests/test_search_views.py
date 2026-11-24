"""The search results page, the header box's suggestions, and Settings > Search."""

from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.staticfiles import finders
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from wagtail.models import PageViewRestriction, Site

from govuk.models import (
    ContentDiscoverySettings,
    ContentDiscoverySource,
    ContentPage,
    ExternalContentItem,
    ExternalContentItemTag,
    FrameworkSkillsPage,
    GovukRole,
    GovukSkill,
    GovukTag,
    SearchSettings,
)
from govuk.tests.helpers import (
    feature_flags,
    make_framework_main_page,
    publish_page,
    role_url,
)

STATIC = Path(__file__).resolve().parents[1] / "static"


class SearchViewTestCase(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root_page = self.site.root_page.specific

    def _search(self, query, **params):
        return self.client.get(reverse("search"), {"query": query, **params})

    def _sign_in(self, username, **kwargs):
        user = get_user_model().objects.create_user(username=username, password="password", **kwargs)
        self.client.force_login(user)
        return user


class SearchViewVisibilityTests(SearchViewTestCase):
    def setUp(self):
        super().setUp()
        publish_page("Search visibility public page", "")
        private_page = publish_page("Search visibility private page", "")
        private_page.view_restrictions.create(restriction_type=PageViewRestriction.LOGIN)

    def test_search_only_shows_public_pages_to_anonymous_users(self):
        response = self._search("search visibility")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Search visibility public page")
        self.assertNotContains(response, "Search visibility private page")

    def test_search_shows_public_and_private_pages_to_logged_in_users(self):
        self._sign_in("search-visibility-user")

        response = self._search("search visibility")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Search visibility public page")
        self.assertContains(response, "Search visibility private page")


class SearchViewFilterTests(SearchViewTestCase):
    def setUp(self):
        super().setUp()
        self.this_site_source_label = f"{self.site.site_name} (this site)" if self.site.site_name else "This site"
        self.this_site_result_source_tag_label = self.site.site_name or "This site"

        self.alpha_tag = GovukTag.objects.create(slug="alpha", name="Alpha")
        self.beta_tag = GovukTag.objects.create(slug="beta", name="Beta")

        alpha_page = self.root_page.add_child(
            instance=ContentPage(title="Filterable alpha page", slug="filterable-alpha-page", body="")
        )
        alpha_page.tags.add(self.alpha_tag)
        alpha_page.save_revision().publish()

        beta_section = self.root_page.add_child(
            instance=ContentPage(title="Filterable beta section", slug="filterable-beta-section", hero_intro="")
        )
        beta_section.tags.add(self.beta_tag)
        beta_section.save_revision().publish()

        discovery_settings = ContentDiscoverySettings.for_site(self.site)
        self.source_one = ContentDiscoverySource.objects.create(
            settings=discovery_settings, sort_order=1, name="Source One", url="https://example.gov.uk/source-one.xml"
        )
        self.source_two = ContentDiscoverySource.objects.create(
            settings=discovery_settings, sort_order=2, name="Source Two", url="https://example.gov.uk/source-two.xml"
        )
        for source, tag, name in ((self.source_one, self.alpha_tag, "alpha"), (self.source_two, self.beta_tag, "beta")):
            item = ExternalContentItem.objects.create(
                source=source,
                url=f"https://example.gov.uk/filterable-{name}",
                title=f"Filterable {name} external",
                hidden=False,
            )
            ExternalContentItemTag.objects.create(content_object=item, tag=tag)

    def test_search_shows_filter_controls_when_results_have_tags_and_sources(self):
        response = self._search("filterable")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Filter by tag")
        self.assertContains(response, "Filter by source")
        self.assertContains(response, "Alpha")
        self.assertContains(response, "Source One")
        self.assertContains(response, self.this_site_source_label)

    def test_each_filter_limits_the_results_to_what_matches_it(self):
        alpha_page, alpha_external = "Filterable alpha page", "Filterable alpha external"
        beta_section, beta_external = "Filterable beta section", "Filterable beta external"
        for label, params, shown, hidden in (
            ("tag", {"tag": self.alpha_tag.slug}, [alpha_page, alpha_external], [beta_section, beta_external]),
            (
                "source",
                {"source": str(self.source_two.id)},
                [beta_external],
                [alpha_external, alpha_page, beta_section],
            ),
            ("this site", {"source": "__this_site__"}, [alpha_page, beta_section], [alpha_external, beta_external]),
        ):
            with self.subTest(label):
                response = self._search("filterable", **params)

                self.assertEqual(response.status_code, 200)
                for title in shown:
                    self.assertContains(response, title)
                for title in hidden:
                    self.assertNotContains(response, title)

    def test_search_results_show_this_site_source_tag_for_internal_results(self):
        response = self._search("filterable")

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f'<strong class="govuk-tag">{self.this_site_result_source_tag_label}</strong>',
            count=2,
            html=True,
        )

    def test_search_results_prefer_hero_title_over_page_title(self):
        page = self.root_page.add_child(
            instance=ContentPage(
                title="Hero title fallback page",
                slug="hero-title-fallback-page",
                hero_title="Preferred hero page title",
                body="",
            )
        )
        page.save_revision().publish()

        response = self._search("hero title fallback")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Preferred hero page title")
        self.assertNotContains(response, "Hero title fallback page")


class SearchResultsLayoutTests(SearchViewTestCase):
    """The results use the deployed design's card grid, with pagination below."""

    def setUp(self):
        super().setUp()
        self.paginated_tag = GovukTag.objects.create(slug="paginated", name="Paginated")
        # A page of results holds 15, so 20 pages give a second page to go to.
        for index in range(1, 21):
            page = self.root_page.add_child(
                instance=ContentPage(title=f"Paginated result {index}", slug=f"paginated-result-{index}", body="")
            )
            page.tags.add(self.paginated_tag)
            page.save_revision().publish()
        publish_page("Solitary result page", "")

    def test_each_result_is_a_card_in_the_grid(self):
        response = self._search("solitary")

        self.assertContains(response, 'class="section-cards"')
        self.assertContains(response, 'class="section-card"', count=1)

    def test_pagination_offers_the_pages_a_long_result_set_runs_to(self):
        response = self._search("paginated result")

        self.assertContains(response, "govuk-pagination")
        self.assertContains(response, 'aria-label="Page 1"')
        self.assertContains(response, 'aria-label="Page 2"')
        self.assertContains(response, 'aria-current="page"')
        self.assertContains(response, "page=2")

    def test_the_page_a_reader_is_on_is_the_current_one(self):
        response = self._search("paginated result", page=2)

        self.assertContains(
            response,
            '<li class="govuk-pagination__item govuk-pagination__item--current">'
            '<a class="govuk-link govuk-pagination__link" '
            'href="?query=paginated+result&amp;page=2" aria-label="Page 2" '
            'aria-current="page">2</a></li>',
            html=True,
        )

    def test_a_page_link_keeps_the_search_and_the_filters(self):
        response = self._search("paginated result", tag=self.paginated_tag.slug)

        self.assertContains(response, "?query=paginated+result&amp;tag=paginated&amp;page=2")

    def test_a_filter_that_matched_nothing_is_not_carried_into_the_page_links(self):
        """The search drops a tag it cannot offer, so a link carrying it would mislabel the results."""
        response = self._search("paginated result", tag="no-such-tag")

        self.assertContains(response, "?query=paginated+result&amp;page=2")
        self.assertNotContains(response, "tag=no-such-tag")

    def test_there_is_no_pagination_where_everything_fits_on_one_page(self):
        response = self._search("solitary")

        self.assertContains(response, "Solitary result page")
        self.assertNotContains(response, "govuk-pagination")


class SearchViewRestrictedPageTests(SearchViewTestCase):
    """A page held back from this reader has no business appearing in a result.

    ``LOGIN`` is covered above: signing in is the whole of what it asks. The two
    below ask for more than an account.
    """

    def setUp(self):
        super().setUp()
        publish_page("Restricted search open page", "")
        self.group_page = publish_page("Restricted search group page", "")
        self.password_page = publish_page("Restricted search password page", "")

        self.board = Group.objects.create(name="Restricted search board")
        restriction = self.group_page.view_restrictions.create(restriction_type=PageViewRestriction.GROUPS)
        restriction.groups.add(self.board)

        self.password_restriction = self.password_page.view_restrictions.create(
            restriction_type=PageViewRestriction.PASSWORD,
            password="correct-horse-battery-staple",
        )

    def _search(self):
        return super()._search("restricted search")

    def test_a_group_page_is_withheld_from_a_reader_outside_the_group(self):
        self._sign_in("restricted-search-outsider")

        response = self._search()

        self.assertContains(response, "Restricted search open page")
        self.assertNotContains(response, "Restricted search group page")

    def test_a_group_page_is_shown_to_a_member_of_that_group(self):
        self._sign_in("restricted-search-member").groups.add(self.board)

        self.assertContains(self._search(), "Restricted search group page")

    def test_a_group_page_is_shown_to_a_superuser(self):
        self._sign_in("restricted-search-admin", is_superuser=True, is_staff=True)

        self.assertContains(self._search(), "Restricted search group page")

    def test_a_password_page_is_withheld_until_the_password_is_given(self):
        self._sign_in("restricted-search-reader")

        self.assertNotContains(self._search(), "Restricted search password page")

        self.client.post(
            reverse(
                "wagtailcore_authenticate_with_password",
                args=[self.password_restriction.id, self.password_page.id],
            ),
            {"password": "correct-horse-battery-staple", "return_url": self.password_page.url},
        )

        self.assertContains(self._search(), "Restricted search password page")

    def test_a_password_page_is_withheld_from_a_superuser_as_well(self):
        """Wagtail serves a superuser the password form like anybody else, so the search agrees."""
        self._sign_in("restricted-search-super", is_superuser=True, is_staff=True)

        self.assertNotContains(self._search(), "Restricted search password page")

    def test_a_page_below_a_restricted_one_is_withheld_too(self):
        """A restriction covers the branch, not the one page."""
        publish_page("Restricted search child page", "", parent=self.group_page)
        self._sign_in("restricted-search-passer-by")

        self.assertNotContains(self._search(), "Restricted search child page")

    def test_the_count_matches_what_the_reader_is_shown(self):
        """Counting what was filtered out would announce the pages by number."""
        self._sign_in("restricted-search-counter")

        results = self._search().context["results"]

        self.assertEqual(results.paginator.count, len(results.object_list))
        titles = {item.title for item in results.object_list}
        self.assertIn("Restricted search open page", titles)
        self.assertNotIn("Restricted search group page", titles)
        self.assertNotIn("Restricted search password page", titles)


class SearchRestrictionQueryCostTests(SearchViewTestCase):
    """The reader's groups are asked for once a search, not once a restriction."""

    def test_the_readers_groups_are_fetched_once_however_many_restrictions(self):
        board = Group.objects.create(name="Query cost board")
        for number in range(6):
            page = publish_page(f"Query cost page {number}", "")
            page.view_restrictions.create(restriction_type=PageViewRestriction.GROUPS).groups.add(board)
        self._sign_in("query-cost-reader")

        with CaptureQueriesContext(connection) as queries:
            self._search("query cost")

        # Django's own permission cache joins the same table to reach
        # auth_permission, which is a different question and asked once.
        group_lookups = [
            entry
            for entry in queries.captured_queries
            if "auth_user_groups" in entry["sql"] and "auth_permission" not in entry["sql"]
        ]
        self.assertEqual(len(group_lookups), 1, group_lookups)


class SearchResultsPageDisabledTests(SearchViewTestCase):
    """Settings > Search can turn the free-text results page off.

    The header box keeps its jump-to suggestions, but a submitted or bookmarked
    /search/?query= URL is not served (SearchSettings.enable_search_results_page).
    """

    def _disable_results_page(self):
        settings = SearchSettings.for_site(self.site)
        settings.enable_search_results_page = False
        settings.save()

    def test_results_page_is_served_by_default(self):
        self.assertEqual(self._search("anything").status_code, 200)

    def test_results_page_404s_when_free_text_results_are_disabled(self):
        self._disable_results_page()

        self.assertEqual(self._search("anything").status_code, 404)

    def test_suggestions_still_work_when_free_text_results_are_disabled(self):
        # The box relies on the suggest endpoint when results are off.
        self._disable_results_page()

        self.assertEqual(self.client.get(reverse("search_suggest"), {"q": "anything"}).status_code, 200)


@override_settings(FEATURE_FLAGS=feature_flags(skills=True))
class SearchSuggestTests(TestCase):
    """The header box's autocomplete: roles, then skills, then pages, each linking where the results page would."""

    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root_page = self.site.root_page.specific
        self.main_page = make_framework_main_page(self.root_page)

        self.role = GovukRole.objects.create(
            title="Data analyst",
            family="Data",
            body="<p>A data analyst collects, manages and shares data.</p>",
        )
        self.skill = GovukSkill.objects.create(
            title="Data visualisation",
            body="<p>Presenting data so it can be understood.</p>",
        )
        self.skills_page = self.main_page.add_child(instance=FrameworkSkillsPage(title="Skills A to Z", slug="skills"))
        self.skills_page.save_revision().publish()
        self.page = publish_page("Data protection guidance", "<p>How the framework handles personal data.</p>")

    def _suggest(self, **params):
        response = self.client.get("/search/suggest/", params)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        return response, response.json()

    def test_roles_then_skills_then_pages(self):
        _response, items = self._suggest(q="data")

        kinds = [item["type"] for item in items]
        self.assertIn("Role", kinds)
        self.assertIn("Skill", kinds)
        self.assertIn("Page", kinds)
        order = {"Role": 0, "Skill": 1, "Page": 2}
        self.assertEqual(kinds, sorted(kinds, key=order.__getitem__))

    def test_each_kind_links_where_the_results_page_would(self):
        _response, items = self._suggest(q="data")
        by_text = {item["text"]: item for item in items}

        self.assertEqual(by_text["Data analyst"]["link"], role_url(self.main_page, self.role))
        self.assertEqual(by_text["Data visualisation"]["link"], f"{self.skills_page.url}#{self.skill.slug}")
        self.assertEqual(by_text["Data protection guidance"]["link"], self.page.url)
        self.assertEqual(by_text["Data protection guidance"]["type"], "Page")

    def test_the_shape_is_the_one_the_autocomplete_consumes(self):
        _response, items = self._suggest(q="data")

        for item in items:
            self.assertEqual(set(item), {"text", "link", "type"})

    def test_fewer_than_two_characters_suggests_nothing(self):
        for params in ({"q": "d"}, {"q": " "}, {}):
            _response, items = self._suggest(**params)
            self.assertEqual(items, [], params)

    def test_the_results_pages_parameter_name_works_too(self):
        _response, items = self._suggest(query="data analyst")

        self.assertEqual(items[0]["text"], "Data analyst")

    def test_no_more_than_ten(self):
        for index in range(12):
            GovukRole.objects.create(title=f"Data role {index}", family="Data")

        _response, items = self._suggest(q="data")

        self.assertEqual(len(items), 10)
        # All ten are roles: the cap falls on the lowest-ranked kind first.
        self.assertEqual({item["type"] for item in items}, {"Role"})

    def test_external_content_is_not_suggested(self):
        source = ContentDiscoverySource.objects.create(
            settings=ContentDiscoverySettings.for_site(self.site),
            sort_order=0,
            name="Elsewhere",
            url="https://example.gov.uk/feed.xml",
        )
        ExternalContentItem.objects.create(
            source=source,
            url="https://example.gov.uk/data-elsewhere",
            title="Data guidance elsewhere",
            hidden=False,
        )

        _response, items = self._suggest(q="data")

        self.assertNotIn("Data guidance elsewhere", [item["text"] for item in items])

    def test_nothing_on_the_path_may_keep_a_copy(self):
        response, _items = self._suggest(q="data")

        self.assertEqual(response["Cache-Control"], "no-store")

    def test_only_get_and_head(self):
        self.assertEqual(self.client.post("/search/suggest/", {"q": "data"}).status_code, 405)
        self.assertEqual(self.client.head("/search/suggest/?q=data").status_code, 200)

    def test_the_header_box_says_where_the_suggestions_are(self):
        response = self.client.get(self.main_page.url)

        self.assertContains(response, 'data-suggest-url="/search/suggest/"')
        self.assertContains(response, 'data-min-length="2"')
        self.assertContains(response, "accessible-autocomplete-3.0.1.min.js")

    def test_the_library_is_served_from_this_site(self):
        """The content security policy allows scripts from here only."""
        self.assertIsNotNone(finders.find("accessible-autocomplete-3.0.1.min.js"))


@override_settings(FEATURE_FLAGS=feature_flags(skills=False))
class SearchSuggestWithoutTheFrameworkTests(TestCase):
    def test_only_pages_are_suggested(self):
        publish_page("Data guidance", "<p>Data.</p>")
        GovukRole.objects.create(title="Data analyst", family="Data")
        GovukSkill.objects.create(title="Data visualisation")

        items = self.client.get("/search/suggest/", {"q": "data"}).json()

        self.assertEqual([item["type"] for item in items], ["Page"])
        self.assertEqual(items[0]["text"], "Data guidance")


