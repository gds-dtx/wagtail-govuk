"""The content catalogue: a content page that serves a GitHub catalogue repo.

An editor adds a ``ContentMainPage``: a content page in every way (its hero,
block body, tags and settings are a ``ContentPage``'s), pointed at a catalogue
repo. The catalogue's entries are synced into a ``CatalogueSnapshot`` and
served beneath the page, without a Wagtail page each:

- ``<group>/``: a group's entries as cards, primary ones first;
- ``all/``: every entry, filterable and searchable;
- ``<group>/<entry>/`` and below: an entry with content of its own (hosted in
  the catalogue, in another repo, or a tool with links), in its type's layout.

An external entry has no page: its card links to where it is published.

The snapshot is a model of its own, not fields on the page, so that publishing
an old revision of the page cannot bring back an old copy of the catalogue.

Gated by ``FEATURE_FLAGS["GUIDANCE"]``.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.http import Http404, HttpResponsePermanentRedirect
from django.urls import reverse
from modelcluster.contrib.taggit import ClusterTaggableManager
from wagtail.admin.panels import FieldPanel, MultiFieldPanel, Panel
from wagtail.contrib.routable_page.models import RoutablePageMixin, path, re_path
from wagtail.models import Page

from govuk.catalogue import context as catalogue_context
from govuk.catalogue.repository import RepositoryRef

from .pages import (
    BaseContentPage,
    BreadcrumbMixin,
    CardTagOptionsMixin,
    ContentBodyContextMixin,
    HeroStyle,
    HeroStyleMixin,
)
from .panels import base_content_panels, content_settings_panels

# The one first path segment beneath the page that is not a group's.
ALL_ENTRIES_SLUG = "all"


def without_guidance_pages(queryset):
    """``queryset`` minus content main pages, on a site without the catalogue.

    The ``without_framework_pages`` counterpart, applied at the same public
    listings (the pages API, search, the service navigation, tag listings), for
    the same reason: a page that 404s should not be offered as a link.
    """
    if settings.FEATURE_FLAGS.get("GUIDANCE"):
        return queryset
    return queryset.not_type(ContentMainPage)


class CatalogueSyncStatusPanel(Panel):
    """Read-only sync state on the page's edit view, with a link to sync it."""

    class BoundPanel(Panel.BoundPanel):
        template_name = "wagtailadmin/catalogue/sync_status_panel.html"

        def get_context_data(self, parent_context=None):
            context = super().get_context_data(parent_context)
            page = self.instance
            snapshot = page.snapshot if page and page.pk else None
            entries = list(snapshot.entries.all()) if snapshot else []
            groups = (snapshot.site.get("vocabularies", {}).get("groups", {})) if snapshot else {}
            context.update(
                {
                    "page": page,
                    "snapshot": snapshot,
                    "entry_count": len(entries),
                    "group_counts": [
                        {
                            "label": group["label"],
                            "count": sum(1 for e in entries if e.group == key),
                        }
                        for key, group in groups.items()
                    ],
                    "entry_errors": [e for e in entries if e.content_error],
                    "commit_url": page.repository().commit_url() if snapshot else "",
                    "sync_url": (
                        reverse("govuk_catalogue_sync", args=[page.pk]) if page.pk else ""
                    ),
                }
            )
            return context


