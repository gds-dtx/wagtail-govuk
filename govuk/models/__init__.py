"""The ``govuk`` app's models, split into a package by domain.

``models.py`` grew past 5,000 lines across six unrelated domains. It is now a
package of submodules, but this ``__init__`` re-exports every public name so
that ``from govuk.models import X`` -- used in 81 places and in the migration
history -- keeps working unchanged.

The submodules, roughly in dependency order: ``constants`` and ``panels`` and
``signing`` and ``blocks`` are the shared foundation; ``tags``, ``settings``,
``content_discovery``, ``pages`` and ``feedback`` are the platform domains; and
the ``framework`` subpackage holds the Capability Framework. Relations use
string references (``"govuk.GovukRole"``) so nothing here depends on import
order across submodules.
"""

from .blocks import (
    ContentBodyBlock,
    GovukTableBlock,
    InsetTextBlock,
    LinkBlock,
    LinkStructValue,
)
from .constants import (
    DEFAULT_JWT_LIFETIME,
    FRAMEWORK_HOME_LABEL,
    FRAMEWORK_WELCOME_RICH_TEXT_FEATURES,
    HEX_COLOR_VALIDATOR,
    HTTP_METHOD_PATTERN,
    JOB_GRADE_CHOICES,
    JOB_GRADE_LABELS,
    JOB_GRADE_ORDER,
    RELATED_ROLES_COUNT,
    SCS_GRADE_CHOICES,
    SIGNING_ALGORITHM_CHOICES,
    SIGNING_ALGORITHM_EDDSA,
    SIGNING_ALGORITHM_ES256,
    SIGNING_ALGORITHM_VALUES,
    SKILL_LEVEL_CHOICES,
    SKILL_LEVEL_LABEL_TO_VALUE,
    SKILL_LEVEL_LABELS,
    SKILL_LEVEL_ORDINALS,
    SKILL_LEVEL_VALUES,
    SKILLS_AND_ROLES_BODY_RICH_TEXT_FEATURES,
    THIS_SITE_SOURCE_FILTER,
    SigningPrivateKey,
    SigningPublicKey,
)
from .content_discovery import (
    ContentDiscoverySettings,
    ContentDiscoverySource,
    ExternalContentItem,
)
from .feedback import Feedback, PageUsefulnessVote
from .framework import (
    CapabilityFrameworkWordingSettings,
    FrameworkContentPage,
    FrameworkFieldsMixin,
    FrameworkMainPage,
    FrameworkSkillsPage,
    FrameworkWelcomeSectionBlock,
    FrameworkWelcomeSectionValue,
    GovukChangelogEntry,
    GovukRole,
    GovukSkill,
    RoleRenderingMixin,
    SectionBreakBlock,
    SidebarNavigationItem,
    SidebarSettings,
    SkillLevelBlock,
    SkillLevelDefinitionsBlock,
    framework_breadcrumbs,
    framework_home_link,
    framework_main_page,
    further_resources_group,
    role_navigation_groups,
    role_page_urls_by_role_id,
    role_route_url,
    site_wide_changelog,
    without_framework_pages,
)
from .pages import BaseContentPage, ContentPage, SectionPage, TagListingsPage
from .panels import (
    base_content_panels,
    base_settings_panels,
    content_settings_panels,
    framework_content_panels,
    framework_content_settings_panels,
    framework_main_settings_panels,
    page_settings_panels,
)
from .settings import (
    AuthenticatedRedirectRule,
    AuthenticatedRedirectSettings,
    CustomiseSettings,
    EdDSAKeyPair,
    EdDSAKeySettings,
    ErrorPagesSettings,
    FooterSettings,
    PhaseBannerSettings,
)
from .signing import JWTGenerationError, SecretTextarea
from .tags import (
    ContentPageTag,
    ExternalContentItemTag,
    FrameworkContentPageTag,
    FrameworkMainPageTag,
    GovukTag,
    SectionPageTag,
    TagListingsPageTag,
)

