from django.contrib.contenttypes.models import ContentType
from django.test import TestCase, override_settings
from wagtail.models import Workflow, WorkflowContentType

from govuk.models import GovukTag, NewsArticle


def _feature_flags(*, news_enabled: bool) -> dict[str, bool]:
    return {
        "SKILLS": False,
        "ORGANISATIONS": False,
        "PEOPLE_FINDER": False,
        "FEEDBACK": False,
        "NEWS": news_enabled,
    }


@override_settings(FEATURE_FLAGS=_feature_flags(news_enabled=True))
class NewsArticleModelTests(TestCase):
    def test_slug_is_set_from_the_title_when_blank(self):
        article = NewsArticle.objects.create(title="Spring Budget 2026")
        self.assertEqual(article.slug, "spring-budget-2026")

    def test_slugs_are_made_unique(self):
        first = NewsArticle.objects.create(title="Update")
        second = NewsArticle.objects.create(title="Update")
        self.assertEqual(first.slug, "update")
        self.assertEqual(second.slug, "update-2")

    def test_str_is_the_title(self):
        self.assertEqual(str(NewsArticle.objects.create(title="Hello")), "Hello")

    def test_articles_can_be_tagged(self):
        article = NewsArticle.objects.create(title="Tagged")
        article.tags.add(GovukTag.objects.create(slug="policy", name="Policy"))
        self.assertEqual(list(article.tags.values_list("slug", flat=True)), ["policy"])

    def test_a_draft_article_is_not_live_until_its_revision_is_published(self):
        article = NewsArticle(title="Draft article")
        article.live = False
        article.save()
        self.assertFalse(article.live)

        article.save_revision().publish()

        article.refresh_from_db()
        self.assertTrue(article.live)

    def test_a_workflow_can_be_assigned_to_news_articles(self):
        """Full editorial sign-off: an article picks up a workflow assigned to
        its content type, so submitting a revision goes through moderation."""
        article = NewsArticle.objects.create(title="Needs sign off")
        self.assertIsNone(article.get_workflow())

        workflow = Workflow.objects.create(name="News moderation")
        WorkflowContentType.objects.create(
            workflow=workflow,
            content_type=ContentType.objects.get_for_model(NewsArticle),
        )

        self.assertEqual(article.get_workflow(), workflow)
