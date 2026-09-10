import importlib
from unittest.mock import call, patch

from django.test import SimpleTestCase, override_settings


def _feature_flags(*, skills_enabled: bool) -> dict[str, bool]:
    return {
        "SKILLS": skills_enabled,
        "ORGANISATIONS": False,
        "PEOPLE_FINDER": False,
        "FEEDBACK": False,
    }


def _reload_hooks():
    import govuk.wagtail_hooks as hooks_module

    return importlib.reload(hooks_module)


class SkillsWagtailHooksTests(SimpleTestCase):
    @override_settings(FEATURE_FLAGS=_feature_flags(skills_enabled=True))
    @patch("wagtail.snippets.models.register_snippet")
    def test_registers_skills_and_roles_snippets_when_enabled(
        self, mock_register_snippet
    ):
        hooks_module = _reload_hooks()

        # Skills, Roles and Changelog are registered through the group, not
        # individually, so the group carries them onto one shared menu.
        mock_register_snippet.assert_has_calls(
            [
                call(hooks_module.GovukTagViewSet),
                call(hooks_module.ExternalContentItemViewSet),
                call(hooks_module.CapabilityFrameworkViewSetGroup),
            ]
        )
        self.assertEqual(mock_register_snippet.call_count, 3)
        self.assertEqual(
            hooks_module.CapabilityFrameworkViewSetGroup.items,
            (
                hooks_module.GovukSkillViewSet,
                hooks_module.GovukRoleViewSet,
                hooks_module.GovukChangelogEntryViewSet,
            ),
        )

        _reload_hooks()

    @override_settings(FEATURE_FLAGS=_feature_flags(skills_enabled=False))
    @patch("wagtail.snippets.models.register_snippet")
    def test_does_not_register_skills_and_roles_snippets_when_disabled(
        self, mock_register_snippet
    ):
        hooks_module = _reload_hooks()

        mock_register_snippet.assert_has_calls(
            [
                call(hooks_module.GovukTagViewSet),
                call(hooks_module.ExternalContentItemViewSet),
            ]
        )
        self.assertEqual(mock_register_snippet.call_count, 2)
        self.assertNotIn(
            call(hooks_module.GovukSkillViewSet),
            mock_register_snippet.mock_calls,
        )
        self.assertNotIn(
            call(hooks_module.GovukRoleViewSet),
            mock_register_snippet.mock_calls,
        )
        self.assertNotIn(
            call(hooks_module.GovukChangelogEntryViewSet),
            mock_register_snippet.mock_calls,
        )

        _reload_hooks()

    @override_settings(FEATURE_FLAGS=_feature_flags(skills_enabled=True))
    @patch("wagtail.contrib.settings.models.register_setting")
    def test_registers_the_framework_settings_when_enabled(
        self, mock_register_setting
    ):
        hooks_module = _reload_hooks()

        mock_register_setting.assert_any_call(
            hooks_module.CapabilityFrameworkWordingSettings, icon="edit"
        )
        mock_register_setting.assert_any_call(
            hooks_module.SidebarSettings, icon="list-ul"
        )

        _reload_hooks()

    @override_settings(FEATURE_FLAGS=_feature_flags(skills_enabled=False))
    @patch("wagtail.contrib.settings.models.register_setting")
    def test_does_not_register_the_framework_wording_setting_when_disabled(
        self, mock_register_setting
    ):
        hooks_module = _reload_hooks()

        self.assertNotIn(
            call(hooks_module.CapabilityFrameworkWordingSettings, icon="edit"),
            mock_register_setting.mock_calls,
        )
        self.assertEqual(mock_register_setting.call_count, 0)

        _reload_hooks()

    def test_skills_and_roles_viewsets_have_expected_admin_configuration(self):
        hooks_module = _reload_hooks()

        # Not on the top-level menu on their own: the group places them under
        # "Capability framework".
        self.assertFalse(hooks_module.GovukSkillViewSet.add_to_admin_menu)
        self.assertEqual(hooks_module.GovukSkillViewSet.menu_label, "Skills")
        self.assertEqual(hooks_module.GovukSkillViewSet.list_display, ["title", "slug"])

        self.assertFalse(hooks_module.GovukRoleViewSet.add_to_admin_menu)
        self.assertEqual(hooks_module.GovukRoleViewSet.menu_label, "Roles")
        self.assertEqual(
            hooks_module.GovukRoleViewSet.list_display, ["title", "family", "slug"]
        )

        self.assertFalse(hooks_module.GovukChangelogEntryViewSet.add_to_admin_menu)

    def test_the_capability_framework_menu_groups_the_snippets_and_wording(self):
        hooks_module = _reload_hooks()

        group = hooks_module.CapabilityFrameworkViewSetGroup
        self.assertEqual(group.menu_label, "Capability framework")
        self.assertEqual(
            group.items,
            (
                hooks_module.GovukSkillViewSet,
                hooks_module.GovukRoleViewSet,
                hooks_module.GovukChangelogEntryViewSet,
            ),
        )
        # The two site settings are added to the group's submenu alongside the
        # three snippets.
        labels = [item.label for item in group().get_submenu_items()]
        self.assertEqual(
            labels,
            [
                "Skills",
                "Roles",
                "Changelog",
                "Sidebar settings",
                "Capability framework wording",
            ],
        )
