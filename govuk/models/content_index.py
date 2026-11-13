"""The content index: every published, public item on the site, as Markdown.

One ``IndexedDocument`` per page, role or skill a reader can reach, each split
into ``IndexedChunk`` sections at its headings. Written by
``govuk.content_index`` as editors publish, and rebuilt by
``manage.py rebuild_content_index``; read by the MCP server.

``HarvestedSource`` holds a copy of the text of each page an external
catalogue entry points at, which the index adds to that entry.
``ContentEmbedding`` holds a vector of what each section and document means,
found by the hash of the text it was made from.

Kept while ``FEATURE_FLAGS["MCP"]`` or ``["ASK"]`` is on. The rows hold only what is already
published to anyone, here or at an entry's source, so there is nothing here a
signed-out reader could not see on the page itself.
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
        # A catalogue entry published elsewhere: the site holds what it records
        # about the entry, and its source is at ``source_url``.
        EXTERNAL = "external", "Published elsewhere"

    # Exactly one source is set. Deleting it deletes the row, so a page or
    # snippet that is gone cannot linger here.
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
    # The path on the site, without its host: the full address is the site's
    # root URL and this, so a change of hostname needs no rebuild.
    site = models.ForeignKey(
        "wagtailcore.Site",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="+",
    )
    url_path = models.CharField(max_length=500)
    source_url = models.URLField(max_length=500, blank=True)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    # What kind of thing it is, as search badges it: "Role", "Skill", a
    # catalogue type such as "Standard". Empty for an ordinary page.
    type_label = models.CharField(max_length=80, blank=True)
    breadcrumbs = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    markdown = models.TextField(blank=True)
    last_updated = models.DateTimeField(null=True, blank=True)
    # What the row was built from, so an unchanged item is skipped and a change
    # to how items are read (``content_index.INDEX_VERSION``) redoes them all.
    content_hash = models.CharField(max_length=64)
    index_version = models.PositiveIntegerField(default=0)
    indexed_at = models.DateTimeField(auto_now=True)
    # The hash of what is embedded for the document as a whole (its title,
    # type and description: what its card says), the key of its
    # ``ContentEmbedding``. See ``content_index.embeddings``.
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
    """One section of a document: the text under one heading."""

    document = models.ForeignKey(
        IndexedDocument, on_delete=models.CASCADE, related_name="chunks"
    )
    position = models.PositiveIntegerField()
    # The headings above the text, outermost first ("Testing › Screen readers").
    # Empty for the text before a document's first heading.
    heading_path = models.CharField(max_length=500, blank=True)
    # The heading's id on the page, where the page gives it one the same way
    # every time (the catalogue's pages do), so a link can go straight to it.
    anchor = models.CharField(max_length=200, blank=True)
    text = models.TextField()
    # The hash of what is embedded for the section, the key of its
    # ``ContentEmbedding``. Set when the section is stored, without calling
    # anything; the vector is made later (``embed_content_index``).
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
    """The text of an external catalogue entry's source page, fetched for the index.

    One per address, so entries that share a source share its copy. ``status``
    is how the last attempt went; ``markdown`` is the last text fetched, which a
    failed or refused attempt leaves in place. Written by
    ``govuk.content_index.harvest``.
    """

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
    # Where the page was in the end, after any redirects.
    fetched_url = models.URLField(max_length=500, blank=True)
    title = models.CharField(max_length=255, blank=True)
    markdown = models.TextField(blank=True)
    content_hash = models.CharField(max_length=64, blank=True)
    # For conditional requests: a source that has not changed answers 304.
    etag = models.CharField(max_length=200, blank=True)
    last_modified = models.CharField(max_length=100, blank=True)
    # The source's own date for its content, where it gives one (GOV.UK does).
    source_updated_at = models.DateTimeField(null=True, blank=True)
    # The last attempt, and the last one that reached the page.
    fetched_at = models.DateTimeField(null=True, blank=True)
    succeeded_at = models.DateTimeField(null=True, blank=True)
    failures = models.PositiveIntegerField(default=0, help_text="Failed attempts since the last success.")

    class Meta:
        verbose_name = "harvested source"
        ordering = ["url"]

    def __str__(self) -> str:
        return self.url


class ContentEmbedding(models.Model):
    """A vector of what one piece of text means, made by one embedding model.

    Found by the SHA-256 of the text (``text_hash``), not tied to the section
    or document it came from: those are deleted and remade whenever their
    document changes, and the text that did not change keeps its vector, so
    only new or changed text is sent to the model. One row per model, so
    another model can be tried without losing the first one's vectors. Rows
    no section or document points at any more are removed by
    ``embed_content_index``.

    The vector has no fixed length, so a model with another length needs no
    migration; with no index on it, every search compares against them all,
    which at the index's size takes milliseconds.
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
