"""The ``govuk`` app's Wagtail hooks, split into a package by concern.

Wagtail auto-discovers ``govuk.wagtail_hooks`` and imports it once at startup.
Registration happens as a side effect of that import, so this ``__init__`` must
pull in every submodule for its hooks to fire:

* ``rich_text`` -- the GOV.UK button, raw-HTML and inset-text rich-text
  features, plus the ``register_rich_text_features`` hook.
* ``viewsets`` -- the snippet viewsets (tags, external content, the Capability
  Framework group, feedback). The classes are defined here; the imperative
  registration below decides which are switched on.
* ``admin_views`` -- the pages import/export, content-discovery and EdDSA-key
  admin views, and the ``register_admin_menu_item`` / ``register_admin_urls`` /
  ``register_reports_menu_item`` hooks.

Names other modules and tests read off ``govuk.wagtail_hooks`` are re-exported
here so the split is invisible to callers.
"""

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from wagtail import hooks
from wagtail.contrib.settings.models import register_setting
from wagtail.snippets.models import register_snippet

from govuk.models import CapabilityFrameworkWordingSettings, SidebarSettings

from . import admin_views  # noqa: F401  (imported for its hook registrations)
from .rich_text import (
    GOVUK_BUTTON_ENTITY_TYPE,
    GOVUK_BUTTON_FEATURE,
    GOVUK_BUTTON_LINKTYPE,
    GOVUK_START_BUTTON_ENTITY_TYPE,
    GOVUK_START_BUTTON_FEATURE,
    GOVUK_START_BUTTON_LINKTYPE,
    INSET_TEXT_BLOCK_TYPE,
    INSET_TEXT_FEATURE,
    LINE_BREAK_FEATURE,
    RAW_HTML_EMBEDTYPE,
    RAW_HTML_ENTITY_TYPE,
    RAW_HTML_FEATURE,
    RawHtmlEmbedHandler,
    _encode_raw_html,
    _wrap_table_in_scroll_region,
    register_govuk_button_rich_text_features,
)
from .viewsets import (
    CapabilityFrameworkViewSetGroup,
    ExternalContentItemViewSet,
    FeedbackIndexView,
    FeedbackViewSet,
    GovukChangelogEntryViewSet,
    GovukRoleViewSet,
    GovukSkillViewSet,
    GovukTagForm,
    GovukTagViewSet,
)

__all__ = [
    "CapabilityFrameworkViewSetGroup",
    "CapabilityFrameworkWordingSettings",
    "ExternalContentItemViewSet",
    "FeedbackIndexView",
    "FeedbackViewSet",
    "GOVUK_BUTTON_ENTITY_TYPE",
    "GOVUK_BUTTON_FEATURE",
    "GOVUK_BUTTON_LINKTYPE",
    "GOVUK_START_BUTTON_ENTITY_TYPE",
    "GOVUK_START_BUTTON_FEATURE",
    "GOVUK_START_BUTTON_LINKTYPE",
    "GovukChangelogEntryViewSet",
    "GovukRoleViewSet",
    "GovukSkillViewSet",
    "GovukTagForm",
    "GovukTagViewSet",
    "INSET_TEXT_BLOCK_TYPE",
    "INSET_TEXT_FEATURE",
    "LINE_BREAK_FEATURE",
    "RAW_HTML_EMBEDTYPE",
    "RAW_HTML_ENTITY_TYPE",
    "RAW_HTML_FEATURE",
    "RawHtmlEmbedHandler",
    "SidebarSettings",
    "_encode_raw_html",
    "_wrap_table_in_scroll_region",
    "register_govuk_button_rich_text_features",
]


def _register_snippet_if_needed(viewset):
    try:
        register_snippet(viewset)
    except ImproperlyConfigured as exc:
        if "already registered as a snippet" not in str(exc):
            raise


_register_snippet_if_needed(GovukTagViewSet)
_register_snippet_if_needed(ExternalContentItemViewSet)
if settings.FEATURE_FLAGS.get("SKILLS"):
    # One "Capability framework" menu with Skills, Roles and Changelog under it,
    # rather than three top-level items. Registering the group registers all
    # three viewsets (their models, views and URLs) too.
    _register_snippet_if_needed(CapabilityFrameworkViewSetGroup)
    # Still registered as a site setting so its edit view, permissions and URL
    # exist; the group adds it to its own menu and the hook below takes it out
    # of the Settings menu so it lives in one place.
    register_setting(CapabilityFrameworkWordingSettings, icon="edit")
    register_setting(SidebarSettings, icon="list-ul")

    _framework_settings_models = (CapabilityFrameworkWordingSettings, SidebarSettings)

    @hooks.register("construct_settings_menu")
    def hide_framework_settings_from_settings_menu(request, menu_items):
        menu_items[:] = [
            item
            for item in menu_items
            if getattr(item, "model", None) not in _framework_settings_models
        ]

if settings.FEATURE_FLAGS.get("FEEDBACK"):
    _register_snippet_if_needed(FeedbackViewSet)
