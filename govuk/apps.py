import logging

from django.apps import AppConfig
from django.conf import settings
from django.db.models.signals import post_migrate


logger = logging.getLogger(__name__)


def _sync_admin_users_after_migrate(app_config, **kwargs):
    if app_config.label != "auth":
        return

    from govuk.settings.base import sync_admin_users_from_env

    results = sync_admin_users_from_env()
    if results["created"] or results["updated"]:
        logger.info(
            "ADMIN_USER_EMAILS sync complete: created=%s updated=%s",
            results["created"],
            results["updated"],
        )


class GovukConfig(AppConfig):
    name = "govuk"
    verbose_name = "GOV.UK"

    def ready(self):
        logger.info(
            "Application startup logging flags",
            extra={
                "DEBUG": settings.DEBUG,
                "INCOMING_REQUEST_INFO_LOGGING": getattr(
                    settings, "INCOMING_REQUEST_INFO_LOGGING", False
                ),
                "CONTENT_DISCOVERY_REQUEST_INFO_LOGGING": getattr(
                    settings, "CONTENT_DISCOVERY_REQUEST_INFO_LOGGING", False
                ),
            },
        )
        post_migrate.connect(
            _sync_admin_users_after_migrate,
            dispatch_uid="govuk.sync_admin_users_after_migrate",
        )
