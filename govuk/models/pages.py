
from django.conf import settings
from django.core.paginator import Paginator
from django.db import models
from django.db.models.functions import Coalesce
from django.utils.text import slugify
from modelcluster.contrib.taggit import ClusterTaggableManager
from wagtail import blocks
from wagtail.admin.panels import FieldPanel, InlinePanel
from wagtail.fields import RichTextField, StreamField
from wagtail.images.blocks import ImageChooserBlock
from wagtail.models import Page, Site
from wagtail.snippets.blocks import SnippetChooserBlock

from govuk.utils import row_id_from_text

from .blocks import ContentBodyBlock, LinkBlock
from .constants import THIS_SITE_SOURCE_FILTER
from .content_discovery import ExternalContentItem
from .panels import (
    base_content_panels,
    content_settings_panels,
    page_settings_panels,
)
from .tags import (
    ContentPageTag,
    ExternalContentItemTag,
    FrameworkContentPageTag,
    FrameworkMainPageTag,
    GovukTag,
    SectionPageTag,
)


class BaseContentPage(Page):
    """Shared hero, body and settings fields for the content-style page types.

    ``ContentPage`` and the two framework page types (``FrameworkMainPage`` and
    ``FrameworkContentPage``) all carry the same hero/body/settings fields.
    Holding them on an abstract base keeps the three concrete types from each
    writing the same twelve fields out. Being abstract, the base contributes its
    columns to each subclass's own table, so every page type keeps its own
    concrete columns -- exactly what Wagtail's multi-table inheritance needs.
    """

    enable_hero_styling = models.BooleanField(
        default=False,
        verbose_name="Enable hero styling",
        help_text="When enabled, this page uses hero styling.",
    )
    enable_combined_service_navigation_and_hero_styling = models.BooleanField(
        default=False,
        verbose_name="Enable combined service navigation and hero styling",
        help_text="When enabled, this page uses a combined service navigation and hero styling.",
    )
    hero_title = models.CharField(
        max_length=255,
        blank=True,
        help_text="Optional hero heading. If blank, the page title is used.",
    )
    hero_intro = RichTextField(
        blank=True,
        features=["bold", "italic", "link"],
    )
    author = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Optional author name displayed above the main content.",
    )
    show_last_updated_date = models.BooleanField(
        default=False,
        verbose_name="Show last updated date",
        help_text="Show the page last updated date above the main content.",
    )
    show_page_content_metadata = models.BooleanField(
        default=False,
        verbose_name="Show page content metadata",
        help_text="Show page metadata above the main content.",
    )
    body = RichTextField(blank=True)
    body_blocks = StreamField(
        ContentBodyBlock(),
        blank=True,
        use_json_field=True,
        verbose_name="Tables and further content",
        help_text=(
            "Shown after the body above. To put a table part-way through a "
            "page, move the body text into a Text block here and add the "
            "table between the blocks."
        ),
    )
    enable_free_text_heading_navigation = models.BooleanField(
        default=False,
        verbose_name="Enable sidebar heading navigation",
        help_text="Show free text in a two-thirds and one-third layout with an automatic clickable heading list.",
    )

    class Meta:
        abstract = True




class ContentPage(BaseContentPage):
    parent_page_types = [
        "govuk.ContentPage",
        "govuk.FrameworkMainPage",
        "govuk.FrameworkContentPage",
        "govuk.SectionPage",
        "govuk.TagListingsPage",
        "govuk.FrameworkSkillsPage",
    ]
    subpage_types = [
        "govuk.ContentPage",
        "govuk.FrameworkMainPage",
        "govuk.SectionPage",
        "govuk.TagListingsPage",
        "govuk.FrameworkSkillsPage",
    ]
    tags = ClusterTaggableManager(through="govuk.ContentPageTag", blank=True)

    content_panels = base_content_panels()

    settings_panels = content_settings_panels()

    def get_context(self, request, *args, **kwargs):
        context = super().get_context(request, *args, **kwargs)
        # Plain content pages carry no role navigation, so the sidebar heading
        # navigation is offered whenever it is switched on.
        context["heading_navigation"] = self.enable_free_text_heading_navigation
        return context




