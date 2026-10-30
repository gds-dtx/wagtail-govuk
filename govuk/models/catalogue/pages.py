"""The content catalogue's page types.

The catalogue is a branch of the page tree, edited in the admin like any other:

- ``ContentMainPage``: a content page in every way, that is also the
  catalogue's root, with an ``all/`` route listing every entry beneath it;
- ``ContentGroupPage``: a group of entries ("Standards and policies"), listing
  its entries as cards, a section for each type;
- an entry page for each layout (``ContentEntryRequirementsPage`` and the
  rest), which holds what the catalogue records about the entry and its front
  page's content;
- the pages within an entry: a standard's points, a method's steps, a guide's
  pages.

An external entry (``ContentEntryExternalPage``) is a page so that it is
edited, published and moderated where every other entry is, but it has no
content of its own: its card links to where it is published, and so does its
own address.

Gated by ``FEATURE_FLAGS["GUIDANCE"]``.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.forms import CheckboxSelectMultiple
from django.http import Http404, HttpResponseRedirect
from django.urls import reverse
from modelcluster.contrib.taggit import ClusterTaggableManager
from modelcluster.fields import ParentalManyToManyField
from wagtail import blocks
from wagtail.admin.panels import FieldPanel, MultiFieldPanel, Panel
from wagtail.contrib.routable_page.models import RoutablePageMixin, path
from wagtail.fields import StreamField
from wagtail.models import Page

from govuk.catalogue import presentation

from ..blocks import ContentBodyBlock
from ..pages import (
    BaseContentPage,
    BreadcrumbMixin,
    CardTagOptionsMixin,
    ContentBodyContextMixin,
    HeroStyle,
    HeroStyleMixin,
)
from ..panels import base_content_panels, content_settings_panels, page_settings_panels
from .blocks import CatalogueBodyBlock, PolicySectionBlock, ToolLinkBlock
from .vocabulary import CatalogueLayout

# Every entry page type, by label: what a group page may hold, and what an
# entry's "related" may point at.
ENTRY_PAGE_TYPES = [
    "govuk.ContentEntryRequirementsPage",
    "govuk.ContentEntryPolicyPage",
    "govuk.ContentEntryGuidePage",
    "govuk.ContentEntryStepsPage",
    "govuk.ContentEntryToolPage",
    "govuk.ContentEntryExternalPage",
]


def catalogue_enabled() -> bool:
    return bool(settings.FEATURE_FLAGS.get("GUIDANCE"))


def without_guidance_pages(queryset):
    """``queryset`` minus the catalogue's pages, on a site without the catalogue.

    The ``without_framework_pages`` counterpart, applied at the same public
    listings (the pages API, search, the service navigation, tag listings), for
    the same reason: a page that 404s should not be offered as a link. Not
    applied in the admin, where an editor needs to find a page to delete it.
    """
    if catalogue_enabled():
        return queryset
    return queryset.not_type(*catalogue_page_models())


def catalogue_page_models() -> tuple:
    return (
        ContentMainPage,
        ContentGroupPage,
        *entry_page_models(),
        ContentRequirementPage,
        ContentStepPage,
        ContentGuidePage,
    )


def entry_page_models() -> tuple:
    return (
        ContentEntryRequirementsPage,
        ContentEntryPolicyPage,
        ContentEntryGuidePage,
        ContentEntryStepsPage,
        ContentEntryToolPage,
        ContentEntryExternalPage,
    )


def catalogue_type_labels(pages) -> dict[int, str]:
    """The type label ("Standard") of each catalogue entry among ``pages``, by page id.

    A page within an entry (a standard's point, a guide's page) has its
    entry's. Pages that are not the catalogue's are left out, so the keys are
    also which of ``pages`` are catalogue content. ``pages`` are specific
    pages; the entries above the pages within them are found in one query,
    however many there are.
    """
    from .vocabulary import CatalogueType

    entry_models = entry_page_models()
    sub_page_models = (ContentRequirementPage, ContentStepPage, ContentGuidePage)
    entries = [page for page in pages if isinstance(page, entry_models)]
    sub_pages = [page for page in pages if isinstance(page, sub_page_models)]
    if not entries and not sub_pages:
        return {}

    labels = dict(CatalogueType.objects.values_list("pk", "label"))
    by_page = {page.pk: labels.get(page.entry_type_id, "") for page in entries}
    if sub_pages:
        ancestor_paths = {
            page.path[:end]
            for page in sub_pages
            for end in range(page.steplen, len(page.path), page.steplen)
        }
        by_path = {
            entry.path: labels.get(entry.entry_type_id, "")
            for entry in Page.objects.filter(path__in=ancestor_paths)
            .type(*entry_models)
            .specific()
        }
        for page in sub_pages:
            # The nearest entry above it: a guide page can sit deep in its guide.
            for end in range(len(page.path) - page.steplen, 0, -page.steplen):
                if page.path[:end] in by_path:
                    by_page[page.pk] = by_path[page.path[:end]]
                    break
    return by_page


class CatalogueFlagMixin:
    """Not creatable, and not served, on a site without the catalogue.

    ``can_create_at`` and ``can_exist_under`` keep an editor from making one,
    but the page import is not the admin, so ``serve`` 404s too.
    """

    @classmethod
    def can_create_at(cls, parent):
        return catalogue_enabled() and super().can_create_at(parent)

    @classmethod
    def can_exist_under(cls, parent):
        return catalogue_enabled() and super().can_exist_under(parent)

    def serve(self, request, *args, **kwargs):
        if not catalogue_enabled():
            raise Http404
        return super().serve(request, *args, **kwargs)


class CatalogueToolsPanel(Panel):
    """Links to the catalogue's import and export, on the main page's edit view."""

    class BoundPanel(Panel.BoundPanel):
        template_name = "wagtailadmin/catalogue/tools_panel.html"

        def get_context_data(self, parent_context=None):
            context = super().get_context_data(parent_context)
            page = self.instance
            context["page"] = page
            if page and page.pk:
                context["import_url"] = reverse("govuk_catalogue_import", args=[page.pk])
                context["export_url"] = reverse("govuk_catalogue_export", args=[page.pk])
            return context


class ContentMainPage(
    CatalogueFlagMixin,
    ContentBodyContextMixin,
    RoutablePageMixin,
    HeroStyleMixin,
    BreadcrumbMixin,
    CardTagOptionsMixin,
    BaseContentPage,
):
    """The catalogue's root: a content page, with its groups beneath it."""

    parent_page_types = [
        "wagtailcore.Page",
        "govuk.ContentPage",
        "govuk.TagListingsPage",
        "govuk.FrameworkMainPage",
    ]
    subpage_types = ["govuk.ContentGroupPage", "govuk.ContentPage", "govuk.TagListingsPage"]

    template = "govuk/content_page.html"
    tags = ClusterTaggableManager(through="govuk.ContentMainPageTag", blank=True)

    content_panels = base_content_panels() + [
        CatalogueToolsPanel(heading="Catalogue import and export"),
    ]
    settings_panels = content_settings_panels()

    class Meta:
        verbose_name = "content main page"

    @path("all/")
    def serve_all(self, request):
        """Every entry beneath the page, searchable and filterable.

        ``all`` is the one child slug this shadows.
        """
        if not catalogue_enabled():
            raise Http404
        # The page's own hero is its own page's; "all" carries its heading in
        # the content.
        self.hero_style = HeroStyle.NONE
        context = presentation.all_context(self, request)
        return self.render(request, template="catalogue/all_page.html", context_overrides=context)

    def entries(self):
        """Every live entry beneath the page, as their specific pages."""
        return presentation.live_entries(Page.objects.descendant_of(self))


