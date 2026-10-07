from django.test import TestCase, override_settings
from wagtail.models import Site

from govuk.models import (
    ContentPage,
    GovukTag,
    NewsArticle,
    NewsIndexPage,
    SectionPage,
)
from govuk.models.pages import HeroStyle


def _feature_flags(*, news_enabled: bool) -> dict[str, bool]:
    return {
        "SKILLS": False,
        "FEEDBACK": False,
        "NEWS": news_enabled,
    }


def _make_index(parent, *, title="News", slug="news", **kwargs) -> NewsIndexPage:
    page = parent.add_child(instance=NewsIndexPage(title=title, slug=slug, **kwargs))
    page.save_revision().publish()
    return NewsIndexPage.objects.get(pk=page.pk)


def _article_url(index_page, article) -> str:
    return index_page.url + index_page.reverse_subpage(
        "serve_article", args=[article.slug]
    )


@override_settings(FEATURE_FLAGS=_feature_flags(news_enabled=True))
class NewsIndexPageTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root_page = self.site.root_page.specific
        self.index = _make_index(self.root_page)

        self.published = NewsArticle.objects.create(
            title="Published article",
            standfirst="<p>The lead sentence.</p>",
            body="<p>Body copy for the published article.</p>",
        )
        self.draft = NewsArticle.objects.create(title="Draft article", live=False)

    def test_the_route_serves_a_live_article(self):
        response = self.client.get(_article_url(self.index, self.published))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Published article")
        self.assertContains(response, "The lead sentence.")

    def test_the_route_404s_for_a_draft_article(self):
        response = self.client.get(_article_url(self.index, self.draft))
        self.assertEqual(response.status_code, 404)

    def test_the_route_404s_for_an_unknown_slug(self):
        response = self.client.get(
            self.index.url
            + self.index.reverse_subpage("serve_article", args=["nope"])
        )
        self.assertEqual(response.status_code, 404)

    def test_the_index_lists_only_live_articles_newest_first(self):
        older = NewsArticle.objects.create(
            title="Older", publication_date="2020-01-01"
        )
        newer = NewsArticle.objects.create(
            title="Newer", publication_date="2030-01-01"
        )

        listed = list(self.index.listed_articles())

        self.assertIn(newer, listed)
        self.assertIn(older, listed)
        self.assertNotIn(self.draft, listed)
        self.assertLess(listed.index(newer), listed.index(older))

    def test_a_configured_tag_filter_narrows_the_listing(self):
        cyber = GovukTag.objects.create(slug="cyber", name="Cyber")
        health = GovukTag.objects.create(slug="health", name="Health")
        cyber_article = NewsArticle.objects.create(title="Cyber news")
        cyber_article.tags.add(cyber)
        cyber_article.save()
        health_article = NewsArticle.objects.create(title="Health news")
        health_article.tags.add(health)
        health_article.save()

        self.index.tags.add(cyber)
        self.index.save()

        listed = list(self.index.listed_articles())
        self.assertIn(cyber_article, listed)
        self.assertNotIn(health_article, listed)
        # An article outside the filter 404s at this page's route.
        self.assertEqual(
            self.client.get(_article_url(self.index, health_article)).status_code,
            404,
        )

    def test_it_is_creatable_under_the_general_container_pages(self):
        """parent_page_types and each parent's subpage_types must agree, or
        Wagtail hides "News index page" from the Add menu."""
        section = self.root_page.add_child(
            instance=SectionPage(title="A section", slug="a-section")
        )
        content = self.root_page.add_child(
            instance=ContentPage(title="A page", slug="a-page", body="<p>Hi.</p>")
        )

        self.assertTrue(NewsIndexPage.can_create_at(section))
        self.assertTrue(NewsIndexPage.can_create_at(content))

    @override_settings(FEATURE_FLAGS=_feature_flags(news_enabled=False))
    def test_a_news_index_page_is_not_creatable_when_the_feature_is_disabled(self):
        self.assertFalse(NewsIndexPage.can_create_at(self.root_page))

    @override_settings(FEATURE_FLAGS=_feature_flags(news_enabled=False))
    def test_the_route_404s_without_the_feature(self):
        response = self.client.get(_article_url(self.index, self.published))
        self.assertEqual(response.status_code, 404)


@override_settings(FEATURE_FLAGS=_feature_flags(news_enabled=True))
class NewsArticleLayoutTests(TestCase):
    """An article has its own plain large-text hero, so it must not inherit the
    index page's masthead chrome (which would flush the top with no band to fill
    it). Breadcrumbs still follow the index page's show_breadcrumbs switch."""

    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root_page = self.site.root_page.specific
        self.article = NewsArticle.objects.create(
            title="Published article",
            standfirst="<p>The lead sentence.</p>",
            body="<p>Body copy.</p>",
        )

    def _article_under(self, *, slug, **index_kwargs):
        index = _make_index(self.root_page, slug=slug, title=slug, **index_kwargs)
        return self.client.get(_article_url(index, self.article))

    def test_a_styled_index_leaves_the_article_top_unflushed(self):
        response = self._article_under(slug="styled", hero_style=HeroStyle.STYLED)
        self.assertContains(response, "hero__title")  # the plain hero still renders
        self.assertNotContains(response, "masthead")
        self.assertNotContains(response, "govuk-!-padding-top-0")

    def test_a_combined_index_does_not_flush_or_invert_the_article(self):
        response = self._article_under(slug="combined", hero_style=HeroStyle.COMBINED)
        self.assertContains(response, "hero__title")
        self.assertNotContains(response, "masthead--combined")
        self.assertNotContains(response, "govuk-service-navigation--inverse")
        self.assertNotContains(response, "govuk-!-padding-0")

    def test_article_breadcrumbs_show_even_when_the_index_disables_them(self):
        shown = self._article_under(slug="with-crumbs", show_breadcrumbs=True)
        self.assertContains(shown, "govuk-breadcrumbs")

        # The article keeps its own trail even if the index page hides breadcrumbs.
        still_shown = self._article_under(slug="no-crumbs", show_breadcrumbs=False)
        self.assertContains(still_shown, "govuk-breadcrumbs")
