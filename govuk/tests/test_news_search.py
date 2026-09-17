from django.test import TestCase, override_settings
from wagtail.models import Site

from govuk.models import NewsArticle, NewsIndexPage
from govuk.search_backend import search_backend


def _feature_flags(*, news_enabled: bool) -> dict[str, bool]:
    return {
        "SKILLS": False,
        "ORGANISATIONS": False,
        "PEOPLE_FINDER": False,
        "FEEDBACK": False,
        "NEWS": news_enabled,
    }


def _make_index(parent) -> NewsIndexPage:
    page = parent.add_child(instance=NewsIndexPage(title="News", slug="news"))
    page.save_revision().publish()
    return NewsIndexPage.objects.get(pk=page.pk)


@override_settings(FEATURE_FLAGS=_feature_flags(news_enabled=True))
class NewsSearchTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root_page = self.site.root_page.specific
        self.index = _make_index(self.root_page)
        self.article = NewsArticle.objects.create(
            title="Quantum computing milestone",
            standfirst="<p>A milestone in quantum computing.</p>",
            body="<p>Details about the quantum computing milestone.</p>",
        )

    def test_a_news_article_appears_in_search_with_a_news_badge(self):
        results = search_backend.search("quantum computing milestone", page=1)
        news = [r for r in results.object_list if r.result_type == "News"]

        self.assertTrue(news)
        self.assertEqual(news[0].title, "Quantum computing milestone")
        self.assertIn("/news/article/quantum-computing-milestone/", news[0].url)

    def test_a_draft_article_is_not_searchable(self):
        NewsArticle.objects.create(title="Secret draft quantum story", live=False)

        results = search_backend.search("secret draft quantum story", page=1)

        self.assertFalse([r for r in results.object_list if r.result_type == "News"])

    @override_settings(FEATURE_FLAGS=_feature_flags(news_enabled=False))
    def test_news_is_absent_from_search_when_the_feature_is_off(self):
        results = search_backend.search("quantum computing milestone", page=1)

        self.assertFalse([r for r in results.object_list if r.result_type == "News"])