class TagListingsPage(Page):
    class SortOrder(models.TextChoices):
        NEWEST_FIRST = "newest_first", "Newest first"
        ALPHABETICAL = "alphabetical_az", "Alphabetical (A-Z)"

    enable_hero_styling = models.BooleanField(
        default=False,
        verbose_name="Enable hero styling",
        help_text="When enabled, this page uses hero styling.",
    )
    enable_combined_service_navigation_and_hero_styling = models.BooleanField(
        default=False,
        verbose_name="Enable combined service navigation and hero styling",
        help_text="When enabled, this page uses a combined service navigation and hero styling.",
    )
    hero_title = models.CharField(
        max_length=255,
        blank=True,
        help_text="Optional hero heading. If blank, the page title is used.",
    )
    hero_intro = RichTextField(
        blank=True,
        features=["bold", "italic", "link"],
    )
    author = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Optional author name displayed above the main content.",
    )
    show_last_updated_date = models.BooleanField(
        default=False,
        verbose_name="Show last updated date",
        help_text="Show the page last updated date above the main content.",
    )
    show_page_content_metadata = models.BooleanField(
        default=False,
        verbose_name="Show page content metadata",
        help_text="Show page metadata above the main content.",
    )
    free_text = RichTextField(blank=True)
    enable_free_text_heading_navigation = models.BooleanField(
        default=False,
        verbose_name="Enable sidebar heading navigation",
        help_text="Show free text in a two-thirds and one-third layout with an automatic clickable heading list.",
    )
    enable_tag_filter = models.BooleanField(
        default=False,
        verbose_name="Enable tag filter",
        help_text="Show a tag filter control above the listings.",
    )
    enable_source_filter = models.BooleanField(
        default=False,
        verbose_name="Enable source filter",
        help_text="Show a source filter control above the listings.",
    )
    enable_source_display = models.BooleanField(
        default=False,
        verbose_name="Enable source display",
        help_text="Show source labels on listing cards.",
    )
    enable_tag_display = models.BooleanField(
        default=False,
        verbose_name="Enable tag display",
        help_text="Show tag labels on listing cards.",
    )
    show_private_cards_to_non_authenticated_users = models.BooleanField(
        default=False,
        verbose_name="Show private cards to non-authenticated users",
        help_text=(
            "Show cards for private pages to users who are not signed in. "
            "Opening those pages still requires signing in."
        ),
    )
    hide_last_updated = models.BooleanField(
        default=False,
        verbose_name="Hide last updated",
        help_text="Hide the last updated date below each listing.",
    )
    sort_order = models.CharField(
        max_length=20,
        choices=SortOrder.choices,
        default=SortOrder.NEWEST_FIRST,
        verbose_name="Sort order",
        help_text="Choose how listing cards are ordered.",
    )
    tags = ClusterTaggableManager(
        through="govuk.TagListingsPageTag",
        blank=True,
    )

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
        "govuk.FrameworkMainPage",
        "govuk.SectionPage",
        "govuk.TagListingsPage",
        "govuk.FrameworkSkillsPage",
    ]

    content_panels = Page.content_panels + [
        FieldPanel("hero_title"),
        FieldPanel("hero_intro"),
        FieldPanel("author"),
        InlinePanel("tagged_items", heading="Tags to list", label="Tag", min_num=1),
        FieldPanel("free_text"),
    ]

    settings_panels = page_settings_panels() + [
        FieldPanel("enable_hero_styling"),
        FieldPanel("enable_combined_service_navigation_and_hero_styling"),
        FieldPanel("show_last_updated_date"),
        FieldPanel("show_page_content_metadata"),
        FieldPanel("enable_source_filter"),
        FieldPanel("enable_source_display"),
        FieldPanel("enable_tag_filter"),
        FieldPanel("enable_tag_display"),
        FieldPanel("show_private_cards_to_non_authenticated_users"),
        FieldPanel("hide_last_updated"),
        FieldPanel("sort_order"),
        FieldPanel("enable_free_text_heading_navigation"),
    ]

    def _configured_tag_ids(self) -> list[int]:
        return list(self.tags.values_list("id", flat=True))

    def _this_site_source_labels(self, *, request=None) -> tuple[str, str]:
        site_name = ""
        if request is not None:
            site = Site.find_for_request(request)
            if isinstance(site, Site):
                site_name = (site.site_name or "").strip()

        display_label = site_name or "This site"
        filter_label = f"{site_name} (this site)" if site_name else "This site"
        return display_label, filter_label

    def _external_listing_queryset(self, *, tag_ids: list[int], request=None):
        if not tag_ids:
            return ExternalContentItem.objects.none()
        queryset = ExternalContentItem.objects.filter(
            hidden=False, tags__id__in=tag_ids
        )
        if request is None or not request.user.is_authenticated:
            queryset = queryset.filter(private=False)
        return queryset.distinct().select_related("source").prefetch_related("tags")

    def _page_listing_querysets(self, *, tag_ids: list[int], request=None):
        if not tag_ids:
            return []

        page_sort_updated = Coalesce(
            "last_published_at",
            "latest_revision_created_at",
            "first_published_at",
        )
        page_querysets = [
            ContentPage.objects.live()
            .filter(tags__id__in=tag_ids)
            .annotate(sort_updated=page_sort_updated)
            .prefetch_related("tags", "view_restrictions")
            .distinct(),
            SectionPage.objects.live()
            .filter(tags__id__in=tag_ids)
            .annotate(sort_updated=page_sort_updated)
            .prefetch_related("tags", "view_restrictions")
            .distinct(),
        ]
        # A tag listing is a platform page type and the framework's content
        # pages are not, so they are listed only where they can be read. See
        # ``without_framework_pages``; this one is a whole queryset rather than
        # a filter on a shared one, so there is no query to run at all. Roles
        # are snippets now rather than pages, so they no longer appear here.
        from .framework.pages import FrameworkContentPage, FrameworkMainPage

        if settings.FEATURE_FLAGS.get("SKILLS"):
            page_querysets += [
                FrameworkMainPage.objects.live()
                .filter(tags__id__in=tag_ids)
                .annotate(sort_updated=page_sort_updated)
                .prefetch_related("tags", "view_restrictions")
                .distinct(),
                FrameworkContentPage.objects.live()
                .filter(tags__id__in=tag_ids)
                .annotate(sort_updated=page_sort_updated)
                .prefetch_related("tags", "view_restrictions")
                .distinct(),
            ]
        is_authenticated = bool(request and request.user.is_authenticated)
        if (
            not is_authenticated
            and not self.show_private_cards_to_non_authenticated_users
        ):
            page_querysets = [queryset.public() for queryset in page_querysets]
        return page_querysets

    def _page_listing_items(
        self,
        *,
        tag_ids: list[int],
        selected_tag_id: int | None = None,
        request=None,
        this_site_source_label: str = "",
    ) -> list[dict]:
        page_querysets = self._page_listing_querysets(tag_ids=tag_ids, request=request)
        if not page_querysets:
            return []

        page_items: list[dict] = []
        for queryset in page_querysets:
            if selected_tag_id is not None:
                queryset = queryset.filter(tags__id=selected_tag_id)
            for page in queryset:
                page_items.append(
                    {
                        "id": page.id,
                        "url": page.url or page.url_path,
                        "title": page.hero_title or page.title,
                        "summary": page.hero_intro or page.search_description or "",
                        "source": (
                            {"name": this_site_source_label}
                            if this_site_source_label
                            else None
                        ),
                        "tags": [tag.name for tag in page.tags.all()],
                        "private": bool(page.view_restrictions.all()),
                        "metadata": {},
                        "updated_at": page.last_published_at or page.sort_updated,
                        "created_at": page.first_published_at,
                        "published_at": page.first_published_at,
                        "last_seen_at": page.last_published_at,
                        "sort_updated": page.sort_updated,
                    }
                )
        return page_items

    def _available_filter_tags(
        self, *, tag_ids: list[int], request=None
    ) -> list["GovukTag"]:
        if not tag_ids:
            return []

        available_tag_ids: set[int] = set(tag_ids)

        external_item_ids = list(
            self._external_listing_queryset(
                tag_ids=tag_ids, request=request
            ).values_list("id", flat=True)
        )
        if external_item_ids:
            available_tag_ids.update(
                ExternalContentItemTag.objects.filter(
                    content_object_id__in=external_item_ids
                ).values_list("tag_id", flat=True)
            )

        # Keyed by model rather than indexed by position: the framework page
        # querysets are not there at all on a site without the framework, and a
        # fixed ``page_querysets[2]`` made that an IndexError -- a 500 on every
        # tag listing page rather than a listing without the framework's pages
        # in it.
        from .framework.pages import FrameworkContentPage, FrameworkMainPage

        through_models = {
            ContentPage: ContentPageTag,
            SectionPage: SectionPageTag,
            FrameworkMainPage: FrameworkMainPageTag,
            FrameworkContentPage: FrameworkContentPageTag,
        }
        page_querysets = self._page_listing_querysets(tag_ids=tag_ids, request=request)
        for page_queryset in page_querysets:
            through_model = through_models[page_queryset.model]
            page_ids = list(page_queryset.values_list("id", flat=True))
            if page_ids:
                available_tag_ids.update(
                    through_model.objects.filter(
                        content_object_id__in=page_ids
                    ).values_list("tag_id", flat=True)
                )

        return list(
            GovukTag.objects.filter(id__in=available_tag_ids).order_by("name", "slug")
        )

    def get_listing_queryset(
        self,
        *,
        selected_tag_id: int | None = None,
        selected_source_id: int | str | None = None,
        request=None,
    ) -> list[dict]:
        # Keep this as the single data-source entry point so external content and
        # tagged Wagtail pages remain filtered and sorted consistently.
        configured_tag_ids = self._configured_tag_ids()
        if not configured_tag_ids:
            return []

        selected_source_key = selected_source_id
        if isinstance(selected_source_key, str):
            selected_source_key = selected_source_key.strip()
            if not selected_source_key:
                selected_source_key = None
            elif selected_source_key != THIS_SITE_SOURCE_FILTER:
                selected_source_key = row_id_from_text(selected_source_key)

        external_queryset = self._external_listing_queryset(
            tag_ids=configured_tag_ids,
            request=request,
        )
        if selected_tag_id is not None:
            external_queryset = external_queryset.filter(tags__id=selected_tag_id)
        external_queryset = external_queryset.annotate(
            sort_updated=Coalesce(
                "updated_at",
                "created_at",
                "published_at",
                "last_seen_at",
                "first_seen_at",
            )
        )
        if selected_source_key == THIS_SITE_SOURCE_FILTER:
            external_queryset = external_queryset.none()
        elif isinstance(selected_source_key, int):
            external_queryset = external_queryset.filter(source_id=selected_source_key)

        listing_items: list[dict] = []
        for item in external_queryset:
            source_label = ""
            if item.source is not None:
                source_label = (item.source.name or item.source.url or "").strip()
            listing_items.append(
                {
                    "id": item.id,
                    "url": item.url,
                    "title": item.title,
                    "summary": item.summary,
                    "source": {"name": source_label} if source_label else None,
                    "tags": [tag.name for tag in item.tags.all()],
                    "private": item.private,
                    "metadata": item.metadata or {},
                    "updated_at": item.updated_at,
                    "created_at": item.created_at,
                    "published_at": item.published_at,
                    "last_seen_at": item.last_seen_at,
                    "sort_updated": item.sort_updated,
                }
            )

        this_site_source_label, _ = self._this_site_source_labels(request=request)
        if selected_source_key in {None, THIS_SITE_SOURCE_FILTER}:
            listing_items.extend(
                self._page_listing_items(
                    tag_ids=configured_tag_ids,
                    selected_tag_id=selected_tag_id,
                    request=request,
                    this_site_source_label=this_site_source_label,
                )
            )

        if self.sort_order == self.SortOrder.ALPHABETICAL:
            listing_items.sort(
                key=lambda item: (
                    (item.get("title") or item.get("url") or "").strip().lower(),
                    item["id"],
                ),
            )
        else:
            listing_items.sort(
                key=lambda item: (
                    item["sort_updated"].timestamp() if item["sort_updated"] else 0.0,
                    item["id"],
                ),
                reverse=True,
            )
        return listing_items

    def get_context(self, request, *args, **kwargs):
        context = super().get_context(request, *args, **kwargs)

        configured_tag_ids = self._configured_tag_ids()
        available_tags = self._available_filter_tags(
            tag_ids=configured_tag_ids,
            request=request,
        )
        selected_tag = None
        selected_tag_slug = ""
        if self.enable_tag_filter:
            selected_tag_slug = (request.GET.get("tag") or "").strip().lower()
            if selected_tag_slug:
                selected_tag = next(
                    (tag for tag in available_tags if tag.slug == selected_tag_slug),
                    None,
                )

        available_sources = []
        selected_source_id = ""
        selected_source_label = ""
        selected_tag_id = selected_tag.id if selected_tag is not None else None
        _, this_site_filter_label = self._this_site_source_labels(request=request)
        available_sources.append(
            {
                "id": THIS_SITE_SOURCE_FILTER,
                "label": this_site_filter_label,
            }
        )
        source_queryset = self._external_listing_queryset(
            tag_ids=configured_tag_ids,
            request=request,
        )
        if selected_tag_id is not None:
            source_queryset = source_queryset.filter(tags__id=selected_tag_id)
        source_rows = (
            source_queryset.exclude(source__isnull=True)
            .values("source_id", "source__name", "source__url")
            .distinct()
            .order_by("source__name", "source__url")
        )
        for source_row in source_rows:
            source_id = source_row["source_id"]
            if source_id is None:
                continue
            source_label = (
                source_row["source__name"] or source_row["source__url"] or ""
            ).strip()
            if not source_label:
                continue
            available_sources.append(
                {
                    "id": str(source_id),
                    "label": source_label,
                }
            )

        if self.enable_source_filter:
            selected_source_id = (request.GET.get("source") or "").strip()
            selected_source = next(
                (
                    source
                    for source in available_sources
                    if source["id"] == selected_source_id
                ),
                None,
            )
            if selected_source is not None:
                selected_source_label = selected_source["label"]
            else:
                selected_source_id = ""

        selected_source_key: int | str | None = None
        if selected_source_id == THIS_SITE_SOURCE_FILTER:
            selected_source_key = THIS_SITE_SOURCE_FILTER
        elif selected_source_id:
            selected_source_key = int(selected_source_id)
        listing_items = self.get_listing_queryset(
            selected_tag_id=selected_tag_id,
            selected_source_id=selected_source_key,
            request=request,
        )

        paginator = Paginator(listing_items, 15)
        context["listing_items"] = paginator.get_page(request.GET.get("page"))
        context["available_tags"] = available_tags
        context["available_sources"] = available_sources
        context["selected_tag"] = selected_tag
        context["selected_source_id"] = selected_source_id
        context["selected_source_label"] = selected_source_label
        return context


