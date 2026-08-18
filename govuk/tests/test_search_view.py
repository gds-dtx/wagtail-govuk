from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
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

    def test_there_is_no_pagination_where_everything_fits_on_one_page(self):
        response = self.client.get(reverse("search"), {"query": "solitary"})

        self.assertContains(response, "Solitary result page")
        self.assertNotContains(response, "govuk-pagination")
