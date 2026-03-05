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
    THIS_SITE_SOURCE_FILTER,
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
        self.this_site_source_filter_label = (
            f"{self.site.site_name} (this site)" if self.site.site_name else "This site"
        )
        self.this_site_source_display_label = self.site.site_name or "This site"

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

    def test_source_and_tag_display_are_disabled_by_default(self):
        self.assertFalse(self.listings_page.enable_source_display)
        self.assertFalse(self.listings_page.enable_tag_display)

    def test_get_listing_queryset_includes_tags_for_external_and_internal_items(self):
        items = self.listings_page.get_listing_queryset()
        item_by_url = {item["url"]: item for item in items}

        self.assertEqual(item_by_url[self.external_alpha.url]["tags"], ["Alpha"])
        self.assertEqual(item_by_url[self.content_page_alpha.url]["tags"], ["Alpha"])
        self.assertEqual(item_by_url[self.section_page_beta.url]["tags"], ["Beta"])

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

    def test_get_listing_queryset_applies_this_site_source_to_internal_only(self):
        items = self.listings_page.get_listing_queryset(
            selected_source_id=THIS_SITE_SOURCE_FILTER
        )
        urls = {item["url"] for item in items}

        self.assertIn(self.content_page_alpha.url, urls)
        self.assertIn(self.section_page_beta.url, urls)
        self.assertIn(self.role_page_alpha.url, urls)
        self.assertNotIn(self.external_alpha.url, urls)
        self.assertNotIn(self.external_beta.url, urls)

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

    def test_tag_listings_source_filter_includes_this_site_option(self):
        self.listings_page.enable_source_filter = True
        self.listings_page.save_revision().publish()

        response = self.client.get(self.listings_page.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.this_site_source_filter_label)
        self.assertContains(response, 'value="__this_site__"')

    def test_tag_listings_this_site_source_filter_excludes_external_results(self):
        self.listings_page.enable_source_filter = True
        self.listings_page.save_revision().publish()

        response = self.client.get(
            self.listings_page.url,
            {"source": THIS_SITE_SOURCE_FILTER},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Alpha page")
        self.assertContains(response, "Beta section")
        self.assertContains(response, "Alpha role page")
        self.assertNotContains(response, "Alpha external")
        self.assertNotContains(response, "Beta external")

    def test_tag_listings_show_this_site_source_tag_for_internal_results(self):
        self.listings_page.enable_source_display = True
        self.listings_page.save_revision().publish()

        response = self.client.get(self.listings_page.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f'<strong class="govuk-tag">{self.this_site_source_display_label}</strong>',
            count=3,
            html=True,
        )

    def test_tag_filter_page_response_excludes_private_pages_for_anonymous_users(self):
        self.listings_page.enable_tag_filter = True
        self.listings_page.save_revision().publish()

        response = self.client.get(self.listings_page.url, {"tag": self.alpha_tag.slug})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Alpha external")
        self.assertContains(response, "Alpha page")
        self.assertContains(response, "Alpha role page")
        self.assertNotContains(response, "Private alpha page")

    def test_get_listing_queryset_includes_private_pages_for_anonymous_users_when_enabled(
        self,
    ):
        self.listings_page.show_private_cards_to_non_authenticated_users = True
        self.listings_page.save_revision().publish()
        self.listings_page = self.listings_page.specific

        items = self.listings_page.get_listing_queryset(selected_tag_id=self.alpha_tag.id)
        urls = {item["url"] for item in items}

        self.assertIn(self.content_page_alpha.url, urls)
        self.assertIn(self.private_alpha_page.url, urls)
        self.assertIn(self.role_page_alpha.url, urls)

    def test_tag_filter_page_response_includes_private_pages_for_anonymous_users_when_enabled(
        self,
    ):
        self.listings_page.enable_tag_filter = True
        self.listings_page.show_private_cards_to_non_authenticated_users = True
        self.listings_page.save_revision().publish()
        self.listings_page = self.listings_page.specific

        response = self.client.get(self.listings_page.url, {"tag": self.alpha_tag.slug})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Private alpha page")
        self.assertContains(
            response,
            '<strong class="govuk-tag govuk-tag--grey">Private</strong>',
            count=1,
            html=True,
        )

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

        item_by_url = {item["url"]: item for item in items}
        self.assertFalse(item_by_url[self.external_alpha.url]["private"])
        self.assertTrue(item_by_url[private_external.url]["private"])

    def test_get_listing_queryset_marks_private_internal_pages_for_authenticated_users(
        self,
    ):
        user = get_user_model().objects.create_user(
            username="tag-listing-page-private-user",
            password="password",
        )
        request = RequestFactory().get(self.listings_page.url)
        request.user = user

        items = self.listings_page.get_listing_queryset(
            selected_tag_id=self.alpha_tag.id,
            request=request,
        )
        item_by_url = {item["url"]: item for item in items}

        self.assertFalse(item_by_url[self.content_page_alpha.url]["private"])
        self.assertTrue(item_by_url[self.private_alpha_page.url]["private"])

    def test_tag_filter_page_response_shows_private_badges(self):
        self.listings_page.enable_tag_filter = True
        self.listings_page.enable_tag_display = True
        self.listings_page.save_revision().publish()

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

        user = get_user_model().objects.create_user(
            username="tag-listing-private-badge-user",
            password="password",
        )
        self.client.force_login(user)

        response = self.client.get(self.listings_page.url, {"tag": self.alpha_tag.slug})

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            '<strong class="govuk-tag govuk-tag--grey">Private</strong>',
            count=2,
            html=True,
        )

    def test_available_filter_tags_include_tags_present_on_in_scope_items(self):
        alpha_gamma_external = ExternalContentItem.objects.create(
            source=self.source_one,
            url="https://example.gov.uk/alpha-gamma",
            title="Alpha gamma external",
            hidden=False,
        )
        ExternalContentItemTag.objects.create(
            content_object=alpha_gamma_external,
            tag=self.alpha_tag,
        )
        ExternalContentItemTag.objects.create(
            content_object=alpha_gamma_external,
            tag=self.gamma_tag,
        )

        available_tags = self.listings_page._available_filter_tags(
            tag_ids=self.listings_page._configured_tag_ids(),
        )
        available_slugs = {tag.slug for tag in available_tags}

        self.assertIn(self.alpha_tag.slug, available_slugs)
        self.assertIn(self.beta_tag.slug, available_slugs)
        self.assertIn(self.gamma_tag.slug, available_slugs)

    def test_get_listing_queryset_supports_filtering_by_non_configured_scoped_tag(self):
        alpha_gamma_external = ExternalContentItem.objects.create(
            source=self.source_one,
            url="https://example.gov.uk/alpha-gamma",
            title="Alpha gamma external",
            hidden=False,
        )
        ExternalContentItemTag.objects.create(
            content_object=alpha_gamma_external,
            tag=self.alpha_tag,
        )
        ExternalContentItemTag.objects.create(
            content_object=alpha_gamma_external,
            tag=self.gamma_tag,
        )

        items = self.listings_page.get_listing_queryset(selected_tag_id=self.gamma_tag.id)
        urls = {item["url"] for item in items}

        self.assertIn(alpha_gamma_external.url, urls)
        self.assertNotIn(self.external_gamma.url, urls)
        self.assertNotIn(self.gamma_page.url, urls)