class SectionPage(Page):
    enable_hero_styling = models.BooleanField(
        default=False,
        verbose_name="Enable hero styling",
        help_text="When enabled, this page uses hero styling.",
    )
    enable_combined_service_navigation_and_hero_styling = models.BooleanField(
        default=False,
        verbose_name="Enable combined service navigation and hero styling",
        help_text="When enabled, this page uses a combined service navigation and hero styling.",
    )
    hero_title = models.CharField(
        max_length=255,
        blank=True,
        help_text="Optional hero heading. If blank, the page title is used.",
    )
    hero_intro = RichTextField(
        blank=True,
        features=["bold", "italic", "link"],
    )
    author = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Optional author name displayed above the main content.",
    )
    show_last_updated_date = models.BooleanField(
        default=False,
        verbose_name="Show last updated date",
        help_text="Show the page last updated date above the main content.",
    )
    show_page_content_metadata = models.BooleanField(
        default=False,
        verbose_name="Show page content metadata",
        help_text="Show page metadata above the main content.",
    )
    enable_tag_filter = models.BooleanField(
        default=False,
        verbose_name="Enable tag filter",
        help_text="Show a tag filter control above the cards.",
    )
    enable_tag_display = models.BooleanField(
        default=False,
        verbose_name="Enable tag display",
        help_text="Show tag labels on cards.",
    )
    tags = ClusterTaggableManager(through="govuk.SectionPageTag", blank=True)
    rows = StreamField(
        [
            (
                "row",
                blocks.StructBlock(
                    [
                        (
                            "heading",
                            blocks.CharBlock(
                                required=False,
                                help_text="Optional heading for this row section.",
                            ),
                        ),
                        (
                            "cards",
                            blocks.ListBlock(
                                blocks.StructBlock(
                                    [
                                        (
                                            "title",
                                            blocks.CharBlock(
                                                required=True,
                                                max_length=120,
                                            ),
                                        ),
                                        (
                                            "image",
                                            ImageChooserBlock(
                                                required=False,
                                                help_text="Optional header image for this card.",
                                            ),
                                        ),
                                        (
                                            "image_fit",
                                            blocks.ChoiceBlock(
                                                choices=[
                                                    ("cover", "Cover"),
                                                    ("contain", "Contain"),
                                                ],
                                                default="cover",
                                                required=True,
                                                help_text=(
                                                    "How the header image should fit "
                                                    "inside the card."
                                                ),
                                            ),
                                        ),
                                        (
                                            "text",
                                            blocks.RichTextBlock(
                                                required=False,
                                                features=[
                                                    "bold",
                                                    "italic",
                                                    "link",
                                                    "ul",
                                                    "ol",
                                                ],
                                            ),
                                        ),
                                        (
                                            "link",
                                            LinkBlock(
                                                required=False,
                                            ),
                                        ),
                                        (
                                            "tags",
                                            blocks.ListBlock(
                                                SnippetChooserBlock(
                                                    "govuk.GovukTag",
                                                    required=False,
                                                ),
                                                required=False,
                                                help_text="Optional tags for this card.",
                                            ),
                                        ),
                                    ],
                                    icon="doc-full",
                                    label="Card",
                                ),
                                min_num=1,
                                max_num=60,
                                help_text="Add between 1 and 60 cards in this row.",
                            ),
                        ),
                    ],
                    icon="placeholder",
                    label="Row section",
                ),
            ),
        ],
        blank=True,
        help_text="Add one or more row sections. Each row can contain up to 60 cards.",
    )
    free_text = RichTextField(blank=True)
    enable_free_text_heading_navigation = models.BooleanField(
        default=False,
        verbose_name="Enable sidebar heading navigation",
        help_text="Show free text in a two-thirds and one-third layout with an automatic clickable heading list.",
    )

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
        "govuk.FrameworkMainPage",
        "govuk.SectionPage",
        "govuk.TagListingsPage",
        "govuk.FrameworkSkillsPage",
    ]

    content_panels = Page.content_panels + [
        FieldPanel("hero_title"),
        FieldPanel("hero_intro"),
        FieldPanel("author"),
        FieldPanel("rows"),
        FieldPanel("free_text"),
    ]

    settings_panels = page_settings_panels() + [
        FieldPanel("enable_hero_styling"),
        FieldPanel("enable_combined_service_navigation_and_hero_styling"),
        FieldPanel("show_last_updated_date"),
        FieldPanel("show_page_content_metadata"),
        FieldPanel("enable_tag_filter"),
        FieldPanel("enable_tag_display"),
        FieldPanel("enable_free_text_heading_navigation"),
        InlinePanel("tagged_items", heading="Tags", label="Tag"),
    ]

    @staticmethod
    def _card_tag_items(card) -> list[dict[str, str]]:
        items: list[dict[str, str]] = []
        seen: set[str] = set()
        for tag in card.get("tags", []):
            if not tag:
                continue

            key = (getattr(tag, "slug", "") or getattr(tag, "key", "")).strip().lower()
            value = (getattr(tag, "name", "") or getattr(tag, "value", "")).strip()

            if not key and isinstance(value, str):
                key = slugify(value).strip().lower()
            if not value and key:
                value = key
            if not key or not value or key in seen:
                continue

            seen.add(key)
            items.append({"key": key, "value": value})
        return items

    def get_context(self, request, *args, **kwargs):
        context = super().get_context(request, *args, **kwargs)

        prepared_rows: list[dict] = []
        available_tags_by_key: dict[str, str] = {}
        for block in self.rows:
            if block.block_type != "row":
                continue

            prepared_cards: list[dict] = []
            for card in block.value.get("cards", []):
                tag_items = self._card_tag_items(card)
                tag_keys = {item["key"] for item in tag_items}
                for item in tag_items:
                    available_tags_by_key[item["key"]] = item["value"]
                prepared_cards.append(
                    {
                        "card": card,
                        "tag_keys": tag_keys,
                    }
                )

            prepared_rows.append(
                {
                    "heading": block.value.get("heading"),
                    "cards": prepared_cards,
                }
            )

        selected_tag = None
        selected_tag_key = ""
        if self.enable_tag_filter:
            selected_tag_key = (request.GET.get("tag") or "").strip().lower()
            if selected_tag_key not in available_tags_by_key:
                selected_tag_key = ""

            if selected_tag_key:
                selected_tag = {
                    "key": selected_tag_key,
                    "value": available_tags_by_key[selected_tag_key],
                }

        row_sections: list[dict] = []
        for row in prepared_rows:
            cards = [
                card_entry["card"]
                for card_entry in row["cards"]
                if not selected_tag_key or selected_tag_key in card_entry["tag_keys"]
            ]
            if cards:
                row_sections.append(
                    {
                        "heading": row["heading"],
                        "cards": cards,
                    }
                )

        context["available_tags"] = [
            {"key": key, "value": value}
            for key, value in sorted(
                available_tags_by_key.items(),
                key=lambda row: row[1].lower(),
            )
        ]
        context["selected_tag"] = selected_tag
        context["row_sections"] = row_sections
        return context