class ContentMainPage(
    ContentBodyContextMixin,
    RoutablePageMixin,
    HeroStyleMixin,
    BreadcrumbMixin,
    CardTagOptionsMixin,
    BaseContentPage,
):
    """A content page that also serves a catalogue's groups and entries."""

    catalogue_repository = models.CharField(
        max_length=200,
        verbose_name="Catalogue repository",
        help_text="The GitHub repository holding the catalogue, as owner/name, like MattyHayward/content-catalogue-prototype",
    )
    catalogue_branch = models.CharField(max_length=200, default="main", verbose_name="Branch")

    parent_page_types = [
        "wagtailcore.Page",
        "govuk.ContentPage",
        "govuk.TagListingsPage",
        "govuk.FrameworkMainPage",
    ]
    subpage_types = ["govuk.ContentPage", "govuk.TagListingsPage"]

    # The page itself is a content page; the catalogue routes use their own.
    template = "govuk/content_page.html"
    tags = ClusterTaggableManager(through="govuk.ContentMainPageTag", blank=True)

    content_panels = base_content_panels() + [
        MultiFieldPanel(
            [FieldPanel("catalogue_repository"), FieldPanel("catalogue_branch")],
            heading="Catalogue",
            help_text=(
                "The groups and entries beneath this page come from the "
                "catalogue. Publishing syncs it the first time and whenever "
                "these settings change."
            ),
        ),
        CatalogueSyncStatusPanel(heading="Catalogue sync status"),
    ]
    settings_panels = content_settings_panels()

    class Meta:
        verbose_name = "content main page"

    @classmethod
    def can_create_at(cls, parent):
        if not settings.FEATURE_FLAGS.get("GUIDANCE"):
            return False
        return super().can_create_at(parent)

    @classmethod
    def can_exist_under(cls, parent):
        if not settings.FEATURE_FLAGS.get("GUIDANCE"):
            return False
        return super().can_exist_under(parent)

    def clean(self):
        super().clean()
        try:
            ref = RepositoryRef.parse(self.catalogue_repository, branch=self.catalogue_branch)
        except ValueError as exc:
            raise ValidationError({"catalogue_repository": str(exc)}) from exc
        self.catalogue_repository = f"{ref.owner}/{ref.name}"
        self.catalogue_branch = ref.branch

    # -- source and snapshot -------------------------------------------------

    @property
    def snapshot(self):
        try:
            return self.catalogue_snapshot
        except CatalogueSnapshot.DoesNotExist:
            return None

    def repository(self) -> RepositoryRef:
        snapshot = self.snapshot
        ref = RepositoryRef.parse(self.catalogue_repository, branch=self.catalogue_branch)
        return ref.at(snapshot.commit_sha) if snapshot and snapshot.commit_sha else ref

    def needs_sync(self) -> bool:
        """Never synced, or synced from source settings that have since changed."""
        snapshot = self.snapshot
        if snapshot is None or snapshot.synced_at is None:
            return True
        try:
            return snapshot.source_key != self.repository().source_key
        except ValueError:
            return False

    # -- serving --------------------------------------------------------------

    def route(self, request, path_components):
        """Catalogue routes for ``all`` and the groups; child pages for the rest.

        ``RoutablePageMixin`` resolves its routes before child pages, so a bare
        ``<group>/`` route would hide every child page. Only a first segment
        the catalogue owns goes to the routes; any other is an ordinary child
        page an editor made (a group's slug is the one name it shadows).
        """
        if path_components and not self._owns_segment(path_components[0]):
            return Page.route(self, request, path_components)
        return super().route(request, path_components)

    def _owns_segment(self, segment: str) -> bool:
        if not settings.FEATURE_FLAGS.get("GUIDANCE"):
            return False
        snapshot = self.snapshot
        if snapshot is None or snapshot.synced_at is None:
            return False
        groups = snapshot.site.get("vocabularies", {}).get("groups", {})
        return segment == ALL_ENTRIES_SLUG or segment in groups

    def serve(self, request, *args, **kwargs):
        if not settings.FEATURE_FLAGS.get("GUIDANCE"):
            raise Http404
        return super().serve(request, *args, **kwargs)

    @path("all/")
    def serve_all(self, request):
        return self._render_catalogue(
            request, "catalogue/all_page.html", catalogue_context.all_context
        )

    @re_path(r"^(?P<group>[\w-]+)/$")
    def serve_group(self, request, group):
        return self._render_catalogue(
            request, "catalogue/group_page.html", catalogue_context.group_context, group
        )

    @re_path(r"^(?P<group>[\w-]+)/(?P<entry_id>[\w-]+)/$")
    def serve_entry(self, request, group, entry_id):
        return self._serve_entry(request, group, entry_id, "")

    @re_path(r"^(?P<group>[\w-]+)/(?P<entry_id>[\w-]+)/(?P<doc_path>[\w-]+(?:/[\w-]+)*)/$")
    def serve_entry_page(self, request, group, entry_id, doc_path):
        return self._serve_entry(request, group, entry_id, doc_path)

    def _snapshot_or_404(self):
        if not settings.FEATURE_FLAGS.get("GUIDANCE"):
            raise Http404
        snapshot = self.snapshot
        if snapshot is None or snapshot.synced_at is None:
            raise Http404
        return snapshot

    def _render_catalogue(self, request, template, build, *args):
        snapshot = self._snapshot_or_404()
        # The page's own hero belongs to its own page: the catalogue's pages
        # carry their heading in the content, as a role page does.
        self.hero_style = HeroStyle.NONE
        context = build(self, request, snapshot, *args)
        response = self.render(request, template=template, context_overrides=context)
        if context.get("image_sources"):
            config = dict(settings.SECURE_CSP)
            config["img-src"] = [*config.get("img-src", []), *context["image_sources"]]
            response._csp_config = config
        return response

    def _serve_entry(self, request, group, entry_id, doc_path):
        snapshot = self._snapshot_or_404()
        entry = snapshot.entries.filter(entry_id=entry_id, has_pages=True).first()
        if entry is None:
            raise Http404
        if entry.group != group:
            return HttpResponsePermanentRedirect(
                catalogue_context.entry_url(self, request, entry, doc_path)
            )
        return self._render_catalogue(
            request,
            f"catalogue/layouts/{entry.layout}.html",
            catalogue_context.entry_context,
            entry,
            doc_path,
        )


