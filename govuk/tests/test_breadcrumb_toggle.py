"""The per-page show_breadcrumbs switch on the non-framework page types."""

from django.test import TestCase, override_settings
from wagtail.models import Site

from govuk.models import ContentPage
from govuk.models.pages import HeroStyle


def _feature_flags() -> dict[str, bool]:
    return {
        "SKILLS": False,
        "FEEDBACK": False,
    }


@override_settings(FEATURE_FLAGS=_feature_flags())
class BreadcrumbToggleTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root = self.site.root_page.specific

    def _child(self, **kwargs):
        # A child of the site root has a non-empty ancestor trail.
        page = self.root.add_child(
            instance=ContentPage(title="Guidance", slug="guidance", body="", **kwargs)
        )
        page.save_revision().publish()
        return self.client.get(page.url)

    def test_shown_by_default_in_the_normal_position(self):
        response = self._child(show_breadcrumbs=True)
        self.assertContains(response, "govuk-breadcrumbs")
        self.assertNotContains(response, "govuk-breadcrumbs--inverse")

    def test_hidden_when_switched_off(self):
        response = self._child(show_breadcrumbs=False)
        self.assertNotContains(response, "govuk-breadcrumbs")

    def test_combined_page_shows_inverse_breadcrumbs_when_enabled(self):
        response = self._child(
            hero_style=HeroStyle.COMBINED, show_breadcrumbs=True
        )
        self.assertContains(response, "govuk-breadcrumbs--inverse")

    def test_combined_page_shows_none_when_disabled(self):
        response = self._child(
            hero_style=HeroStyle.COMBINED, show_breadcrumbs=False
        )
        self.assertNotContains(response, "govuk-breadcrumbs")

    def test_the_site_root_still_has_no_breadcrumbs(self):
        # No ancestor trail on the home page, so nothing renders regardless.
        response = self.client.get(self.root.url)
        self.assertNotContains(response, "govuk-breadcrumbs")
