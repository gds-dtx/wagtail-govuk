"""Migration 0094 moves the search settings from Customise to Settings > Search."""

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from wagtail.models import Site

from govuk.models import CustomiseSettings, SearchSettings

BEFORE = [("govuk", "0093_banner_html_and_custom_colour")]
AFTER = [("govuk", "0094_search_settings")]


def _leaf():
    return MigrationExecutor(connection).loader.graph.leaf_nodes("govuk")


class SearchSettingsMigrationTests(TransactionTestCase):
    serialized_rollback = True

    def setUp(self):
        executor = MigrationExecutor(connection)
        executor.migrate(BEFORE)
        apps = executor.loader.project_state(BEFORE).apps
        try:
            OldCustomise = apps.get_model("govuk", "CustomiseSettings")
            site_id = Site.objects.get(is_default_site=True).pk
            OldCustomise.objects.update_or_create(
                site_id=site_id,
                defaults={
                    "search_location": "navigation",
                    "search_placeholder": "Search for roles or skills",
                    "enable_search_results_page": False,
                    "show_site_name_in_search_box": True,
                    "header_logo": "uk-government",
                },
            )
            executor = MigrationExecutor(connection)
            executor.migrate(AFTER)
        finally:
            MigrationExecutor(connection).migrate(_leaf())
        self.site = Site.objects.get(is_default_site=True)

    def tearDown(self):
        MigrationExecutor(connection).migrate(_leaf())

    def test_each_sites_search_box_settings_move_to_search(self):
        search = SearchSettings.objects.get(site=self.site)
        self.assertEqual(search.search_location, "navigation")
        self.assertEqual(search.search_placeholder, "Search for roles or skills")
        self.assertFalse(search.enable_search_results_page)

    def test_customise_keeps_its_other_settings(self):
        customise = CustomiseSettings.objects.get(site=self.site)
        self.assertEqual(customise.header_logo, "uk-government")
        for gone in (
            "search_location",
            "search_placeholder",
            "enable_search_results_page",
            "show_site_name_in_search_box",
        ):
            self.assertFalse(hasattr(customise, gone), gone)
