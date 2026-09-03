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


def _sync_default_site_after_migrate(app_config, **kwargs):
    if app_config.label != "wagtailcore":
        return

    from govuk.settings.base import sync_default_site_from_env

    sync_default_site_from_env()


def _warn_about_framework_across_sites(app_config, **kwargs):
    """One database per site while the Capability Framework is switched on.

    Roles, skills, changelog entries and tags are snippets with no site of
    their own, so where two Wagtail Sites share a database they share the
    framework's content: the side navigation on one site lists the other's
    roles and links to them by the other site's hostname. The deployment is a
    database per instance, so this does not arise today, and the fix is a
    schema change rather than a flag.

    Say so out loud instead of leaving it to be discovered. A warning, not an
    error -- it belongs in the deployment log, not in the way of a deployment.
    """
    if app_config.label != "wagtailcore":
        return
    if not settings.FEATURE_FLAGS.get("SKILLS"):
        return

    from wagtail.models import Site

    site_count = Site.objects.count()
    if site_count > 1:
        logger.warning(
            "FEATURE_SKILLS is on and this database holds %s Wagtail sites. "
            "Capability Framework snippets are not site-scoped, so every site "
            "here will show the same roles, skills and changelog. Run one "
            "database per site, or scope the snippets first.",
            site_count,
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
        post_migrate.connect(
            _sync_default_site_after_migrate,
            dispatch_uid="govuk.sync_default_site_after_migrate",
        )
        post_migrate.connect(
            _warn_about_framework_across_sites,
            dispatch_uid="govuk.warn_about_framework_across_sites",
        )