class ContentGroupPage(CatalogueFlagMixin, Page):
    """A group of entries, listed as cards: a section for each type."""

    description = models.TextField(
        blank=True, help_text="Shown under the heading, and on the group's card elsewhere."
    )
    body = StreamField(
        ContentBodyBlock(),
        blank=True,
        use_json_field=True,
        verbose_name="Content blocks",
        help_text="Optional. Shown under the description, above the entries.",
    )

    parent_page_types = ["govuk.ContentMainPage"]
    subpage_types = ENTRY_PAGE_TYPES
    template = "catalogue/group_page.html"

    content_panels = Page.content_panels + [FieldPanel("description"), FieldPanel("body")]
    settings_panels = page_settings_panels()

    class Meta:
        verbose_name = "content group page"

    def get_context(self, request, *args, **kwargs):
        context = super().get_context(request, *args, **kwargs)
        context.update(presentation.group_context(self, request))
        return context


def entry_type_field(layout: str | None = None):
    """An entry's type, limited to the types that use the entry's layout."""
    return models.ForeignKey(
        "govuk.CatalogueType",
        on_delete=models.PROTECT,
        related_name="+",
        limit_choices_to={"layout": layout} if layout else {},
        verbose_name="Type",
    )


class ContentEntryPage(CatalogueFlagMixin, Page):
    """What the catalogue records about an entry, whatever its layout."""

    layout: str = ""

    short_description = models.CharField(
        max_length=180, help_text="One sentence, for the card that links to the entry."
    )
    long_description = models.TextField(
        max_length=1200, help_text="The opening paragraph of the entry's front page."
    )
    entry_type = entry_type_field()
    primary = models.BooleanField(
        default=False,
        verbose_name="Primary",
        help_text="Listed first in its section, with a darker edge to its card.",
    )
    withdrawn = models.BooleanField(
        default=False, help_text="Still listed, with a withdrawn notice."
    )
    scope = models.ForeignKey(
        "govuk.CatalogueScope", on_delete=models.PROTECT, related_name="+"
    )
    professions = ParentalManyToManyField("govuk.CatalogueProfession", related_name="+", blank=True)
    topics = ParentalManyToManyField("govuk.GovukTag", related_name="+", blank=True)
    owner_organisation = models.CharField(max_length=200, verbose_name="Organisation")
    owner_team = models.CharField(max_length=200, blank=True, verbose_name="Team")
    contact_email = models.EmailField(blank=True)
    contact_url = models.URLField(blank=True, verbose_name="Contact page")
    contact_slack = models.CharField(
        max_length=80, blank=True, verbose_name="Slack channel", help_text="Like #channel-name."
    )
    applies_to = models.TextField(
        max_length=300, blank=True, verbose_name="Who it is for", help_text="One sentence."
    )
    source_name = models.CharField(
        max_length=80, blank=True, help_text="Where it is published, such as GOV.UK."
    )
    source_url = models.URLField(max_length=500, blank=True, verbose_name="Source address")
    version = models.CharField(max_length=40, blank=True)
    effective_from = models.DateField(null=True, blank=True)
    last_reviewed_on = models.DateField(null=True, blank=True)
    next_review_on = models.DateField(null=True, blank=True)
    related_entries = StreamField(
        [("entry", blocks.PageChooserBlock(page_type=ENTRY_PAGE_TYPES))],
        blank=True,
        use_json_field=True,
        verbose_name="Related entries",
    )

    parent_page_types = ["govuk.ContentGroupPage"]
    settings_panels = page_settings_panels()

    class Meta:
        abstract = True

    @classmethod
    def entry_panels(cls, *layout_panels) -> list:
        return Page.content_panels + [
            MultiFieldPanel(
                [
                    FieldPanel("entry_type"),
                    FieldPanel("short_description"),
                    FieldPanel("long_description"),
                    FieldPanel("primary"),
                    FieldPanel("withdrawn"),
                ],
                heading="Entry",
            ),
            MultiFieldPanel(
                [
                    FieldPanel("scope"),
                    FieldPanel("professions", widget=CheckboxSelectMultiple),
                    FieldPanel("topics", widget=CheckboxSelectMultiple),
                ],
                heading="Filters",
            ),
            MultiFieldPanel(
                [
                    FieldPanel("owner_organisation"),
                    FieldPanel("owner_team"),
                    FieldPanel("contact_email"),
                    FieldPanel("contact_url"),
                    FieldPanel("contact_slack"),
                    FieldPanel("applies_to"),
                ],
                heading="Owner",
            ),
            *layout_panels,
            MultiFieldPanel(
                [
                    FieldPanel("version"),
                    FieldPanel("effective_from"),
                    FieldPanel("last_reviewed_on"),
                    FieldPanel("next_review_on"),
                ],
                heading="Version and review",
            ),
            MultiFieldPanel(
                [FieldPanel("source_name"), FieldPanel("source_url")], heading="Source"
            ),
            FieldPanel("related_entries"),
        ]

    def clean(self):
        super().clean()
        if self.entry_type_id and self.layout and self.entry_type.layout != self.layout:
            raise ValidationError(
                {"entry_type": f"Choose a type that uses the {self.layout} layout."}
            )

    def save(self, *args, **kwargs):
        # Search finds pages by their summary: an entry's is its card's sentence.
        if not self.search_description:
            self.search_description = self.short_description
        return super().save(*args, **kwargs)

    def get_context(self, request, *args, **kwargs):
        context = super().get_context(request, *args, **kwargs)
        context.update(presentation.entry_context(self, request, document=self))
        return context

    def serve(self, request, *args, **kwargs):
        response = super().serve(request, *args, **kwargs)
        return presentation.with_image_policy(response)


