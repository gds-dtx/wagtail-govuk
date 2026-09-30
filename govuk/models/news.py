"""News: an editorial snippet served through a routable index page.

Split out of the old monolithic ``models.py`` into this submodule with the rest
of the package. ``NewsArticle`` is the project's first workflow-enabled snippet
(draft/publish, revisions, locking, workflow sign-off and preview); a
``NewsIndexPage`` lists live articles and serves each at ``article/<slug>/``.
Gated by ``FEATURE_NEWS``. The tag through-models (``NewsArticleTag`` and
``NewsIndexPageTag``) live in ``tags.py`` with the rest of the package's tags.
"""

from django.conf import settings
from django.core.paginator import Paginator
from django.db import models
from django.http import Http404
from django.utils import timezone
from django.utils.text import slugify
from modelcluster.contrib.taggit import ClusterTaggableManager
from modelcluster.models import ClusterableModel
from wagtail.admin.panels import FieldPanel, InlinePanel, PublishingPanel
from wagtail.contrib.routable_page.models import RoutablePageMixin, path
from wagtail.fields import RichTextField
from wagtail.models import (
    DraftStateMixin,
    LockableMixin,
    Page,
    PreviewableMixin,
    RevisionMixin,
    Site,
    WorkflowMixin,
)

from .constants import SKILLS_AND_ROLES_BODY_RICH_TEXT_FEATURES
from .helpers import _next_unique_slug
from .pages import BreadcrumbMixin, HeroStyleMixin
from .panels import page_settings_panels


class NewsArticle(
    WorkflowMixin,
    DraftStateMixin,
    LockableMixin,
    RevisionMixin,
    PreviewableMixin,
    ClusterableModel,
):
    """A news article, authored as a snippet and served through a NewsIndexPage.

    Mirrors the roles pattern -- the article is a snippet, not a page, and a
    ``NewsIndexPage`` serves it at ``article/<slug>/`` -- but unlike the role and
    skill snippets it carries Wagtail's editorial mixins, so it has draft/publish,
    revisions, locking, workflow sign-off and preview. It is the first
    workflow-enabled snippet in the project. Gated by ``FEATURE_NEWS``.
    """

    title = models.CharField(max_length=255, help_text="The article headline.")
    slug = models.SlugField(
        max_length=120,
        unique=True,
        blank=True,
        help_text="Set automatically from the title; the article's URL segment.",
    )
    publication_date = models.DateField(
        default=timezone.now,
        help_text="Date shown on the article and used to order the news list.",
    )
    hero_image = models.ForeignKey(
        "wagtailimages.Image",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="Optional lead image shown on the article and on listing cards.",
    )
    standfirst = RichTextField(
        blank=True,
        features=["bold", "italic", "link"],
        help_text="Optional short introduction shown above the body.",
    )
    body = RichTextField(
        blank=True,
        features=SKILLS_AND_ROLES_BODY_RICH_TEXT_FEATURES,
    )
    author = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Optional author name displayed on the article.",
    )
    featured = models.BooleanField(
        default=False,
        help_text="Feature this article, so index pages can surface it first.",
    )
    tags = ClusterTaggableManager(through="govuk.NewsArticleTag", blank=True)

    # The PublishingPanel (go-live/expiry) is offered only when something can
    # act on a schedule, the same honest gate page_settings_panels() applies to
    # pages: without the publish_scheduled cron a scheduled date silently never
    # fires. Draft/publish and workflow sign-off do not need this panel.
    panels = [
        FieldPanel("title"),
        FieldPanel("slug"),
        FieldPanel("publication_date"),
        FieldPanel("hero_image"),
        FieldPanel("standfirst"),
        FieldPanel("body"),
        FieldPanel("author"),
        FieldPanel("featured"),
        InlinePanel("tagged_items", heading="Tags", label="Tag"),
    ] + ([PublishingPanel()] if getattr(settings, "SCHEDULED_PUBLISHING", False) else [])

    class Meta:
        verbose_name = "News article"
        verbose_name_plural = "News articles"
        ordering = ["-publication_date", "-id"]

    def __str__(self):
        return self.title or self.slug

    def _assign_slug(self):
        self.title = (self.title or "").strip()
        slug_candidate = slugify((self.slug or self.title or "").strip())[:120]
        self.slug = _next_unique_slug(
            model_class=type(self),
            candidate=slug_candidate,
            instance_id=self.pk,
            fallback="news",
        )

    def clean(self):
        super().clean()
        self._assign_slug()

    def save(self, *args, **kwargs):
        self._assign_slug()
        super().save(*args, **kwargs)

    @property
    def url(self) -> str:
        return news_article_url(self)

    def get_preview_template(self, request, mode_name):
        return "govuk/news_article.html"

    def get_preview_context(self, request, mode_name):
        # No page serves the snippet in preview, so the template is driven
        # entirely by the ``article`` in context (it extends base.html, not
        # wagtailcore/page.html, for exactly this reason).
        context = super().get_preview_context(request, mode_name)
        context.update(
            {
                "article": self,
                "page_heading": self.title,
                "breadcrumbs": [],
            }
        )
        return context


