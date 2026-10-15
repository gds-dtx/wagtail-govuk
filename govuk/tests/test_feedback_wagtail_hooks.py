import importlib
from unittest.mock import call, patch

from django.test import SimpleTestCase, override_settings

from govuk.models import Feedback


def _feature_flags(*, feedback_enabled: bool) -> dict[str, bool]:
    return {
        "FEEDBACK": feedback_enabled,
    }


def _reload_feedback_hooks():
    import govuk.wagtail_hooks as feedback_hooks

    return importlib.reload(feedback_hooks)


class FeedbackWagtailHooksTests(SimpleTestCase):
    @override_settings(FEATURE_FLAGS=_feature_flags(feedback_enabled=True))
    @patch("wagtail.snippets.models.register_snippet")
    def test_registers_feedback_snippet_when_enabled(self, mock_register_snippet):
        hooks_module = _reload_feedback_hooks()

        mock_register_snippet.assert_has_calls(
            [
                call(hooks_module.GovukTagViewSet),
                call(hooks_module.ExternalContentItemViewSet),
                call(hooks_module.FeedbackViewSet),
            ]
        )
        self.assertEqual(mock_register_snippet.call_count, 3)

        _reload_feedback_hooks()

    @override_settings(FEATURE_FLAGS=_feature_flags(feedback_enabled=False))
    @patch("wagtail.snippets.models.register_snippet")
    def test_does_not_register_feedback_snippet_when_disabled(
        self, mock_register_snippet
    ):
        hooks_module = _reload_feedback_hooks()

        mock_register_snippet.assert_has_calls(
            [
                call(hooks_module.GovukTagViewSet),
                call(hooks_module.ExternalContentItemViewSet),
            ]
        )
        self.assertEqual(mock_register_snippet.call_count, 2)
        self.assertNotIn(
            call(hooks_module.FeedbackViewSet),
            mock_register_snippet.mock_calls,
        )

        _reload_feedback_hooks()

    def test_feedback_viewset_configuration_matches_expected_admin_listing(self):
        hooks_module = _reload_feedback_hooks()

        self.assertTrue(hooks_module.FeedbackViewSet.add_to_admin_menu)
        self.assertEqual(hooks_module.FeedbackViewSet.menu_label, "Feedback")
        self.assertEqual(
            hooks_module.FeedbackViewSet.index_view_class, hooks_module.FeedbackIndexView
        )
        self.assertEqual(
            hooks_module.FeedbackViewSet.list_display,
            ["name", "feedback_type_label", "comments_preview", "created_at"],
        )
        self.assertEqual(hooks_module.FeedbackViewSet.ordering, ["-created_at", "-id"])
        self.assertTrue(hooks_module.FeedbackViewSet.inspect_view_enabled)

    def test_feedback_index_title_link_prefers_inspect_url(self):
        hooks_module = _reload_feedback_hooks()
        index_view = hooks_module.FeedbackIndexView(model=Feedback, list_display=["name"])
        index_view.get_inspect_url = lambda obj: "/inspect/123/"
        index_view.get_edit_url = lambda obj: "/edit/123/"

        title_column = index_view._get_title_column("name")
        link_url = title_column.get_link_url(Feedback(name="Example", comments="Body"), {})

        self.assertEqual(link_url, "/inspect/123/")

    def test_feedback_index_title_link_falls_back_to_edit_url(self):
        hooks_module = _reload_feedback_hooks()
        index_view = hooks_module.FeedbackIndexView(model=Feedback, list_display=["name"])
        index_view.get_inspect_url = lambda obj: None
        index_view.get_edit_url = lambda obj: "/edit/123/"

        title_column = index_view._get_title_column("name")
        link_url = title_column.get_link_url(Feedback(name="Example", comments="Body"), {})

        self.assertEqual(link_url, "/edit/123/")