class ContentEntryRequirementsPage(ContentEntryPage):
    """A standard or principles: an introduction, then numbered points, each a page."""

    layout = CatalogueLayout.REQUIREMENTS
    entry_type = entry_type_field(CatalogueLayout.REQUIREMENTS)
    body = StreamField(
        CatalogueBodyBlock(), blank=True, use_json_field=True,
        verbose_name="Introduction", help_text="Before the numbered points.",
    )
    closing_body = StreamField(
        CatalogueBodyBlock(), blank=True, use_json_field=True,
        verbose_name="After the points",
    )

    subpage_types = ["govuk.ContentRequirementPage"]
    template = "catalogue/layouts/requirements.html"
    content_panels = ContentEntryPage.entry_panels(FieldPanel("body"), FieldPanel("closing_body"))

    class Meta:
        verbose_name = "catalogue entry: requirements"


class ContentEntryPolicyPage(ContentEntryPage):
    """A policy: an introduction, then numbered sections in an accordion."""

    layout = CatalogueLayout.POLICY
    entry_type = entry_type_field(CatalogueLayout.POLICY)
    body = StreamField(
        CatalogueBodyBlock(), blank=True, use_json_field=True, verbose_name="Introduction"
    )
    sections = StreamField(
        [("section", PolicySectionBlock())], blank=True, use_json_field=True,
        help_text="Numbered in this order.",
    )

    subpage_types = []
    template = "catalogue/layouts/policy.html"
    content_panels = ContentEntryPage.entry_panels(FieldPanel("body"), FieldPanel("sections"))

    class Meta:
        verbose_name = "catalogue entry: policy"


