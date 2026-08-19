from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils.text import slugify
from wagtail.models import PageViewRestriction, Site

from govuk.models import (
    ContentDiscoverySettings,
    ContentDiscoverySource,
    ContentPage,
    ExternalContentItem,
    ExternalContentItemTag,
    GovukTag,
    SectionPage,
)
from govuk.search_backend import search_backend


class SearchViewVisibilityTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root_page = self.site.root_page.specific

        self.public_page = self.root_page.add_child(
            instance=ContentPage(
                title="Search visibility public page",
                slug="search-visibility-public-page",
                body="",
            )
        )
        self.public_page.save_revision().publish()

        self.private_page = self.root_page.add_child(
            instance=ContentPage(
                title="Search visibility private page",
                slug="search-visibility-private-page",
                body="",
            )
        )
        self.private_page.save_revision().publish()
        self.private_page.view_restrictions.create(
            restriction_type=PageViewRestriction.LOGIN
        )

    def test_search_only_shows_public_pages_to_anonymous_users(self):
        response = self.client.get(reverse("search"), {"query": "search visibility"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Search visibility public page")
        self.assertNotContains(response, "Search visibility private page")

    def test_search_shows_public_and_private_pages_to_logged_in_users(self):
        user = get_user_model().objects.create_user(
            username="search-visibility-user",
            password="password",
        )
        self.client.force_login(user)

        response = self.client.get(reverse("search"), {"query": "search visibility"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Search visibility public page")
        self.assertContains(response, "Search visibility private page")


class SearchViewFilterTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root_page = self.site.root_page.specific
        self.this_site_source_label = (
            f"{self.site.site_name} (this site)"
            if self.site.site_name
            else "This site"
        )
        self.this_site_result_source_tag_label = self.site.site_name or "This site"

        self.alpha_tag = GovukTag.objects.create(slug="alpha", name="Alpha")
        self.beta_tag = GovukTag.objects.create(slug="beta", name="Beta")

        self.alpha_page = self.root_page.add_child(
            instance=ContentPage(
                title="Filterable alpha page",
                slug="filterable-alpha-page",
                body="",
            )
        )
        self.alpha_page.tags.add(self.alpha_tag)
        self.alpha_page.save_revision().publish()

        self.beta_section = self.root_page.add_child(
            instance=SectionPage(
                title="Filterable beta section",
                slug="filterable-beta-section",
                hero_intro="",
                rows=[],
                free_text="",
            )
        )
        self.beta_section.tags.add(self.beta_tag)
        self.beta_section.save_revision().publish()

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

        self.alpha_external = ExternalContentItem.objects.create(
            source=self.source_one,
            url="https://example.gov.uk/filterable-alpha",
            title="Filterable alpha external",
            hidden=False,
        )
        ExternalContentItemTag.objects.create(
            content_object=self.alpha_external,
            tag=self.alpha_tag,
        )

        self.beta_external = ExternalContentItem.objects.create(
            source=self.source_two,
            url="https://example.gov.uk/filterable-beta",
            title="Filterable beta external",
            hidden=False,
        )
        ExternalContentItemTag.objects.create(
            content_object=self.beta_external,
            tag=self.beta_tag,
        )

    def test_search_shows_filter_controls_when_results_have_tags_and_sources(self):
        response = self.client.get(reverse("search"), {"query": "filterable"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Filter by tag")
        self.assertContains(response, "Filter by source")
        self.assertContains(response, "Alpha")
        self.assertContains(response, "Source One")
        self.assertContains(response, self.this_site_source_label)

    def test_search_tag_filter_limits_results_to_matching_tag(self):
        response = self.client.get(
            reverse("search"),
            {"query": "filterable", "tag": self.alpha_tag.slug},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Filterable alpha page")
        self.assertContains(response, "Filterable alpha external")
        self.assertNotContains(response, "Filterable beta section")
        self.assertNotContains(response, "Filterable beta external")

    def test_search_source_filter_limits_results_to_matching_source(self):
        response = self.client.get(
            reverse("search"),
            {"query": "filterable", "source": str(self.source_two.id)},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Filterable beta external")
        self.assertNotContains(response, "Filterable alpha external")
        self.assertNotContains(response, "Filterable alpha page")
        self.assertNotContains(response, "Filterable beta section")

    def test_search_this_site_source_filter_excludes_external_results(self):
        response = self.client.get(
            reverse("search"),
            {"query": "filterable", "source": "__this_site__"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Filterable alpha page")
        self.assertContains(response, "Filterable beta section")
        self.assertNotContains(response, "Filterable alpha external")
        self.assertNotContains(response, "Filterable beta external")

    def test_search_results_show_this_site_source_tag_for_internal_results(self):
        response = self.client.get(reverse("search"), {"query": "filterable"})

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

        response = self.client.get(reverse("search"), {"query": "hero title fallback"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Preferred hero page title")
        self.assertNotContains(response, "Hero title fallback page")


class SearchResultsLayoutTests(TestCase):
    """The results read as one column of entries under the Design System's pagination."""

    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root_page = self.site.root_page.specific
        self.paginated_tag = GovukTag.objects.create(
            slug="paginated", name="Paginated"
        )

        # A page of results holds 15, so 20 pages give a second page to go to.
        for index in range(1, 21):
            page = self.root_page.add_child(
                instance=ContentPage(
                    title=f"Paginated result {index}",
                    slug=f"paginated-result-{index}",
                    body="",
                )
            )
            page.tags.add(self.paginated_tag)
            page.save_revision().publish()

        self.single_result = self.root_page.add_child(
            instance=ContentPage(
                title="Solitary result page",
                slug="solitary-result-page",
                body="",
            )
        )
        self.single_result.save_revision().publish()

    def test_each_result_is_an_entry_in_one_list(self):
        response = self.client.get(reverse("search"), {"query": "solitary"})

        self.assertContains(response, 'class="govuk-list app-search-results"')
        self.assertContains(response, 'class="app-search-result"', count=1)

    def test_pagination_offers_the_pages_a_long_result_set_runs_to(self):
        response = self.client.get(reverse("search"), {"query": "paginated result"})

        self.assertContains(response, "govuk-pagination")
        self.assertContains(response, 'aria-label="Page 1"')
        self.assertContains(response, 'aria-label="Page 2"')
        self.assertContains(response, 'aria-current="page"')
        self.assertContains(response, "page=2")

    def test_the_page_a_reader_is_on_is_the_current_one(self):
        response = self.client.get(
            reverse("search"), {"query": "paginated result", "page": 2}
        )

        self.assertContains(
            response,
            '<li class="govuk-pagination__item govuk-pagination__item--current">'
            '<a class="govuk-link govuk-pagination__link" '
            'href="?query=paginated+result&amp;page=2" aria-label="Page 2" '
            'aria-current="page">2</a></li>',
            html=True,
        )

    def test_a_page_link_keeps_the_search_and_the_filters(self):
        response = self.client.get(
            reverse("search"),
            {"query": "paginated result", "tag": self.paginated_tag.slug},
        )

        self.assertContains(
            response, "?query=paginated+result&amp;tag=paginated&amp;page=2"
        )

    def test_a_filter_that_matched_nothing_is_not_carried_into_the_page_links(self):
        """The search drops a tag it cannot offer, so a link still carrying it
        would show a filtered address over unfiltered results."""
        response = self.client.get(
            reverse("search"), {"query": "paginated result", "tag": "no-such-tag"}
        )

        self.assertContains(response, "?query=paginated+result&amp;page=2")
        self.assertNotContains(response, "tag=no-such-tag")

    def test_there_is_no_pagination_where_everything_fits_on_one_page(self):
        response = self.client.get(reverse("search"), {"query": "solitary"})

        self.assertContains(response, "Solitary result page")
        self.assertNotContains(response, "govuk-pagination")


class SearchViewRestrictedPageTests(TestCase):
    """A search result names a page and summarises it, so a page held back
    from this reader has no business appearing in one.

    ``LOGIN`` is covered above: signing in is the whole of what it asks, so a
    signed-in reader sees those. The two below ask for more than an account.
    """

    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root_page = self.site.root_page.specific

        self.open_page = self._publish("Restricted search open page")
        self.group_page = self._publish("Restricted search group page")
        self.password_page = self._publish("Restricted search password page")

        self.board = Group.objects.create(name="Restricted search board")
        restriction = self.group_page.view_restrictions.create(
            restriction_type=PageViewRestriction.GROUPS
        )
        restriction.groups.add(self.board)

        self.password_restriction = self.password_page.view_restrictions.create(
            restriction_type=PageViewRestriction.PASSWORD,
            password="correct-horse-battery-staple",
        )

    def _publish(self, title, parent=None):
        page = (parent or self.root_page).add_child(
            instance=ContentPage(title=title, slug=slugify(title), body="")
        )
        page.save_revision().publish()
        return page

    def _search(self):
        return self.client.get(reverse("search"), {"query": "restricted search"})

    def _sign_in(self, username, **kwargs):
        user = get_user_model().objects.create_user(
            username=username, password="password", **kwargs
        )
        self.client.force_login(user)
        return user

    def test_a_group_page_is_withheld_from_a_reader_outside_the_group(self):
        self._sign_in("restricted-search-outsider")

        response = self._search()

        self.assertContains(response, "Restricted search open page")
        self.assertNotContains(response, "Restricted search group page")

    def test_a_group_page_is_shown_to_a_member_of_that_group(self):
        user = self._sign_in("restricted-search-member")
        user.groups.add(self.board)

        response = self._search()

        self.assertContains(response, "Restricted search group page")

    def test_a_group_page_is_shown_to_a_superuser(self):
        self._sign_in("restricted-search-admin", is_superuser=True, is_staff=True)

        response = self._search()

        self.assertContains(response, "Restricted search group page")

    def test_a_password_page_is_withheld_until_the_password_is_given(self):
        self._sign_in("restricted-search-reader")

        self.assertNotContains(self._search(), "Restricted search password page")

        self.client.post(
            reverse(
                "wagtailcore_authenticate_with_password",
                args=[self.password_restriction.id, self.password_page.id],
            ),
            {
                "password": "correct-horse-battery-staple",
                "return_url": self.password_page.url,
            },
        )

        self.assertContains(self._search(), "Restricted search password page")

    def test_a_password_page_is_withheld_from_a_superuser_as_well(self):
        """Wagtail serves a superuser the password form like anybody else, so
        the search says the same thing the page would."""
        self._sign_in("restricted-search-super", is_superuser=True, is_staff=True)

        self.assertNotContains(self._search(), "Restricted search password page")

    def test_a_page_below_a_restricted_one_is_withheld_too(self):
        """A restriction covers the branch, not the one page, and so does the
        page it hides behind it."""
        self._publish("Restricted search child page", parent=self.group_page)
        self._sign_in("restricted-search-passer-by")

        response = self._search()

        self.assertNotContains(response, "Restricted search child page")

    def test_the_count_matches_what_the_reader_is_shown(self):
        """Counting what was filtered out would announce the pages by number."""
        self._sign_in("restricted-search-counter")

        response = self._search()

        self.assertEqual(
            response.context["results"].paginator.count,
            len(response.context["results"].object_list),
        )
        titles = {item.title for item in response.context["results"].object_list}
        self.assertIn("Restricted search open page", titles)
        self.assertNotIn("Restricted search group page", titles)
        self.assertNotIn("Restricted search password page", titles)


class SearchBackendWithoutARequestTests(TestCase):
    """``public=False`` says the caller will decide who may see what. Asked
    without a request there is no reader to decide about, so nothing
    restricted is let through."""

    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        root = self.site.root_page.specific
        self.open_page = root.add_child(
            instance=ContentPage(
                title="Requestless search open page",
                slug="requestless-search-open-page",
                body="",
            )
        )
        self.open_page.save_revision().publish()
        self.shut_page = root.add_child(
            instance=ContentPage(
                title="Requestless search shut page",
                slug="requestless-search-shut-page",
                body="",
            )
        )
        self.shut_page.save_revision().publish()
        self.shut_page.view_restrictions.create(
            restriction_type=PageViewRestriction.GROUPS
        ).groups.add(Group.objects.create(name="Requestless search board"))

    def test_nothing_restricted_comes_back_without_a_request(self):
        results = search_backend.search(
            "requestless search",
            filters={"site": self.site, "public": False},
            page=1,
        )
        titles = {item.title for item in results.object_list}

        self.assertIn("Requestless search open page", titles)
        self.assertNotIn("Requestless search shut page", titles)


class SearchRestrictionQueryCostTests(TestCase):
    """The reader's groups are asked for once a search, not once a restriction."""

    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        root = self.site.root_page.specific
        board = Group.objects.create(name="Query cost board")
        for number in range(6):
            page = root.add_child(
                instance=ContentPage(
                    title=f"Query cost page {number}",
                    slug=f"query-cost-page-{number}",
                    body="",
                )
            )
            page.save_revision().publish()
            page.view_restrictions.create(
                restriction_type=PageViewRestriction.GROUPS
            ).groups.add(board)

    def test_the_readers_groups_are_fetched_once_however_many_restrictions(self):
        user = get_user_model().objects.create_user(
            username="query-cost-reader", password="password"
        )
        self.client.force_login(user)

        with CaptureQueriesContext(connection) as queries:
            self.client.get(reverse("search"), {"query": "query cost"})

        # Django's own permission cache joins the same table to reach
        # auth_permission, which is a different question and asked once.
        group_lookups = [
            entry
            for entry in queries.captured_queries
            if "auth_user_groups" in entry["sql"]
            and "auth_permission" not in entry["sql"]
        ]
        self.assertEqual(len(group_lookups), 1, group_lookups)