class NewsIndexPage(RoutablePageMixin, HeroStyleMixin, BreadcrumbMixin, Page):
    """Lists news articles and serves each at ``article/<slug>/``.

    Any number may exist, placed anywhere the general containers allow. Each can
    be limited to articles carrying particular tags -- configured here in the
    editor, not filtered on the front end; with no tags it lists every live
    article. Gated by ``FEATURE_NEWS``.
    """

    hero_title = models.CharField(
        max_length=255,
        blank=True,
        help_text="Optional hero heading. If blank, the page title is used.",
    )
    hero_intro = RichTextField(
        blank=True,
        features=["bold", "italic", "link"],
    )
    show_featured_first = models.BooleanField(
        default=False,
        verbose_name="Show featured articles first",
        help_text="List featured articles ahead of the rest.",
    )
    tags = ClusterTaggableManager(through="govuk.NewsIndexPageTag", blank=True)

    parent_page_types = [
        "govuk.ContentPage",
        "govuk.SectionPage",
        "govuk.TagListingsPage",
        "govuk.FrameworkSkillsPage",
        "govuk.FrameworkMainPage",
        "govuk.FrameworkContentPage",
    ]
    subpage_types = [
        "govuk.ContentPage",
        "govuk.SectionPage",
        "govuk.TagListingsPage",
    ]

    content_panels = Page.content_panels + [
        FieldPanel("hero_title"),
        FieldPanel("hero_intro"),
        InlinePanel(
            "tagged_items",
            heading="Only show articles tagged with (leave empty to show all)",
            label="Tag",
        ),
    ]

    settings_panels = page_settings_panels() + [
        FieldPanel("hero_style"),
        FieldPanel("show_breadcrumbs"),
        FieldPanel("show_featured_first"),
    ]

    @classmethod
    def can_create_at(cls, parent):
        if not settings.FEATURE_FLAGS.get("NEWS"):
            return False
        return super().can_create_at(parent)

    @classmethod
    def can_exist_under(cls, parent):
        if not settings.FEATURE_FLAGS.get("NEWS"):
            return False
        return super().can_exist_under(parent)

    def serve(self, request, *args, **kwargs):
        # 404 on a site without the feature, as the framework pages do: the
        # page import can land one here regardless of the admin's guards.
        if not settings.FEATURE_FLAGS.get("NEWS"):
            raise Http404
        return super().serve(request, *args, **kwargs)

    def _configured_tag_ids(self) -> list[int]:
        return list(self.tags.values_list("id", flat=True))

    def listed_articles(self):
        """The live articles this page lists, in display order."""
        articles = NewsArticle.objects.filter(live=True)
        tag_ids = self._configured_tag_ids()
        if tag_ids:
            articles = articles.filter(tags__id__in=tag_ids).distinct()
        if self.show_featured_first:
            return articles.order_by("-featured", "-publication_date", "-id")
        return articles.order_by("-publication_date", "-id")

    def _article_context(self, request, article) -> dict:
        site = Site.find_for_request(request)
        home_url = site.root_page.get_url(request) if site else None
        index_url = self.get_url(request)
        breadcrumbs = []
        if home_url:
            breadcrumbs.append(
                {"title": "Home", "url": home_url, "is_current": False}
            )
        if index_url and index_url != home_url:
            breadcrumbs.append(
                {"title": self.title, "url": index_url, "is_current": False}
            )
        breadcrumbs.append(
            {"title": article.title, "url": None, "is_current": True}
        )
        return {
            "article": article,
            "page_heading": article.title,
            "breadcrumbs": breadcrumbs,
        }

    def get_context(self, request, *args, **kwargs):
        context = super().get_context(request, *args, **kwargs)
        paginator = Paginator(self.listed_articles(), 15)
        context["articles"] = paginator.get_page(request.GET.get("page"))
        return context

    @path("article/<slug:slug>/")
    def serve_article(self, request, slug):
        """Serve a news article from its snippet, or 404.

        Mirrors ``FrameworkMainPage.serve_role``: the article is a snippet and
        this route stands in for a page. Prefixed with ``article/`` so it does
        not shadow the index page's own child pages -- ``RoutablePageMixin``
        resolves routes before child-page routing. Only articles this page would
        list are served here, so its URLs match its listing; an article the tag
        filter excludes 404s.
        """
        if not settings.FEATURE_FLAGS.get("NEWS"):
            raise Http404
        article = self.listed_articles().filter(slug=slug).first()
        if article is None:
            raise Http404
        return self.render(
            request,
            template="govuk/news_article.html",
            context_overrides=self._article_context(request, article),
        )


def news_index_page_for(article):
    """The first live ``NewsIndexPage`` that would list ``article``, or ``None``.

    An article can appear under several index pages; its canonical URL (used by
    search and cross-links) is built from the first that lists it -- as a role
    resolves its URL through the single framework main page.
    """
    for index_page in NewsIndexPage.objects.live():
        if index_page.listed_articles().filter(pk=article.pk).exists():
            return index_page
    return None


def news_article_url(article) -> str:
    """The URL of an article: a ``NewsIndexPage``'s article route for its slug.

    Empty when no live index page lists it or the page has no URL yet (no site,
    no request), which the templates fall back on as plain text -- as role URLs
    do without a framework main page.
    """
    index_page = news_index_page_for(article)
    if index_page is None:
        return ""
    base = index_page.url
    if not base:
        return ""
    return base + index_page.reverse_subpage("serve_article", args=[article.slug])
