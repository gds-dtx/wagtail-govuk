"""Guidance-as-code: a page that serves a GitHub repo of guidance.

An editor adds a ``GuidanceCollectionPage`` and points it at a repo (or a
directory in one). Everything else -- the pages, their titles and order, the
side navigation, review dates and owners -- comes from the repo, read either
from its ``guidance.yml`` manifest or, for a tech-docs repo such as The GDS
Way, from its existing layout (``govuk/guidance/adapters.py``).

The repo is synced into a ``GuidanceSnapshot`` and its ``GuidanceDocument``
rows, rather than fetched when a page is requested: pages serve from the
database at the speed of any other page and keep serving if GitHub is
unreachable. The snapshot is a model of its own, not fields on the page, so
that publishing an old revision of the page cannot bring back an old copy of
the guidance.

Gated by ``FEATURE_FLAGS["GUIDANCE"]``.
"""

from collections import deque
from datetime import date

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.http import Http404, HttpResponsePermanentRedirect
from django.urls import reverse
from django.utils.safestring import mark_safe
from wagtail.admin.panels import FieldPanel, MultiFieldPanel, Panel
from wagtail.contrib.routable_page.models import RoutablePageMixin, path, re_path
from wagtail.models import Page

from govuk.guidance.bundle import clean_path
from govuk.guidance.render import INTERNAL_LINK_PREFIX
from govuk.guidance.repository import (
    RepositoryRef,
    parse_repository_url,
    validate_branch,
)

from .panels import page_settings_panels

RAW_CONTENT_HOST = "https://raw.githubusercontent.com"


def without_guidance_pages(queryset):
    """``queryset`` minus guidance collection pages, on a site without them.

    The ``without_framework_pages`` counterpart, applied at the same public
    listings (the pages API, search, the service navigation), for the same
    reason: a page that 404s should not be offered as a link.
    """
    if settings.FEATURE_FLAGS.get("GUIDANCE"):
        return queryset
    return queryset.not_type(GuidanceCollectionPage)


class GuidanceSyncStatusPanel(Panel):
    """Read-only sync state on the page's edit view, with a link to sync it."""

    class BoundPanel(Panel.BoundPanel):
        template_name = "wagtailadmin/guidance/sync_status_panel.html"

        def get_context_data(self, parent_context=None):
            context = super().get_context_data(parent_context)
            page = self.instance
            snapshot = page.snapshot if page and page.pk else None
            context.update(
                {
                    "page": page,
                    "snapshot": snapshot,
                    "document_count": snapshot.documents.count() if snapshot else 0,
                    "commit_url": page.repository().commit_url() if snapshot else "",
                    "sync_url": (
                        reverse("govuk_guidance_sync", args=[page.pk]) if page.pk else ""
                    ),
                }
            )
            return context


