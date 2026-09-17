import importlib
from unittest.mock import call, patch

from django.test import SimpleTestCase, override_settings


def _feature_flags(*, news_enabled: bool) -> dict[str, bool]:
    return {
        "SKILLS": False,
        "ORGANISATIONS": False,
        "PEOPLE_FINDER": False,
        "FEEDBACK": False,
        "NEWS": news_enabled,
    }


def _reload_hooks():
    import govuk.wagtail_hooks as hooks_module

    return importlib.reload(hooks_module)


class NewsWagtailHooksTests(SimpleTestCase):
    @override_settings(FEATURE_FLAGS=_feature_flags(news_enabled=True))
    @patch("wagtail.snippets.models.register_snippet")
    def test_registers_news_snippet_when_enabled(self, mock_register_snippet):
        hooks_module = _reload_hooks()

        self.assertIn(
            call(hooks_module.NewsArticleViewSet),
            mock_register_snippet.mock_calls,
        )
        self.assertTrue(hooks_module.NewsArticleViewSet.add_to_admin_menu)
        self.assertEqual(hooks_module.NewsArticleViewSet.menu_label, "News")

        _reload_hooks()

    @override_settings(FEATURE_FLAGS=_feature_flags(news_enabled=False))
    @patch("wagtail.snippets.models.register_snippet")
    def test_does_not_register_news_snippet_when_disabled(self, mock_register_snippet):
        hooks_module = _reload_hooks()

        self.assertNotIn(
            call(hooks_module.NewsArticleViewSet),
            mock_register_snippet.mock_calls,
        )

        _reload_hooks()