class ContentEntryGuidePage(ContentEntryPage):
    """Guidance or a strategy: a front page, and a handbook of pages beneath it."""

    layout = CatalogueLayout.GUIDE
    entry_type = entry_type_field(CatalogueLayout.GUIDE)
    body = StreamField(CatalogueBodyBlock(), blank=True, use_json_field=True)

    subpage_types = ["govuk.ContentGuidePage"]
    template = "catalogue/layouts/guide.html"
    content_panels = ContentEntryPage.entry_panels(FieldPanel("body"))

    class Meta:
        verbose_name = "catalogue entry: guide"


class ContentEntryStepsPage(ContentEntryPage):
    """A method: numbered step pages, and reference pages beside them."""

    layout = CatalogueLayout.STEPS
    entry_type = entry_type_field(CatalogueLayout.STEPS)
    body = StreamField(CatalogueBodyBlock(), blank=True, use_json_field=True)

    subpage_types = ["govuk.ContentStepPage", "govuk.ContentGuidePage"]
    template = "catalogue/layouts/steps.html"
    content_panels = ContentEntryPage.entry_panels(FieldPanel("body"))

    class Meta:
        verbose_name = "catalogue entry: steps"


class ContentEntryToolPage(ContentEntryPage):
    """A resource or product: what it is, and links to use it."""

    layout = CatalogueLayout.TOOL
    entry_type = entry_type_field(CatalogueLayout.TOOL)
    links = StreamField([("link", ToolLinkBlock())], blank=True, use_json_field=True)

    subpage_types = []
    template = "catalogue/layouts/tool.html"
    content_panels = ContentEntryPage.entry_panels(FieldPanel("links"))

    class Meta:
        verbose_name = "catalogue entry: tool"


