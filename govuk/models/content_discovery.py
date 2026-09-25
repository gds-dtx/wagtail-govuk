import hashlib

from django.db import models
from modelcluster.contrib.taggit import ClusterTaggableManager
from modelcluster.fields import ParentalKey
from modelcluster.models import ClusterableModel
from wagtail.admin.panels import FieldPanel, InlinePanel
from wagtail.contrib.settings.models import BaseSiteSetting, register_setting
from wagtail.fields import StreamField
from wagtail.models import Orderable
from wagtail.snippets.blocks import SnippetChooserBlock

from govuk.utils import row_id_from_text

from .tags import ExternalContentItemTag, GovukTag


@register_setting(icon="search")
class ContentDiscoverySettings(ClusterableModel, BaseSiteSetting):
    panels = [
        InlinePanel(
            "sources",
            heading="Content discovery sources",
            label="Source",
            help_text="Add one or more remote URLs for sitemaps, APIs, JSON feeds, RSS or Atom feeds.",
        ),
    ]

    class Meta:
        verbose_name = "Content discovery"
        verbose_name_plural = "Content discovery"




class ContentDiscoverySource(Orderable):
    settings = ParentalKey(
        "govuk.ContentDiscoverySettings",
        on_delete=models.CASCADE,
        related_name="sources",
    )
    name = models.CharField(
        max_length=255,
        blank=True,
        help_text="Optional display name for this source, for example Technology in government blog.",
    )
    url = models.URLField(
        max_length=500,
        help_text="Remote URL to discover content from, for example a sitemap, feed or API endpoint.",
    )
    disable_tls_verification = models.BooleanField(
        default=False,
        verbose_name="Disable TLS verification",
        help_text="When enabled, certificate verification is skipped for this source.",
    )
    send_signed_bearer_jwt = models.BooleanField(
        default=False,
        verbose_name="Send signed bearer JWT",
        help_text=(
            "When enabled, sends an Authorization bearer token signed using this site's "
            "primary key from Settings > Signing keys."
        ),
    )
    sync_source = models.BooleanField(
        default=False,
        verbose_name="Sync from remote source and hide any missing",
        help_text=(
            "When enabled, this remote source will be synced and any "
            "missing items previously discovered will be hidden. Disabled "
            "(default) will just keep adding discovered content."
        ),
    )
    consume_tags = models.BooleanField(
        default=False,
        verbose_name="Consume tags from remote source",
        help_text=(
            "When enabled, if the remote source content has tags they will be "
            "applied to the discovered content."
        ),
    )
    default_tags = StreamField(
        [
            (
                "tag",
                SnippetChooserBlock(
                    "govuk.GovukTag",
                    required=False,
                ),
            )
        ],
        blank=True,
        use_json_field=True,
        help_text="Optional tags to apply to discovered content from this source.",
    )

    panels = [
        FieldPanel("name"),
        FieldPanel("url"),
        FieldPanel("disable_tls_verification"),
        FieldPanel("send_signed_bearer_jwt"),
        FieldPanel("sync_source"),
        FieldPanel("consume_tags"),
        FieldPanel("default_tags"),
    ]

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self) -> str:
        return self.name or self.url

    @staticmethod
    def _extract_tag_id(value) -> int | None:
        if type(value) is int and value > 0:
            return value
        if isinstance(value, str):
            parsed = row_id_from_text(value)
            if parsed is not None and parsed > 0:
                return parsed
        tag_pk = getattr(value, "pk", None)
        if type(tag_pk) is int and tag_pk > 0:
            return tag_pk
        if isinstance(value, dict):
            for key in ("value", "id", "pk"):
                extracted = ContentDiscoverySource._extract_tag_id(value.get(key))
                if extracted:
                    return extracted
        return None

    def get_default_tag_ids(self) -> list[int]:
        tag_ids: list[int] = []
        seen: set[int] = set()

        for block in self.default_tags:
            tag_id = self._extract_tag_id(getattr(block, "value", None))
            if tag_id and tag_id not in seen:
                tag_ids.append(tag_id)
                seen.add(tag_id)

        # Some environments return chooser values as raw IDs in JSON;
        # fall back to raw stream data if resolved block values yielded none.
        if not tag_ids:
            for raw_block in getattr(self.default_tags, "raw_data", []) or []:
                tag_id = self._extract_tag_id(raw_block)
                if tag_id and tag_id not in seen:
                    tag_ids.append(tag_id)
                    seen.add(tag_id)
        return tag_ids

    def get_default_tags(self) -> list["GovukTag"]:
        tag_ids = self.get_default_tag_ids()
        if not tag_ids:
            return []

        tags_by_id = {tag.pk: tag for tag in GovukTag.objects.filter(pk__in=tag_ids)}
        return [tags_by_id[tag_id] for tag_id in tag_ids if tag_id in tags_by_id]




