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
