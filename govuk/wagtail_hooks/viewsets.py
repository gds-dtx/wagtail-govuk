
from django import forms
from wagtail.contrib.settings.registry import SettingMenuItem
from wagtail.snippets.views.snippets import (
    IndexView as SnippetIndexView,
    SnippetViewSet,
    SnippetViewSetGroup,
)

from govuk.models import (
    CapabilityFrameworkWordingSettings,
    ExternalContentItem,
    Feedback,
    GovukChangelogEntry,
    GovukRole,
    GovukSkill,
    GovukTag,
    SidebarSettings,
)


class GovukTagForm(forms.ModelForm):
    class Meta:
        model = GovukTag
        fields = ["slug", "name"]
        labels = {
            "slug": "Key",
            "name": "Value",
        }
        help_texts = {
            "slug": "Lowercase tag key, for example housing-benefit.",
            "name": "Human-readable label, for example Housing benefit.",
        }

    def clean_slug(self) -> str:
        slug = self.cleaned_data["slug"]
        return slug.strip().lower()


class GovukTagViewSet(SnippetViewSet):
    model = GovukTag
    form_class = GovukTagForm
    icon = "tag"
    add_to_admin_menu = True
    menu_label = "Tags"
    menu_name = "govuk-tags"
    menu_order = 200
    list_display = ["key", "value"]
    search_fields = ["slug", "name"]


class ExternalContentItemViewSet(SnippetViewSet):
    model = ExternalContentItem
    icon = "link"
    add_to_admin_menu = True
    menu_label = "External content"
    menu_name = "external-content"
    menu_order = 220
    list_display = [
        "title",
        "url",
        "source",
        "hidden",
        "updated_at",
        "last_seen_at",
    ]
    list_filter = ["hidden", "source"]
    search_fields = ["title", "url"]


class GovukSkillViewSet(SnippetViewSet):
    model = GovukSkill
    icon = "pick"
    # Placed on the admin menu by CapabilityFrameworkViewSetGroup, not on its
    # own, so it appears under the "Capability framework" menu rather than at
    # the top level.
    add_to_admin_menu = False
    menu_label = "Skills"
    menu_name = "govuk-skills"
    list_display = ["title", "slug", "live"]
    search_fields = ["title", "slug", "body"]


class GovukRoleViewSet(SnippetViewSet):
    model = GovukRole
    icon = "user"
    add_to_admin_menu = False
    menu_label = "Roles"
    menu_name = "govuk-roles"
    list_display = ["title", "family", "slug", "live"]
    list_filter = ["family"]
    search_fields = ["title", "slug", "body", "family"]


class GovukChangelogEntryViewSet(SnippetViewSet):
    model = GovukChangelogEntry
    icon = "history"
    add_to_admin_menu = False
    menu_label = "Changelog"
    menu_name = "govuk-changelog"
    list_display = ["date", "role", "skill", "change_type", "live"]
    list_filter = ["live", "date"]
    search_fields = ["note", "change_type"]
    ordering = ["-date"]


class CapabilityFrameworkViewSetGroup(SnippetViewSetGroup):
    """Groups the framework's snippets under one "Capability framework" menu.

    Skills, Roles and Changelog become submenu items of it rather than three
    top-level items, and the "Capability framework wording" site setting is
    added alongside them (and removed from the Settings menu -- see
    ``hide_capability_framework_wording_from_settings_menu``) so everything the
    framework offers an editor sits in one place.
    """

    menu_label = "Capability framework"
    menu_name = "capability-framework"
    menu_icon = "list-ol"
    menu_order = 215
    items = (GovukSkillViewSet, GovukRoleViewSet, GovukChangelogEntryViewSet)

    def get_submenu_items(self):
        menu_items = super().get_submenu_items()
        # These are site settings, not snippets, so they are not among the
        # grouped viewsets; add their settings menu items by hand, after them.
        menu_items.append(
            SettingMenuItem(
                SidebarSettings,
                icon="list-ul",
                order=len(menu_items) + 1,
            )
        )
        menu_items.append(
            SettingMenuItem(
                CapabilityFrameworkWordingSettings,
                icon="edit",
                order=len(menu_items) + 1,
            )
        )
        return menu_items


class FeedbackIndexView(SnippetIndexView):
    def _get_title_column(self, *args, **kwargs):
        column = super()._get_title_column(*args, **kwargs)
        column._get_url_func = lambda instance: self.get_inspect_url(
            instance
        ) or self.get_edit_url(instance)
        return column


class FeedbackViewSet(SnippetViewSet):
    model = Feedback
    index_view_class = FeedbackIndexView
    icon = "doc-full"
    add_to_admin_menu = True
    menu_label = "Feedback"
    menu_name = "feedback"
    menu_order = 220
    ordering = ["-created_at", "-id"]
    list_display = [
        "name",
        "feedback_type_label",
        "comments_preview",
        "created_at",
    ]
    inspect_view_enabled = True
    inspect_view_fields = [
        "name",
        "email",
        "feedback_type",
        "comments",
        "referrer",
        "browser",
        "is_mobile",
        "created_at",
        "user",
    ]
    search_fields = ["name", "email", "comments", "referrer", "browser"]