class ExternalContentItem(ClusterableModel):
    key = models.CharField(
        max_length=64,
        unique=True,
        editable=False,
        db_index=True,
        help_text="SHA256 hash of the URL.",
    )
    source = models.ForeignKey(
        "govuk.ContentDiscoverySource",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="external_content_items",
    )
    url = models.URLField(
        max_length=500,
        unique=True,
        help_text="Remote URL for the discovered content entry.",
    )
    title = models.CharField(
        max_length=255,
        blank=True,
        help_text="Optional title for this external content item.",
    )
    summary = models.TextField(
        blank=True,
        help_text="Optional summary or excerpt.",
    )
    published_at = models.DateTimeField(
        blank=True,
        null=True,
        help_text="Optional publication date from the source.",
    )
    created_at = models.DateTimeField(
        blank=True,
        null=True,
        help_text="Optional created date from the source.",
    )
    updated_at = models.DateTimeField(
        blank=True,
        null=True,
        help_text="Optional updated date from the source.",
    )
    tags = ClusterTaggableManager(through="govuk.ExternalContentItemTag", blank=True)
    hidden = models.BooleanField(
        default=False,
        help_text="Hide this item from external content listings.",
    )
    private = models.BooleanField(
        default=False,
        help_text="Set this item private, accessible to any logged-in users.",
    )
    metadata = models.JSONField(
        blank=True,
        default=dict,
        help_text="Optional source-specific metadata.",
    )
    first_seen_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now=True)

    panels = [
        FieldPanel("source"),
        FieldPanel("url"),
        FieldPanel("title"),
        FieldPanel("summary"),
        FieldPanel("published_at"),
        FieldPanel("created_at"),
        FieldPanel("updated_at"),
        InlinePanel("tagged_items", heading="Tags", label="Tag"),
        FieldPanel("hidden"),
        FieldPanel("private"),
        FieldPanel("metadata"),
    ]

    class Meta:
        ordering = ["-last_seen_at", "title", "url"]

    @staticmethod
    def build_key(url: str) -> str:
        return hashlib.sha256(url.strip().encode("utf-8")).hexdigest()

    def save(self, *args, **kwargs):
        self.url = self.url.strip()
        self.key = self.build_key(self.url)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.title or self.url

    @classmethod
    def upsert_from_url(cls, *, url: str, source=None, tags=None, **defaults):
        normalised_url = url.strip()
        item, _ = cls.objects.update_or_create(
            url=normalised_url,
            defaults={"source": source, **defaults},
        )
        tags_to_apply = []
        if source:
            tags_to_apply.extend(source.get_default_tags())
        if tags:
            tags_to_apply.extend(tags)
        if tags_to_apply:
            existing_tag_ids = set(item.tagged_items.values_list("tag_id", flat=True))
            pending_tag_ids: set[int] = set()
            rows_to_add = []
            for tag in tags_to_apply:
                tag_id = getattr(tag, "pk", None)
                if (
                    not tag_id
                    or tag_id in existing_tag_ids
                    or tag_id in pending_tag_ids
                ):
                    continue
                rows_to_add.append(ExternalContentItemTag(content_object=item, tag=tag))
                pending_tag_ids.add(tag_id)

            if rows_to_add:
                ExternalContentItemTag.objects.bulk_create(
                    rows_to_add,
                    ignore_conflicts=True,
                )
        return item