class GuidanceCollectionPage(RoutablePageMixin, Page):
    """Serves a repo of guidance: its index here, each page beneath it.

    Every path under this page is one of the guidance's pages, so it can have
    no child pages of its own: the catch-all document route would shadow them.
    """

    repository_url = models.URLField(
        max_length=300,
        verbose_name="Repository",
        help_text="The GitHub repository the guidance lives in, like https://github.com/alphagov/gds-way",
    )
    branch = models.CharField(max_length=200, default="main")
    subdirectory = models.CharField(
        max_length=300,
        blank=True,
        help_text=(
            "Optional. The directory inside the repository that holds this "
            "guidance, for a repository that holds more than one."
        ),
    )

    parent_page_types = [
        "wagtailcore.Page",
        "govuk.ContentPage",
        "govuk.SectionPage",
        "govuk.TagListingsPage",
        "govuk.FrameworkMainPage",
    ]
    subpage_types = []

    template = "govuk/guidance_page.html"

    content_panels = Page.content_panels + [
        MultiFieldPanel(
            [
                FieldPanel("repository_url"),
                FieldPanel("branch"),
                FieldPanel("subdirectory"),
            ],
            heading="Source",
            help_text=(
                "Everything else -- the pages, their order and the side "
                "navigation -- comes from the repository. Publishing syncs it "
                "the first time and whenever these settings change."
            ),
        ),
        GuidanceSyncStatusPanel(heading="Sync status"),
    ]
    settings_panels = page_settings_panels()

    class Meta:
        verbose_name = "guidance collection page"

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
        errors = {}
        try:
            parse_repository_url(self.repository_url)
        except ValueError as exc:
            errors["repository_url"] = str(exc)
        try:
            self.branch = validate_branch(self.branch)
        except ValueError as exc:
            errors["branch"] = str(exc)
        self.subdirectory = (self.subdirectory or "").strip().strip("/")
        if ".." in self.subdirectory.split("/"):
            errors["subdirectory"] = "Enter a directory inside the repository."
        if errors:
            raise ValidationError(errors)

    # -- source and snapshot -------------------------------------------------

    @property
    def snapshot(self):
        try:
            return self.guidance_snapshot
        except GuidanceSnapshot.DoesNotExist:
            return None

    def repository(self) -> RepositoryRef:
        snapshot = self.snapshot
        ref = RepositoryRef.from_page(self)
        return ref.at(snapshot.commit_sha) if snapshot and snapshot.commit_sha else ref

    def needs_sync(self) -> bool:
        """Never synced, or synced from source settings that have since changed."""
        snapshot = self.snapshot
        if snapshot is None or snapshot.synced_at is None:
            return True
        try:
            return snapshot.source_key != RepositoryRef.from_page(self).source_key
        except ValueError:
            return False

    # -- serving --------------------------------------------------------------

    def serve(self, request, *args, **kwargs):
        if not settings.FEATURE_FLAGS.get("GUIDANCE"):
            raise Http404
        return super().serve(request, *args, **kwargs)

    @path("")
    def index_route(self, request, *args, **kwargs):
        return self._serve_document(request, "")

    @re_path(r"^(?P<doc_path>[\w-]+(?:/[\w-]+)*)/$")
    def serve_document(self, request, doc_path):
        """One page of the guidance, or a redirect to where it now lives.

        A path that names a page another way (``x/index``) is redirected to
        the page's own address, and so is one the source's own config
        redirects. The source's ``.html`` addresses never reach here --
        Wagtail's URL pattern has no dots -- so ``guidance_legacy_url_view``
        sends those on.
        """
        return self._serve_document(request, doc_path)

    def _serve_document(self, request, doc_path: str):
        if not settings.FEATURE_FLAGS.get("GUIDANCE"):
            raise Http404
        snapshot = self.snapshot
        if snapshot is None or snapshot.synced_at is None:
            if doc_path:
                raise Http404
            return self.render(request, context_overrides={"guidance_unsynced": True})

        path = clean_path(doc_path)
        if path != doc_path:
            target = {"path": path, "fragment": ""}
        else:
            document = snapshot.documents.filter(path=path).first()
            if document is not None:
                return self._render_document(request, snapshot, document)
            target = snapshot.site.get("redirects", {}).get(path)
        if not target or not snapshot.documents.filter(path=target["path"]).exists():
            raise Http404
        return HttpResponsePermanentRedirect(self.document_url(request, **target))

    def legacy_redirect(self, request, remainder: str):
        """A redirect for one of the source site's own addresses, or None.

        ``remainder`` is the request path below this page, such as
        ``standards/alerting.html``.
        """
        snapshot = self.snapshot
        if snapshot is None or snapshot.synced_at is None:
            return None
        path = clean_path(remainder)
        if snapshot.documents.filter(path=path).exists():
            return HttpResponsePermanentRedirect(self.document_url(request, path))
        target = snapshot.site.get("redirects", {}).get(path)
        if target and snapshot.documents.filter(path=target["path"]).exists():
            return HttpResponsePermanentRedirect(self.document_url(request, **target))
        return None

    def base_url(self, request=None) -> str:
        url = self.get_url(request) or "/"
        return url if url.endswith("/") else f"{url}/"

    def document_url(self, request, path: str, fragment: str = "") -> str:
        url = self.base_url(request) + (f"{path}/" if path else "")
        return f"{url}#{fragment}" if fragment else url

    def expand_links(self, html: str, request=None, *, base: str = "") -> str:
        """The stored HTML with its ``guidance:/`` links pointed at this page.

        ``base`` overrides the page's own URL, for the API's absolute links.
        """
        base = base or self.base_url(request)
        return html.replace(f'href="{INTERNAL_LINK_PREFIX}', f'href="{base}')

    def _render_document(self, request, snapshot, document):
        response = self.render(
            request,
            template=self.template,
            context_overrides=self.document_context(request, snapshot, document),
        )
        if "<img" in document.html:
            # Images are served from the repo itself. Only these pages widen
            # the CSP for them, and only to GitHub's raw content host.
            config = dict(settings.SECURE_CSP)
            config["img-src"] = [*config.get("img-src", []), RAW_CONTENT_HOST]
            response._csp_config = config
        return response

    def document_context(self, request, snapshot, document) -> dict:
        site = snapshot.site or {}
        repository = self.repository()
        is_index = document.path == ""
        metadata = document.metadata or {}
        navigation, ancestors = self.navigation_tree(request, snapshot, document.path)
        return {
            "page_heading": document.title,
            "guidance_site": site,
            "guidance_document": document,
            "guidance_is_index": is_index,
            "guidance_withdrawn": metadata.get("status") == "withdrawn",
            "guidance_html": mark_safe(self.expand_links(document.html, request)),
            "guidance_banner": mark_safe(
                self.expand_links(site.get("banner_html", ""), request)
            ),
            "guidance_contents": _contents(document.headings or []),
            "guidance_review": _review(metadata, site),
            "guidance_source": {
                "view_url": repository.blob_url(document.source_path),
                "report_url": repository.new_issue_url(f"Feedback on: {document.title}"),
                "repository_url": repository.html_url,
                "commit": snapshot.commit_sha,
                "commit_url": repository.commit_url(),
                "synced_at": snapshot.synced_at,
            },
            # The guidance's own name heads its navigation: "Home" would be
            # ambiguous on a site with more than one set of guidance, or with
            # other content around it.
            "guidance_home": {
                "title": self.title,
                "url": self.base_url(request),
                "is_current": is_index,
            },
            "guidance_navigation": navigation,
            # Shown at every width, not only where the sidebar is hidden: the
            # sidebar shows only the open branch, so the trail is how a reader
            # sees, and climbs, where a nested page sits.
            "breadcrumbs": self._breadcrumbs(request, document, ancestors),
        }

    def navigation_tree(self, request, snapshot, current_path=None):
        """The side navigation for ``current_path``, and that page's ancestors in it.

        Returns ``(groups, ancestors)``. Each group is ``{"title", "items"}`` and
        each item ``{"title", "url", "is_current", "is_open", "children"}``:
        the sidebar shows an item's children only while it ``is_open``, which is
        while the current page is that item or somewhere beneath it. A page
        listed more than once is current at its shallowest, earliest listing.

        A section that could not be placed in the navigation (its page is not
        listed there) is shown above it while reading that section, as a group
        of its own. ``current_path=None`` gives the whole tree, for the API.
        """
        site = snapshot.site or {}
        groups = []
        if current_path is not None:
            for section in site.get("sections", []):
                paths = {item.get("path") for item in _walk_items(section.get("items", []))}
                if current_path == section.get("owner_path") or current_path in paths:
                    groups.append({"title": section.get("title", ""), "items": section["items"]})
        groups.extend(site.get("navigation", []))

        route = _find_route(groups, current_path) if current_path else None
        built = [
            {
                "title": group.get("title") or self.title,
                "items": self._navigation_items(request, group.get("items", []), (index,), route),
            }
            for index, group in enumerate(groups)
        ]
        ancestors = []
        if route:
            items = groups[route[0]].get("items", [])
            for position in route[1:-1]:
                ancestors.append(items[position])
                items = items[position].get("children", [])
        return [group for group in built if group["items"]], ancestors

    def _navigation_items(self, request, items, prefix, route) -> list[dict]:
        built = []
        for position, item in enumerate(items):
            here = (*prefix, position)
            built.append(
                {
                    "title": item.get("title", ""),
                    "url": item.get("url")
                    or self.document_url(request, item.get("path") or "", item.get("fragment", "")),
                    "path": item.get("path"),
                    "is_current": route == here,
                    "is_open": bool(route) and route[: len(here)] == here,
                    "children": self._navigation_items(
                        request, item.get("children", []), here, route
                    ),
                }
            )
        return built

    def _breadcrumbs(self, request, document, ancestors=()) -> list[dict]:
        site = self.get_site()
        root_path = site.root_page.path if site else ""
        trail = [
            {"title": ancestor.title, "url": ancestor.get_url(request), "is_current": False}
            for ancestor in self.get_ancestors().specific()
            if root_path and ancestor.path.startswith(root_path)
        ]
        if document.path == "":
            trail.append({"title": self.title, "url": None, "is_current": True})
            return trail if len(trail) > 1 else []
        trail.append({"title": self.title, "url": self.base_url(request), "is_current": False})
        trail.extend(
            {
                "title": item.get("title", ""),
                "url": self.document_url(request, item["path"]),
                "is_current": False,
            }
            for item in ancestors
            if item.get("path") is not None
        )
        trail.append({"title": document.title, "url": None, "is_current": True})
        return trail