class CatalogueSnapshot(models.Model):
    """The last good sync of a content main page's catalogue.

    ``site`` holds the catalogue as a whole: its title and descriptions, and
    the vocabularies (groups, types, scope, professions, tags) with their
    labels. ``last_error`` is the most recent failed sync; the previous good
    copy keeps serving until a sync succeeds.
    """

    page = models.OneToOneField(
        ContentMainPage, on_delete=models.CASCADE, related_name="catalogue_snapshot"
    )
    source_key = models.CharField(max_length=600, blank=True)
    commit_sha = models.CharField(max_length=40, blank=True)
    synced_at = models.DateTimeField(null=True, blank=True)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    warnings = models.JSONField(default=list, blank=True)
    site = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return f"{self.page} @ {self.commit_sha[:7] or 'unsynced'}"


class CatalogueEntry(models.Model):
    """One listed catalogue entry, as the catalogue describes it."""

    snapshot = models.ForeignKey(
        CatalogueSnapshot, on_delete=models.CASCADE, related_name="entries"
    )
    entry_id = models.CharField(max_length=60)
    group = models.CharField(max_length=60)
    type = models.CharField(max_length=60)
    layout = models.CharField(max_length=20)
    primary = models.BooleanField(default=False)
    status = models.CharField(max_length=20, default="current")
    title = models.CharField(max_length=200)
    short_description = models.CharField(max_length=300)
    long_description = models.TextField()
    scope = models.CharField(max_length=60)
    tags = models.JSONField(default=list, blank=True)
    professions = models.JSONField(default=list, blank=True)
    owner = models.JSONField(default=dict, blank=True)
    applies_to = models.TextField(blank=True)
    related = models.JSONField(default=list, blank=True)
    source = models.JSONField(default=dict, blank=True)
    version = models.CharField(max_length=40, blank=True)
    links = models.JSONField(default=list, blank=True)
    # Whether the entry is a page here, or its card links to external_url.
    has_pages = models.BooleanField(default=False)
    external_url = models.URLField(max_length=500, blank=True)
    # A hosted or repo entry's navigation, numbered sequence and banner.
    structure = models.JSONField(default=dict, blank=True)
    content_key = models.CharField(max_length=600, blank=True)
    content_commit = models.CharField(max_length=40, blank=True)
    content_error = models.TextField(blank=True)
    warnings = models.JSONField(default=list, blank=True)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position"]
        verbose_name_plural = "catalogue entries"
        constraints = [
            models.UniqueConstraint(
                fields=["snapshot", "entry_id"], name="unique_catalogue_entry_id"
            )
        ]

    def __str__(self):
        return self.title


class CatalogueDocument(models.Model):
    """One rendered page of a hosted or repo entry, at ``path`` below it."""

    entry = models.ForeignKey(
        CatalogueEntry, on_delete=models.CASCADE, related_name="documents"
    )
    path = models.CharField(max_length=500, blank=True)
    source_path = models.CharField(max_length=500)
    title = models.CharField(max_length=500)
    # A requirement's or step's place in its entry's numbered sequence.
    number = models.PositiveIntegerField(null=True, blank=True)
    markdown = models.TextField(blank=True)
    html = models.TextField(blank=True)
    # A policy's text before its first section, and its numbered sections.
    intro_html = models.TextField(blank=True)
    sections = models.JSONField(default=list, blank=True)
    headings = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["path"]
        constraints = [
            models.UniqueConstraint(
                fields=["entry", "path"], name="unique_catalogue_document_path"
            )
        ]

    def __str__(self):
        return self.title
