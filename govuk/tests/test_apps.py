from importlib import import_module
from types import SimpleNamespace
from unittest.mock import call, patch

from django.test import SimpleTestCase, override_settings

from govuk.apps import (
    GovukConfig,
    _sync_admin_users_after_migrate,
    _sync_default_site_after_migrate,
)


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
