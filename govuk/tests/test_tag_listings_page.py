from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase, override_settings
from wagtail.models import PageViewRestriction, Site

from govuk.models import (
    ContentDiscoverySettings,
    ContentDiscoverySource,
    ContentPage,
    ExternalContentItem,
    ExternalContentItemTag,
    GovukTag,
    RolePage,
    SectionPage,
    TagListingsPage,
)


def _feature_flags(*, skills_enabled: bool) -> dict[str, bool]:
    return {
        "SKILLS": skills_enabled,
        "ORGANISATIONS": False,
        "PEOPLE_FINDER": False,
        "FEEDBACK": False,
    }


@override_settings(FEATURE_FLAGS=_feature_flags(skills_enabled=True))
class TagListingsPageQuerysetTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root_page = self.site.root_page.specific

        self.alpha_tag = GovukTag.objects.create(slug="alpha", name="Alpha")
        self.beta_tag = GovukTag.objects.create(slug="beta", name="Beta")
        self.gamma_tag = GovukTag.objects.create(slug="gamma", name="Gamma")

        self.listings_page = self.root_page.add_child(
            instance=TagListingsPage(title="Tagged listings", slug="tagged-listings")
        )
        self.listings_page.tags.add(self.alpha_tag, self.beta_tag)
        self.listings_page.save_revision().publish()
        self.listings_page = self.listings_page.specific

        self.content_page_alpha = self.root_page.add_child(
            instance=ContentPage(title="Alpha page", slug="alpha-page", body="")
        )
        self.content_page_alpha.tags.add(self.alpha_tag)
        self.content_page_alpha.save_revision().publish()

        self.private_alpha_page = self.root_page.add_child(
            instance=ContentPage(
                title="Private alpha page",
                slug="private-alpha-page",
                body="",
            )
        )
        self.private_alpha_page.tags.add(self.alpha_tag)
        self.private_alpha_page.save_revision().publish()
        self.private_alpha_page.view_restrictions.create(
            restriction_type=PageViewRestriction.LOGIN
        )

        self.section_page_beta = self.root_page.add_child(
            instance=SectionPage(
                title="Beta section",
                slug="beta-section",
                hero_intro="",
                rows=[],
                free_text="",
            )
        )
        self.section_page_beta.tags.add(self.beta_tag)
        self.section_page_beta.save_revision().publish()

        self.role_page_alpha = self.root_page.add_child(
            instance=RolePage(
                title="Alpha role page",
                slug="alpha-role-page",
                body="",
            )
        )
        self.role_page_alpha.tags.add(self.alpha_tag)
        self.role_page_alpha.save_revision().publish()

        self.gamma_page = self.root_page.add_child(
            instance=ContentPage(title="Gamma page", slug="gamma-page", body="")
        )
        self.gamma_page.tags.add(self.gamma_tag)
        self.gamma_page.save_revision().publish()

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

        self.external_alpha = ExternalContentItem.objects.create(
            source=self.source_one,
            url="https://example.gov.uk/alpha",
            title="Alpha external",
            hidden=False,
        )
        ExternalContentItemTag.objects.create(
            content_object=self.external_alpha,
            tag=self.alpha_tag,
        )

        self.external_beta = ExternalContentItem.objects.create(
            source=self.source_two,
            url="https://example.gov.uk/beta",
            title="Beta external",
            hidden=False,
        )
        ExternalContentItemTag.objects.create(
            content_object=self.external_beta,
            tag=self.beta_tag,
        )

        self.external_gamma = ExternalContentItem.objects.create(
            source=self.source_one,
            url="https://example.gov.uk/gamma",
            title="Gamma external",
            hidden=False,
        )
        ExternalContentItemTag.objects.create(
            content_object=self.external_gamma,
            tag=self.gamma_tag,
        )

    def test_get_listing_queryset_combines_external_and_tagged_pages(self):
        items = self.listings_page.get_listing_queryset()
        urls = {item["url"] for item in items}

        self.assertIn(self.external_alpha.url, urls)
        self.assertIn(self.external_beta.url, urls)
        self.assertIn(self.content_page_alpha.url, urls)
        self.assertIn(self.section_page_beta.url, urls)
        self.assertIn(self.role_page_alpha.url, urls)

        self.assertNotIn(self.external_gamma.url, urls)
        self.assertNotIn(self.gamma_page.url, urls)
        self.assertNotIn(self.private_alpha_page.url, urls)

    def test_get_listing_queryset_applies_selected_tag_to_external_and_pages(self):
        items = self.listings_page.get_listing_queryset(selected_tag_id=self.beta_tag.id)
        urls = {item["url"] for item in items}

        self.assertEqual(urls, {self.external_beta.url, self.section_page_beta.url})

    def test_get_listing_queryset_applies_selected_tag_to_role_pages(self):
        items = self.listings_page.get_listing_queryset(selected_tag_id=self.alpha_tag.id)
        urls = {item["url"] for item in items}

        self.assertIn(self.external_alpha.url, urls)
        self.assertIn(self.content_page_alpha.url, urls)
        self.assertIn(self.role_page_alpha.url, urls)
        self.assertNotIn(self.section_page_beta.url, urls)

    def test_get_listing_queryset_applies_selected_source_to_external_only(self):
        items = self.listings_page.get_listing_queryset(
            selected_source_id=self.source_one.id
        )
        urls = {item["url"] for item in items}

        self.assertEqual(urls, {self.external_alpha.url})
        self.assertTrue(all(item["source"] is not None for item in items))

    def test_get_listing_queryset_can_sort_alphabetically(self):
        self.listings_page.sort_order = TagListingsPage.SortOrder.ALPHABETICAL
        self.listings_page.save_revision().publish()
        self.listings_page = self.listings_page.specific

        items = self.listings_page.get_listing_queryset()
        titles = [item["title"] or item["url"] for item in items]
        expected_titles = sorted(
            titles,
            key=lambda value: (value or "").strip().lower(),
        )

        self.assertEqual(titles, expected_titles)

    def test_tag_filter_page_response_includes_external_and_internal_results(self):
        self.listings_page.enable_tag_filter = True
        self.listings_page.save_revision().publish()

        response = self.client.get(self.listings_page.url, {"tag": self.beta_tag.slug})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Beta external")
        self.assertContains(response, "Beta section")
        self.assertNotContains(response, "Alpha external")
        self.assertNotContains(response, "Alpha page")

    def test_tag_listings_page_shows_last_updated_by_default(self):
        response = self.client.get(self.listings_page.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Last updated:")

    def test_tag_listings_page_can_hide_last_updated(self):
        self.listings_page.hide_last_updated = True
        self.listings_page.save_revision().publish()

        response = self.client.get(self.listings_page.url)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Last updated:")

    def test_tag_filter_page_response_excludes_private_pages_for_anonymous_users(self):
        self.listings_page.enable_tag_filter = True
        self.listings_page.save_revision().publish()

        response = self.client.get(self.listings_page.url, {"tag": self.alpha_tag.slug})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Alpha external")
        self.assertContains(response, "Alpha page")
        self.assertContains(response, "Alpha role page")
        self.assertNotContains(response, "Private alpha page")

    def test_get_listing_queryset_excludes_private_external_items_for_anonymous_users(
        self,
    ):
        private_external = ExternalContentItem.objects.create(
            source=self.source_one,
            url="https://example.gov.uk/private-alpha-external",
            title="Private alpha external",
            hidden=False,
            private=True,
        )
        ExternalContentItemTag.objects.create(
            content_object=private_external,
            tag=self.alpha_tag,
        )
        hidden_private_external = ExternalContentItem.objects.create(
            source=self.source_one,
            url="https://example.gov.uk/hidden-private-alpha-external",
            title="Hidden private alpha external",
            hidden=True,
            private=True,
        )
        ExternalContentItemTag.objects.create(
            content_object=hidden_private_external,
            tag=self.alpha_tag,
        )

        items = self.listings_page.get_listing_queryset(selected_tag_id=self.alpha_tag.id)
        urls = {item["url"] for item in items}

        self.assertIn(self.external_alpha.url, urls)
        self.assertNotIn(private_external.url, urls)
        self.assertNotIn(hidden_private_external.url, urls)

    def test_tag_filter_page_response_includes_private_pages_for_logged_in_users(self):
        self.listings_page.enable_tag_filter = True
        self.listings_page.save_revision().publish()

        user = get_user_model().objects.create_user(
            username="tag-listing-user",
            password="password",
        )
        self.client.force_login(user)

        response = self.client.get(self.listings_page.url, {"tag": self.alpha_tag.slug})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Alpha external")
        self.assertContains(response, "Alpha page")
        self.assertContains(response, "Private alpha page")

    def test_get_listing_queryset_includes_private_external_items_for_authenticated_users(
        self,
    ):
        private_external = ExternalContentItem.objects.create(
            source=self.source_one,
            url="https://example.gov.uk/private-alpha-external",
            title="Private alpha external",
            hidden=False,
            private=True,
        )
        ExternalContentItemTag.objects.create(
            content_object=private_external,
            tag=self.alpha_tag,
        )
        hidden_private_external = ExternalContentItem.objects.create(
            source=self.source_one,
            url="https://example.gov.uk/hidden-private-alpha-external",
            title="Hidden private alpha external",
            hidden=True,
            private=True,
        )
        ExternalContentItemTag.objects.create(
            content_object=hidden_private_external,
            tag=self.alpha_tag,
        )

        user = get_user_model().objects.create_user(
            username="tag-listing-external-private-user",
            password="password",
        )
        request = RequestFactory().get(self.listings_page.url)
        request.user = user

        items = self.listings_page.get_listing_queryset(
            selected_tag_id=self.alpha_tag.id,
            request=request,
        )
        urls = {item["url"] for item in items}

        self.assertIn(self.external_alpha.url, urls)
        self.assertIn(private_external.url, urls)
        self.assertNotIn(hidden_private_external.url, urls)
