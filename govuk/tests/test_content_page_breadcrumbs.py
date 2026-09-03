"""Which breadcrumb a content page carries, and on which kind of site.

The framework replaces the ancestor trail with its own two-crumb one, hidden
until the side navigation is, because its root page is named after the service
in a full sentence and the live service shows no breadcrumb on a wide screen.
Every other instance builds with this same page type and nests more deeply
than the framework does, so the trail the context processor assembles has to
survive there.
"""

from django.test import TestCase, override_settings
from wagtail.models import Site

from govuk.models import ContentPage


def _feature_flags(*, skills_enabled: bool) -> dict[str, bool]:
    return {
        "SKILLS": skills_enabled,
        "ORGANISATIONS": False,
        "PEOPLE_FINDER": False,
        "FEEDBACK": False,
    }


class ContentPageBreadcrumbTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root_page = self.site.root_page.specific

        self.section = self.root_page.add_child(
            instance=ContentPage(title="Guidance", slug="guidance", body="")
        )
        self.section.save_revision().publish()

        self.nested = self.section.add_child(
            instance=ContentPage(
                title="Keeping records", slug="keeping-records", body=""
            )
        )
        self.nested.save_revision().publish()

    @override_settings(FEATURE_FLAGS=_feature_flags(skills_enabled=True))
    def test_framework_replaces_the_ancestor_trail_and_hides_it_on_a_wide_screen(self):
        response = self.client.get(self.nested.url)

        self.assertEqual(
            [crumb["title"] for crumb in response.context["breadcrumbs"]],
            ["Home", "Keeping records"],
        )
        self.assertTrue(response.context["breadcrumbs_mobile_only"])

    @override_settings(FEATURE_FLAGS=_feature_flags(skills_enabled=False))
    def test_without_the_framework_the_ancestor_trail_is_left_alone(self):
        response = self.client.get(self.nested.url)

        titles = [crumb["title"] for crumb in response.context["breadcrumbs"]]
        self.assertEqual(titles[-2:], ["Guidance", "Keeping records"])
        self.assertEqual(len(titles), 3)
        self.assertFalse(response.context.get("breadcrumbs_mobile_only"))

    @override_settings(FEATURE_FLAGS=_feature_flags(skills_enabled=True))
    def test_the_framework_home_page_carries_no_breadcrumb(self):
        home = self.root_page

        response = self.client.get(home.url)

        self.assertEqual(response.context["breadcrumbs"], [])
