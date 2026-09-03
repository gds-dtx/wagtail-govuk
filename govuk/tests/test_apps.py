from importlib import import_module
from types import SimpleNamespace
from unittest.mock import call, patch

from django.test import SimpleTestCase, TestCase, override_settings
from wagtail.models import Page, Site

from govuk.apps import (
    GovukConfig,
    _sync_admin_users_after_migrate,
    _sync_default_site_after_migrate,
    _warn_about_framework_across_sites,
)


def _feature_flags(*, skills_enabled: bool) -> dict[str, bool]:
    return {
        "SKILLS": skills_enabled,
        "ORGANISATIONS": False,
        "PEOPLE_FINDER": False,
        "FEEDBACK": False,
    }


class GovukConfigTests(SimpleTestCase):
    @override_settings(
        DEBUG=True,
        INCOMING_REQUEST_INFO_LOGGING=False,
        CONTENT_DISCOVERY_REQUEST_INFO_LOGGING=True,
    )
    def test_ready_logs_startup_logging_flags(self):
        app_config = GovukConfig("govuk", import_module("govuk"))

        with (
            patch("govuk.apps.logger.info") as mock_info,
            patch("govuk.apps.post_migrate.connect") as mock_connect,
        ):
            app_config.ready()

        mock_info.assert_called_once_with(
            "Application startup logging flags",
            extra={
                "DEBUG": True,
                "INCOMING_REQUEST_INFO_LOGGING": False,
                "CONTENT_DISCOVERY_REQUEST_INFO_LOGGING": True,
            },
        )
        mock_connect.assert_has_calls(
            [
                call(
                    _sync_admin_users_after_migrate,
                    dispatch_uid="govuk.sync_admin_users_after_migrate",
                ),
                call(
                    _sync_default_site_after_migrate,
                    dispatch_uid="govuk.sync_default_site_after_migrate",
                ),
                call(
                    _warn_about_framework_across_sites,
                    dispatch_uid="govuk.warn_about_framework_across_sites",
                ),
            ]
        )

    def test_sync_admin_users_after_migrate_runs_for_auth(self):
        app_config = SimpleNamespace(label="auth")

        with (
            patch(
                "govuk.settings.base.sync_admin_users_from_env",
                return_value={"created": 1, "updated": 2},
            ) as mock_sync,
            patch("govuk.apps.logger.info") as mock_info,
        ):
            _sync_admin_users_after_migrate(app_config)

        mock_sync.assert_called_once_with()
        mock_info.assert_called_once_with(
            "ADMIN_USER_EMAILS sync complete: created=%s updated=%s",
            1,
            2,
        )

    def test_sync_admin_users_after_migrate_skips_other_apps(self):
        app_config = SimpleNamespace(label="wagtailcore")

        with (
            patch("govuk.settings.base.sync_admin_users_from_env") as mock_sync,
            patch("govuk.apps.logger.info") as mock_info,
        ):
            _sync_admin_users_after_migrate(app_config)

        mock_sync.assert_not_called()
        mock_info.assert_not_called()

    def test_sync_default_site_after_migrate_runs_for_wagtailcore(self):
        app_config = SimpleNamespace(label="wagtailcore")

        with patch("govuk.settings.base.sync_default_site_from_env") as mock_sync:
            _sync_default_site_after_migrate(app_config)

        mock_sync.assert_called_once_with()

    def test_sync_default_site_after_migrate_skips_other_apps(self):
        app_config = SimpleNamespace(label="auth")

        with patch("govuk.settings.base.sync_default_site_from_env") as mock_sync:
            _sync_default_site_after_migrate(app_config)

        mock_sync.assert_not_called()


class FrameworkAcrossSitesWarningTests(TestCase):
    """Capability Framework snippets carry no site, so two sites in one
    database share them. The deployment gives each instance its own database,
    which is why this has never bitten; say so at migrate time rather than
    leaving it to be found by an editor seeing another service's roles."""

    def setUp(self):
        self.app_config = SimpleNamespace(label="wagtailcore")

    def _add_second_site(self) -> Site:
        root = Page.get_first_root_node()
        second_root = root.add_child(
            instance=Page(title="Second service", slug="second-service")
        )
        return Site.objects.create(
            hostname="second.example.gov.uk",
            root_page=second_root,
            is_default_site=False,
        )

    @override_settings(FEATURE_FLAGS=_feature_flags(skills_enabled=True))
    def test_two_sites_with_the_framework_on_are_warned_about(self):
        self._add_second_site()

        with self.assertLogs("govuk.apps", level="WARNING") as logs:
            _warn_about_framework_across_sites(self.app_config)

        self.assertIn("not site-scoped", logs.output[0])
        self.assertIn("2 Wagtail sites", logs.output[0])

    @override_settings(FEATURE_FLAGS=_feature_flags(skills_enabled=True))
    def test_a_single_site_says_nothing(self):
        with patch("govuk.apps.logger.warning") as mock_warning:
            _warn_about_framework_across_sites(self.app_config)

        mock_warning.assert_not_called()

    @override_settings(FEATURE_FLAGS=_feature_flags(skills_enabled=False))
    def test_two_sites_without_the_framework_say_nothing(self):
        self._add_second_site()

        with patch("govuk.apps.logger.warning") as mock_warning:
            _warn_about_framework_across_sites(self.app_config)

        mock_warning.assert_not_called()

    @override_settings(FEATURE_FLAGS=_feature_flags(skills_enabled=True))
    def test_it_runs_once_rather_than_for_every_app(self):
        self._add_second_site()

        with patch("govuk.apps.logger.warning") as mock_warning:
            _warn_about_framework_across_sites(SimpleNamespace(label="govuk"))

        mock_warning.assert_not_called()