def _walk_items(items):
    for item in items:
        yield item
        yield from _walk_items(item.get("children", []))


def _find_route(groups: list[dict], path: str) -> tuple | None:
    """Where ``path`` sits in the navigation: ``(group, item, child, ...)``.

    Breadth-first, so a page listed both at the top of a group and inside
    another page's section is found at the top.
    """
    queue = deque(
        ((group_index, item_index), item)
        for group_index, group in enumerate(groups)
        for item_index, item in enumerate(group.get("items", []))
    )
    while queue:
        route, item = queue.popleft()
        if item.get("path") == path and not item.get("fragment") and not item.get("url"):
            return route
        queue.extend(
            ((*route, child_index), child)
            for child_index, child in enumerate(item.get("children", []))
        )
    return None


def _contents(headings: list[dict]) -> list[dict]:
    """h2s, each with the h3s under it, as ``role_page.html``'s contents list."""
    contents = []
    for heading in headings:
        entry = {"text": heading["text"], "anchor": heading["anchor"], "children": []}
        if heading.get("level") == 3 and contents:
            contents[-1]["children"].append(entry)
        elif heading.get("level") == 2:
            contents.append(entry)
    return contents


def _review(metadata: dict, site: dict) -> dict:
    """Last reviewed, next review and owner, as the source's own site shows them."""
    owner = metadata.get("owner") or ""
    workspace = (site.get("owner") or {}).get("slack_workspace") or ""
    owner_url = ""
    if owner.startswith("#") and workspace:
        owner_url = f"https://{workspace}.slack.com/messages/{owner.lstrip('#')}"
    elif owner.startswith(("https://", "http://")):
        owner_url = owner
    elif "@" in owner and " " not in owner:
        owner_url = f"mailto:{owner}"

    def as_date(value):
        try:
            return date.fromisoformat(value) if value else None
        except ValueError:
            return None

    next_review_on = as_date(metadata.get("next_review_on"))
    return {
        "last_reviewed_on": as_date(metadata.get("last_reviewed_on")),
        "next_review_on": next_review_on,
        "overdue": bool(next_review_on and next_review_on < date.today()),
        "owner": owner,
        "owner_url": owner_url,
    }


class GuidanceSnapshot(models.Model):
    """The last good sync of a guidance collection page's repo.

    ``site`` holds what the adapter and renderer made of the repo as a whole:
    its title and description, banner, navigation, section sidebars and
    redirects. ``last_error`` is the most recent failed sync; the previous good
    copy keeps serving until a sync succeeds.
    """

    page = models.OneToOneField(
        GuidanceCollectionPage,
        on_delete=models.CASCADE,
        related_name="guidance_snapshot",
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


class GuidanceDocument(models.Model):
    """One rendered page of guidance, at ``path`` below its collection page."""

    snapshot = models.ForeignKey(
        GuidanceSnapshot, on_delete=models.CASCADE, related_name="documents"
    )
    path = models.CharField(max_length=500, blank=True)
    source_path = models.CharField(max_length=500)
    title = models.CharField(max_length=500)
    markdown = models.TextField(blank=True)
    html = models.TextField(blank=True)
    headings = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["path"]
        constraints = [
            models.UniqueConstraint(
                fields=["snapshot", "path"], name="unique_guidance_document_path"
            )
        ]

    def __str__(self):
        return self.title