# These classes are referenced by their ``govuk.models.X`` import path in
# existing migrations (StreamField block definitions, and RoleRenderingMixin in
# a model's bases). They now live in submodules, so pin their module back so
# that a freshly deconstructed field/base still writes ``govuk.models.X`` and
# ``makemigrations`` sees no change. They are blocks and a plain mixin, not
# models, so this does not affect app-label detection.
for _cls in (GovukTableBlock, InsetTextBlock, SectionBreakBlock, RoleRenderingMixin):
    _cls.__module__ = "govuk.models"
del _cls

__all__ = [
    "AuthenticatedRedirectRule",
    "AuthenticatedRedirectSettings",
    "BaseContentPage",
    "CapabilityFrameworkWordingSettings",
    "ContentBodyBlock",
    "ContentDiscoverySettings",
    "ContentDiscoverySource",
    "ContentPage",
    "ContentPageTag",
    "CustomiseSettings",
    "DEFAULT_JWT_LIFETIME",
    "EdDSAKeyPair",
    "EdDSAKeySettings",
    "ErrorPagesSettings",
    "ExternalContentItem",
    "ExternalContentItemTag",
    "FRAMEWORK_HOME_LABEL",
    "FRAMEWORK_WELCOME_RICH_TEXT_FEATURES",
    "Feedback",
    "FooterSettings",
    "FrameworkContentPage",
    "FrameworkContentPageTag",
    "FrameworkFieldsMixin",
    "FrameworkMainPage",
    "FrameworkMainPageTag",
    "FrameworkSkillsPage",
    "FrameworkWelcomeSectionBlock",
    "FrameworkWelcomeSectionValue",
    "GovukChangelogEntry",
    "GovukRole",
    "GovukSkill",
    "GovukTableBlock",
    "GovukTag",
    "HEX_COLOR_VALIDATOR",
    "HTTP_METHOD_PATTERN",
    "InsetTextBlock",
    "JOB_GRADE_CHOICES",
    "JOB_GRADE_LABELS",
    "JOB_GRADE_ORDER",
    "JWTGenerationError",
    "LinkBlock",
    "LinkStructValue",
    "PageUsefulnessVote",
    "PhaseBannerSettings",
    "RELATED_ROLES_COUNT",
    "RoleRenderingMixin",
    "SCS_GRADE_CHOICES",
    "SIGNING_ALGORITHM_CHOICES",
    "SIGNING_ALGORITHM_EDDSA",
    "SIGNING_ALGORITHM_ES256",
    "SIGNING_ALGORITHM_VALUES",
    "SKILLS_AND_ROLES_BODY_RICH_TEXT_FEATURES",
    "SKILL_LEVEL_CHOICES",
    "SKILL_LEVEL_LABELS",
    "SKILL_LEVEL_LABEL_TO_VALUE",
    "SKILL_LEVEL_ORDINALS",
    "SKILL_LEVEL_VALUES",
    "SectionBreakBlock",
    "SectionPage",
    "SectionPageTag",
    "SecretTextarea",
    "SidebarNavigationItem",
    "SidebarSettings",
    "SigningPrivateKey",
    "SigningPublicKey",
    "SkillLevelBlock",
    "SkillLevelDefinitionsBlock",
    "THIS_SITE_SOURCE_FILTER",
    "TagListingsPage",
    "TagListingsPageTag",
    "base_content_panels",
    "base_settings_panels",
    "content_settings_panels",
    "framework_breadcrumbs",
    "framework_content_panels",
    "framework_content_settings_panels",
    "framework_home_link",
    "framework_main_page",
    "framework_main_settings_panels",
    "further_resources_group",
    "page_settings_panels",
    "role_navigation_groups",
    "role_page_urls_by_role_id",
    "role_route_url",
    "site_wide_changelog",
    "without_framework_pages",
]
