from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from wagtail.models import PageViewRestriction, Site

from govuk.models import ContentPage


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
