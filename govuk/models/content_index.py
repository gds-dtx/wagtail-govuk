"""The content index: every published, public page, role and skill as Markdown.

Holds only what a signed-out reader could already see. Read by MCP and AI search.
"""

from django.db import models
from django.db.models import Q
from pgvector.django import VectorField


class IndexedDocument(models.Model):
    class Kind(models.TextChoices):
        PAGE = "page", "Page"
        ROLE = "role", "Role"
        SKILL = "skill", "Skill"

    class Origin(models.TextChoices):
        SITE = "site", "This site"
        EXTERNAL = "external", "Published elsewhere"

    # Exactly one source is set; CASCADE so a deleted item cannot linger here.
    page = models.OneToOneField(
        "wagtailcore.Page",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="+",
    )
    role = models.OneToOneField(
        "govuk.GovukRole",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="+",
    )
    skill = models.OneToOneField(
        "govuk.GovukSkill",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="+",
    )
    kind = models.CharField(max_length=10, choices=Kind.choices)
    origin = models.CharField(max_length=10, choices=Origin.choices, default=Origin.SITE)
    site = models.ForeignKey(
        "wagtailcore.Site",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="+",
    )
    # No host, so a change of hostname needs no rebuild.
    url_path = models.CharField(max_length=500)
    source_url = models.URLField(max_length=500, blank=True)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    # The search badge ("Role", "Standard"...); empty for an ordinary page.
    type_label = models.CharField(max_length=80, blank=True)
    breadcrumbs = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    markdown = models.TextField(blank=True)
    last_updated = models.DateTimeField(null=True, blank=True)
    # Unchanged items are skipped; bumping ``INDEX_VERSION`` redoes them all.
    content_hash = models.CharField(max_length=64)
    index_version = models.PositiveIntegerField(default=0)
    indexed_at = models.DateTimeField(auto_now=True)
    # Hash of the title, type and description, keying its ``ContentEmbedding``.
    embedding_hash = models.CharField(max_length=64, blank=True, db_index=True)

    class Meta:
        verbose_name = "indexed document"
        ordering = ["title", "pk"]
        indexes = [models.Index(fields=["kind", "origin"], name="govuk_indexdoc_kind_origin")]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(page__isnull=False, role__isnull=True, skill__isnull=True)
                    | Q(page__isnull=True, role__isnull=False, skill__isnull=True)
                    | Q(page__isnull=True, role__isnull=True, skill__isnull=False)
                ),
                name="govuk_indexeddocument_one_source",
            )
        ]

    def __str__(self) -> str:
        return self.title


class IndexedChunk(models.Model):
    document = models.ForeignKey(
        IndexedDocument, on_delete=models.CASCADE, related_name="chunks"
    )
    position = models.PositiveIntegerField()
    # Outermost first ("Testing › Screen readers"); empty before the first heading.
    heading_path = models.CharField(max_length=500, blank=True)
    # Only where the page gives headings stable ids (the catalogue's pages do).
    anchor = models.CharField(max_length=200, blank=True)
    text = models.TextField()
    # Set on save without calling the model; ``embed_content_index`` makes the vector.
    embedding_hash = models.CharField(max_length=64, blank=True, db_index=True)

    class Meta:
        verbose_name = "indexed chunk"
        ordering = ["document", "position"]
        constraints = [
            models.UniqueConstraint(
                fields=["document", "position"], name="govuk_indexedchunk_position"
            )
        ]

    def __str__(self) -> str:
        return f"{self.document} #{self.position}"


class HarvestedSource(models.Model):
    """An external catalogue entry's source text; a failed fetch keeps the last copy."""

    class Status(models.TextChoices):
        OK = "ok", "Fetched"
        FAILED = "failed", "Could not be fetched"
        BLOCKED = "blocked", "Refused by the source"
        NO_TEXT = "no_text", "No text found"
        SKIPPED = "skipped", "Not fetched"

    url = models.URLField(max_length=500, unique=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.SKIPPED)
    reason = models.CharField(max_length=300, blank=True)
    http_status = models.PositiveSmallIntegerField(null=True, blank=True)
    fetched_url = models.URLField(max_length=500, blank=True)
    title = models.CharField(max_length=255, blank=True)
    markdown = models.TextField(blank=True)
    content_hash = models.CharField(max_length=64, blank=True)
    # For conditional requests.
    etag = models.CharField(max_length=200, blank=True)
    last_modified = models.CharField(max_length=100, blank=True)
    source_updated_at = models.DateTimeField(null=True, blank=True)
    fetched_at = models.DateTimeField(null=True, blank=True)
    succeeded_at = models.DateTimeField(null=True, blank=True)
    failures = models.PositiveIntegerField(default=0, help_text="Failed attempts since the last success.")

    class Meta:
        verbose_name = "harvested source"
        ordering = ["url"]

    def __str__(self) -> str:
        return self.url


class ContentEmbedding(models.Model):
    """One text's vector from one model, keyed by text hash.

    Keyed by hash, not by chunk, because chunks are remade on every change and
    unchanged text should keep its vector. No fixed length, so a new model needs
    no migration.
    """

    model = models.CharField(max_length=100)
    text_hash = models.CharField(max_length=64)
    vector = VectorField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "content embedding"
        constraints = [
            models.UniqueConstraint(fields=["model", "text_hash"], name="govuk_contentembedding_model_text")
        ]

    def __str__(self) -> str:
        return f"{self.model} {self.text_hash[:12]}"
