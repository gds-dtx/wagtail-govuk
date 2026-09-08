"""Migration 0069's data step: the live service's side-menu pages are ticked.

Before 0069 every page beside the roles was in the menu's last group, so on a
framework instance the five pages live lists were already there. The data
step ticks exactly those, so the menu matches live through the deploy, and
leaves everything else -- Privacy, the Accessibility statement, the project
pages -- unticked, which is the point of the change.
"""

import importlib

from django.apps import apps
from django.test import TestCase, override_settings
from wagtail.models import Site

from govuk.models import ContentPage

migration = importlib.import_module(
    "govuk.migrations.0069_contentpage_show_in_role_navigation"
)


def _feature_flags(*, skills_enabled: bool) -> dict[str, bool]:
    return {
        "SKILLS": skills_enabled,
        "ORGANISATIONS": False,
        "PEOPLE_FINDER": False,
        "FEEDBACK": False,
    }


class SideMenuMigrationTests(TestCase):
    def setUp(self):
        self.root_page = Site.objects.get(is_default_site=True).root_page
        for slug in (
            "skills-placeholder",
            "propose-a-change",
            "download",
            "job-grades",
            "context-and-challenges-for-senior-civil-service-roles-in-digital-and-data",
            "roadmap",
            "privacy",
            "accessibility-statement",
            "project-agile-coach-new-role",
        ):
            page = self.root_page.add_child(instance=ContentPage(title=slug, slug=slug))
            page.save_revision().publish()
        # A grandchild with a listed slug: not a direct child, so not ticked.
        parent = ContentPage.objects.get(slug="roadmap")
        child = parent.add_child(instance=ContentPage(title="nested", slug="download"))
        child.save_revision().publish()

    @override_settings(FEATURE_FLAGS=_feature_flags(skills_enabled=True))
    def test_the_five_pages_live_lists_are_ticked_and_nothing_else(self):
        migration.tick_the_live_pages(apps, None)

        ticked = sorted(
            ContentPage.objects.filter(show_in_role_navigation=True).values_list(
                "slug", flat=True
            )
        )
        self.assertEqual(
            ticked,
            sorted(
                [
                    "propose-a-change",
                    "download",
                    "job-grades",
                    "context-and-challenges-for-senior-civil-service-roles-in-digital-and-data",
                    "roadmap",
                ]
            ),
        )
        # The nested page shares a listed slug but is not beside the roles.
        self.assertEqual(
            ContentPage.objects.filter(slug="download", show_in_role_navigation=True).count(),
            1,
        )

    @override_settings(FEATURE_FLAGS=_feature_flags(skills_enabled=False))
    def test_a_site_without_the_framework_is_left_alone(self):
        migration.tick_the_live_pages(apps, None)

        self.assertFalse(ContentPage.objects.filter(show_in_role_navigation=True).exists())

    @override_settings(FEATURE_FLAGS=_feature_flags(skills_enabled=True))
    def test_the_step_reverses(self):
        migration.tick_the_live_pages(apps, None)
        migration.untick(apps, None)

        self.assertFalse(ContentPage.objects.filter(show_in_role_navigation=True).exists())