class ContentEntryExternalPage(ContentEntryPage):
    """An entry published elsewhere: its card, and its address, go to the source."""

    subpage_types = []
    content_panels = ContentEntryPage.entry_panels()

    class Meta:
        verbose_name = "catalogue entry: external"

    def clean(self):
        super().clean()
        if not self.source_url:
            raise ValidationError({"source_url": "An external entry needs its source's address."})

    def serve(self, request, *args, **kwargs):
        if not catalogue_enabled():
            raise Http404
        return HttpResponseRedirect(self.source_url)


class ContentEntrySubPage(CatalogueFlagMixin, Page):
    """A page within an entry, laid out by the entry's layout."""

    body = StreamField(CatalogueBodyBlock(), blank=True, use_json_field=True)

    subpage_types = []
    settings_panels = page_settings_panels()

    class Meta:
        abstract = True

    @property
    def entry_page(self):
        """The entry this page is within: its nearest entry ancestor."""
        ancestor = (
            Page.objects.ancestor_of(self).type(*entry_page_models()).order_by("-depth").first()
        )
        return ancestor.specific if ancestor else None

    def get_template(self, request, *args, **kwargs):
        entry = self.entry_page
        return entry.template if entry else super().get_template(request, *args, **kwargs)

    def get_context(self, request, *args, **kwargs):
        context = super().get_context(request, *args, **kwargs)
        entry = self.entry_page
        if entry is not None:
            context.update(presentation.entry_context(entry, request, document=self))
        return context

    def serve(self, request, *args, **kwargs):
        response = super().serve(request, *args, **kwargs)
        return presentation.with_image_policy(response)


class ContentRequirementPage(ContentEntrySubPage):
    """One point of a standard, numbered by its place among its entry's points."""

    summary = models.TextField(
        max_length=300, blank=True, help_text="Its line in the standard's list of points."
    )

    parent_page_types = ["govuk.ContentEntryRequirementsPage"]
    content_panels = Page.content_panels + [FieldPanel("summary"), FieldPanel("body")]

    class Meta:
        verbose_name = "catalogue requirement"


class ContentStepPage(ContentEntrySubPage):
    """One step of a method, numbered by its place among its entry's steps."""

    summary = models.TextField(
        max_length=300, blank=True, help_text="Its line in the method's list of steps."
    )

    parent_page_types = ["govuk.ContentEntryStepsPage"]
    content_panels = Page.content_panels + [FieldPanel("summary"), FieldPanel("body")]

    class Meta:
        verbose_name = "catalogue step"


class ContentGuidePage(ContentEntrySubPage):
    """A page of a guide (to any depth), or a method's reference page."""

    parent_page_types = [
        "govuk.ContentEntryGuidePage",
        "govuk.ContentGuidePage",
        "govuk.ContentEntryStepsPage",
    ]
    subpage_types = ["govuk.ContentGuidePage"]
    content_panels = Page.content_panels + [FieldPanel("body")]

    class Meta:
        verbose_name = "catalogue guide page"
