from datetime import date

from django.test import TestCase
from wagtail.models import Site

from govuk.models import (
    ContentPage,
    GovukChangelogEntry,
    GovukRole,
    RolePage,
    site_wide_changelog,
)


class ContentPageRoleNavigationTests(TestCase):
    """A content page can carry the role navigation, as the welcome page does.

    Every other wagtail-govuk site has content pages that know nothing about
    roles, so it is off unless asked for.
    """

    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root_page = self.site.root_page.specific

        self.role = GovukRole.objects.create(title="Data analyst", family="Data")
        role_page = self.root_page.add_child(
            instance=RolePage(
                title="Data analyst",
                slug="data-analyst",
                selected_roles=[{"type": "role", "value": self.role.pk}],
            )
        )
        role_page.save_revision().publish()

        self.page = ContentPage(
            title="Welcome", slug="welcome", body="<p>Some words.</p>"
        )
        self.root_page.add_child(instance=self.page)
        self.page.save_revision().publish()

    def test_a_content_page_has_no_role_navigation_by_default(self):
        response = self.client.get(self.page.url)

        self.assertNotContains(response, "role-nav__list")
        self.assertContains(response, "govuk-grid-column-full")

    def test_the_role_navigation_can_be_switched_on(self):
        self.page.show_role_navigation = True
        self.page.save()

        response = self.client.get(self.page.url)

        self.assertContains(response, 'aria-label="Data roles"')
        self.assertContains(response, "Data analyst")
        self.assertContains(response, "govuk-grid-column-one-quarter")
        self.assertContains(response, "govuk-grid-column-three-quarters")

    def test_the_heading_navigation_is_untouched_on_its_own(self):
        self.page.enable_free_text_heading_navigation = True
        self.page.save()

        response = self.client.get(self.page.url)

        self.assertContains(response, "free-text-heading-nav")
        self.assertContains(response, "data-auto-heading-source")
        self.assertContains(response, "govuk-grid-column-two-thirds")
        self.assertNotContains(response, "role-nav__list")

    def test_only_one_side_column_is_ever_drawn(self):
        """Two side columns would not fit, so the role navigation wins."""
        self.page.show_role_navigation = True
        self.page.enable_free_text_heading_navigation = True
        self.page.save()

        response = self.client.get(self.page.url)

        self.assertContains(response, "role-nav__list")
        self.assertNotContains(response, "free-text-heading-nav")
        self.assertNotContains(response, "govuk-grid-column-one-third")

    def test_the_heading_moves_beside_the_navigation(self):
        """With the navigation alongside, the hero would leave it stranded."""
        self.assertContains(self.client.get(self.page.url), "hero__title")

        self.page.show_role_navigation = True
        self.page.save()

        response = self.client.get(self.page.url)

        self.assertNotContains(response, "hero__title")
        self.assertContains(response, '<h1 class="govuk-heading-xl">Welcome</h1>')


class FrameworkUpdatesTests(TestCase):
    """Changelog entries with no role or skill belong to the framework itself."""

    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.page = ContentPage(
            title="Welcome", slug="welcome", body="<p>Some words.</p>"
        )
        self.site.root_page.specific.add_child(instance=self.page)
        self.page.save_revision().publish()

        self.role = GovukRole.objects.create(title="Data analyst", family="Data")
        GovukChangelogEntry.objects.create(
            date=date(2017, 3, 23), note="<p>The framework was published.</p>"
        )
        GovukChangelogEntry.objects.create(
            date=date(2026, 5, 29), note="<p>Content designer skills changed.</p>"
        )
        GovukChangelogEntry.objects.create(
            date=date(2025, 1, 1),
            role=self.role,
            note="<p>Data analyst skills changed.</p>",
        )

    def test_only_entries_without_a_role_or_skill_count_as_site_wide(self):
        changelog = site_wide_changelog()

        self.assertEqual(len(changelog["entries"]), 2)
        self.assertEqual(changelog["published_date"], date(2017, 3, 23))
        self.assertEqual(changelog["last_updated_date"], date(2026, 5, 29))

    def test_the_updates_are_hidden_unless_asked_for(self):
        response = self.client.get(self.page.url)

        self.assertNotContains(response, "See all updates")
        self.assertNotContains(response, "The framework was published.")

    def test_the_updates_show_with_a_jump_link_above_the_content(self):
        self.page.show_framework_updates = True
        self.page.save()

        response = self.client.get(self.page.url)

        self.assertContains(response, "Last updated 29 May 2026")
        self.assertContains(response, 'href="#update-history"')
        self.assertContains(response, 'id="update-history"')
        self.assertContains(response, "Published 23 March 2017")
        self.assertContains(response, "The framework was published.")
        # Entries about a single role stay on that role's page.
        self.assertNotContains(response, "Data analyst skills changed.")
