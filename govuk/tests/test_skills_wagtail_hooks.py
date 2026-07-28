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

        mock_register_snippet.assert_has_calls(
            [
                call(hooks_module.GovukTagViewSet),
                call(hooks_module.ExternalContentItemViewSet),
                call(hooks_module.GovukSkillViewSet),
                call(hooks_module.GovukRoleViewSet),
                call(hooks_module.GovukChangelogEntryViewSet),
            ]
        )
        self.assertEqual(mock_register_snippet.call_count, 5)

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

    def test_skills_and_roles_viewsets_have_expected_admin_configuration(self):
        hooks_module = _reload_hooks()

        self.assertTrue(hooks_module.GovukSkillViewSet.add_to_admin_menu)
        self.assertEqual(hooks_module.GovukSkillViewSet.menu_label, "Skills")
        self.assertEqual(hooks_module.GovukSkillViewSet.list_display, ["title", "slug"])

        self.assertTrue(hooks_module.GovukRoleViewSet.add_to_admin_menu)
        self.assertEqual(hooks_module.GovukRoleViewSet.menu_label, "Roles")
        self.assertEqual(hooks_module.GovukRoleViewSet.list_display, ["title", "slug"])
