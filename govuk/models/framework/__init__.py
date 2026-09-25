"""The Capability Framework's models, blocks, settings and navigation.

Grouped here because the framework is one service among several this image
serves; every name is re-exported from ``govuk.models`` so callers and
migrations keep addressing it as ``govuk.models.X``.
"""

from .blocks import (
    FrameworkWelcomeSectionBlock,
    FrameworkWelcomeSectionValue,
    SectionBreakBlock,
    SkillLevelBlock,
    SkillLevelDefinitionsBlock,
)
from .navigation import (
    framework_breadcrumbs,
    framework_home_link,
    framework_main_page,
    further_resources_group,
    role_navigation_groups,
    role_page_urls_by_role_id,
    role_route_url,
    without_framework_pages,
)
from .pages import (
    FrameworkContentPage,
    FrameworkFieldsMixin,
    FrameworkMainPage,
    FrameworkSkillsPage,
    RoleRenderingMixin,
)
from .settings import (
    CapabilityFrameworkWordingSettings,
    SidebarNavigationItem,
    SidebarSettings,
)
from .snippets import (
    GovukChangelogEntry,
    GovukRole,
    GovukSkill,
    site_wide_changelog,
)

__all__ = [
    "CapabilityFrameworkWordingSettings",
    "FrameworkContentPage",
    "FrameworkFieldsMixin",
    "FrameworkMainPage",
    "FrameworkSkillsPage",
    "FrameworkWelcomeSectionBlock",
    "FrameworkWelcomeSectionValue",
    "GovukChangelogEntry",
    "GovukRole",
    "GovukSkill",
    "RoleRenderingMixin",
    "SectionBreakBlock",
    "SidebarNavigationItem",
    "SidebarSettings",
    "SkillLevelBlock",
    "SkillLevelDefinitionsBlock",
    "framework_breadcrumbs",
    "framework_home_link",
    "framework_main_page",
    "further_resources_group",
    "role_navigation_groups",
    "role_page_urls_by_role_id",
    "role_route_url",
    "site_wide_changelog",
    "without_framework_pages",
]
