"""Template context for the catalogue's page types, built from the page tree.

Models are imported inside functions: the model module imports this one.
"""

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.utils import timezone
from django.utils.safestring import mark_safe
from django.utils.text import slugify

from .colours import DEFAULT_COLOUR, colour_class
from .filtering import filter_entries
from .render import _GithubAnchors

ALL_PAGE_SIZE = 24
# The listing's heading on a main page that lists all content itself.
ALL_CONTENT_ID = "all-content"
_IMG_SRC = re.compile(r'<img[^>]*\ssrc="([^"]+)"')


def vocabularies() -> dict:
    """The vocabulary snippets as key -> label dicts, in order."""
    from govuk.models import CatalogueProfession, CatalogueType, GovukTag

    return {
        "types": {
            t.key: {"label": t.label, "plural": t.heading, "layout": t.layout, "colour": t.colour}
            for t in CatalogueType.objects.all()
        },
        "professions": {p.key: {"label": p.label} for p in CatalogueProfession.objects.all()},
        "tags": {t.slug: {"label": t.name} for t in GovukTag.objects.order_by("name")},
    }


@dataclass
class EntryView:
    """An entry page flattened to the attributes ``filtering.filter_entries`` reads."""

    page: object
    title: str
    short_description: str
    long_description: str
    type: str
    tags: list[str]
    professions: list[str]
    owner: dict
    primary: bool
    withdrawn: bool
    url: str
    external: bool
    source_name: str = ""
    updated: dict | None = None
    position: int = 0
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_page(cls, page, request, position: int = 0, dates=None) -> "EntryView":
        """``dates`` is ``source_dates()`` for many pages at once; looked up when absent."""
        from govuk.models import ContentEntryExternalPage

        external = isinstance(page, ContentEntryExternalPage)
        if external:
            if dates is None:
                dates = source_dates([page.source_url])
            updated = dated(None, dates.get(page.source_url))
        else:
            updated = publication(page)
        return cls(
            page=page,
            title=page.title,
            short_description=page.short_description,
            long_description=page.long_description,
            type=page.entry_type.key,
            tags=[tag.slug for tag in page.topics.all()],
            professions=[profession.key for profession in page.professions.all()],
            owner={"organisation": page.owner_organisation},
            primary=page.primary,
            withdrawn=page.withdrawn,
            url=page.source_url if external else (page.get_url(request) or ""),
            external=external,
            source_name=page.source_name if external or page.source_url else "",
            updated=updated,
            position=position,
        )


def source_dates(urls) -> dict[str, datetime]:
    """When each source last changed, by its own account: only GOV.UK's Content API says."""
    from govuk.models import HarvestedSource

    return dict(
        HarvestedSource.objects.filter(url__in=[url for url in urls if url])
        .exclude(source_updated_at=None)
        .values_list("url", "source_updated_at")
    )


def dated(published: datetime | None, updated: datetime | None) -> dict | None:
    """A card's "Published"/"Last updated" line, by the day."""
    published_on = timezone.localdate(published) if published else None
    updated_on = timezone.localdate(updated) if updated else None
    if updated_on and (published_on is None or updated_on > published_on):
        return {"label": "Last updated", "on": updated_on}
    if published_on:
        return {"label": "Published", "on": published_on}
    return None


def publication(obj) -> dict | None:
    """``dated`` from Wagtail's publishing dates, for a page, role or skill on this site."""
    return dated(getattr(obj, "first_published_at", None), getattr(obj, "last_published_at", None))


def live_entries(queryset) -> list:
    """Live, public entry pages in ``queryset``, specific, in tree order."""
    from govuk.models import entry_page_models

    return list(
        queryset.live().public().type(*entry_page_models()).specific().order_by("path")
    )


def entry_views(pages, request) -> list[EntryView]:
    from govuk.models import ContentEntryExternalPage

    pages = list(pages)
    dates = source_dates(
        page.source_url for page in pages if isinstance(page, ContentEntryExternalPage)
    )
    return [
        EntryView.from_page(page, request, position, dates=dates)
        for position, page in enumerate(pages)
    ]


def card(view: EntryView, vocab: dict) -> dict:
    entry_type = vocab.get("types", {}).get(view.type, {})
    return {
        "title": view.title,
        "url": view.url,
        "external": view.external,
        "source_name": view.source_name,
        "description": view.short_description,
        "type": entry_type.get("label", view.type),
        "colour_class": colour_class(entry_type.get("colour", DEFAULT_COLOUR)),
        "updated": view.updated,
        "primary": view.primary,
        "withdrawn": view.withdrawn,
    }


def _primary_first(views):
    return sorted(views, key=lambda v: (not v.primary, v.title.lower()))


def _filter_form(entry_filter) -> dict:
    labels = {"tag": "Topic", "profession": "Profession", "type": "Content type"}
    return {
        "query": entry_filter.query,
        "active": entry_filter.active,
        "fields": [
            {"name": name, "label": labels[name], "options": entry_filter.options[name]}
            for name in ("tag", "profession", "type")
            if entry_filter.options.get(name)
        ],
    }


def wants_counts(request) -> bool:
    return request.GET.get("counts") == "1"


def counts_response(entries, request) -> JsonResponse:
    """The result count and each filter's option counts for the form as it stands.

    Fetched by ``catalogue.js`` as the reader changes the form, before they apply it.
    """
    vocab = vocabularies()
    visible, entry_filter = filter_entries(
        entry_views(entries, request), request.GET, vocab, keep_empty=True
    )
    return JsonResponse(
        {
            "count": len(visible),
            "fields": {
                name: [{key: o[key] for key in ("value", "label", "count")} for o in options]
                for name, options in entry_filter.options.items()
            },
        }
    )


def _base_url(page, request) -> str:
    url = page.get_url(request) or "/"
    return url if url.endswith("/") else f"{url}/"


def _common(main_page, request) -> dict:
    from govuk.models import ContentGroupPage

    groups = ContentGroupPage.objects.child_of(main_page).live().public().order_by("path")
    return {
        "catalogue_groups": [
            {"slug": group.slug, "label": group.title, "url": group.get_url(request)}
            for group in groups
        ],
        "catalogue_all_url": main_page.all_content_url(request),
        "catalogue_home_url": _base_url(main_page, request),
    }


def group_context(group_page, request) -> dict:
    vocab = vocabularies()
    views = entry_views(group_page.entries(), request)
    visible, entry_filter = filter_entries(views, request.GET, vocab)

    sections, results = [], []
    if entry_filter.query:
        results = [card(view, vocab) for view in visible]
    else:
        for key, entry_type in vocab["types"].items():
            items = _primary_first(view for view in visible if view.type == key)
            if items:
                sections.append(
                    {
                        "title": entry_type["plural"],
                        "slug": key,
                        "cards": [card(view, vocab) for view in items],
                    }
                )
    return {
        **_common(group_page.get_parent().specific, request),
        "page_heading": group_page.title,
        "catalogue_group": {
            "slug": group_page.slug,
            "label": group_page.title,
            "description": group_page.description,
        },
        "type_sections": sections,
        "result_cards": results,
        "result_count": len(visible),
        "filter_form": _filter_form(entry_filter),
    }


def all_listing_context(main_page, request) -> dict:
    """Every entry, searched, filtered and paged: on the "all" page, or at the foot of the main page."""
    vocab = vocabularies()
    views = entry_views(main_page.entries(), request)
    visible, entry_filter = filter_entries(views, request.GET, vocab)
    if not entry_filter.query:
        visible = _primary_first(visible)
    paginator = Paginator(visible, ALL_PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get("page"))
    query = request.GET.copy()
    query.pop("page", None)
    return {
        **_common(main_page, request),
        "result_cards": [card(view, vocab) for view in page_obj.object_list],
        "result_count": paginator.count,
        "page_obj": page_obj,
        "pagination_query": query.urlencode(),
        "filter_form": _filter_form(entry_filter),
        # Brings the reader back to the listing, not the top of the page, after Apply or a page link.
        "listing_fragment": f"#{ALL_CONTENT_ID}" if main_page.lists_all_here else "",
        "listing_id": ALL_CONTENT_ID,
    }


def all_context(main_page, request) -> dict:
    return {
        **all_listing_context(main_page, request),
        "page_heading": "All content",
        "breadcrumbs": _route_breadcrumbs(main_page, request, "All content"),
        "show_breadcrumbs": True,
    }


def _route_breadcrumbs(page, request, title: str) -> list[dict]:
    """Breadcrumbs to ``page``, then ``title`` as the current item."""
    site = page.get_site()
    root_path = site.root_page.path if site else ""
    crumbs = [
        {"title": ancestor.title, "url": ancestor.get_url(request), "is_current": False}
        for ancestor in page.get_ancestors(inclusive=True).specific()
        if root_path and ancestor.path.startswith(root_path)
    ]
    crumbs.append({"title": title, "url": None, "is_current": True})
    return crumbs


def render_blocks(stream, context: dict) -> tuple[str, list[dict]]:
    """A stream's HTML with anchors on its headings, and its h2/h3 outline."""
    html = "".join(child.render(context=context) for child in stream or [])
    return add_heading_anchors(html)


def add_heading_anchors(html: str) -> tuple[str, list[dict]]:
    """Give each heading an id (as GitHub would), and list the h2s and h3s."""
    soup = BeautifulSoup(html, "html.parser")
    anchors = _GithubAnchors()
    headings = []
    for tag in soup.find_all(["h2", "h3", "h4"]):
        text = tag.get_text(" ", strip=True)
        if not tag.get("id"):
            tag["id"] = anchors.create(text, int(tag.name[1])) or f"section-{len(headings) + 1}"
        if tag.name in ("h2", "h3"):
            headings.append({"level": int(tag.name[1]), "text": text, "anchor": tag["id"]})
    return str(soup), headings


def _contents(headings: list[dict]) -> list[dict]:
    """h2s, each with the h3s under it, as ``role_page.html``'s contents list."""
    contents = []
    for heading in headings:
        item = {"text": heading["text"], "anchor": heading["anchor"], "children": []}
        if heading.get("level") == 3 and contents:
            contents[-1]["children"].append(item)
        elif heading.get("level") == 2:
            contents.append(item)
    return contents


def image_sources(html: str) -> list[str]:
    """The https origins a page's images load from, for its content security policy."""
    origins = set()
    for src in _IMG_SRC.findall(html):
        parts = urlsplit(src)
        if parts.scheme == "https" and parts.netloc:
            origins.add(f"https://{parts.netloc}")
    return sorted(origins)


def with_image_policy(response):
    """Allow a page's images' origins in its content security policy, and no others."""
    from django.conf import settings

    sources = (getattr(response, "context_data", None) or {}).get("image_sources")
    if sources:
        config = dict(settings.SECURE_CSP)
        config["img-src"] = [*config.get("img-src", []), *sources]
        response._csp_config = config
    return response


def entry_context(entry, request, *, document) -> dict:
    """An entry's front page (``document`` is the entry) or a page within it."""
    from govuk.models import CatalogueLayout
    from govuk.models.catalogue.vocabulary import GENERIC_LAYOUT

    vocab = vocabularies()
    is_front = document.pk == entry.pk
    render_context = {"page": document, "self": document, "request": request}
    layout = entry.layout

    context = {
        "page_heading": document.title,
        "is_entry_front": is_front,
        "entry": _entry_details(entry, request, vocab),
        "document": document,
        "review": _review(entry),
    }
    html_parts = []

    if layout == CatalogueLayout.REQUIREMENTS and is_front:
        intro, intro_headings = render_blocks(entry.body, render_context)
        closing, closing_headings = render_blocks(entry.closing_body, render_context)
        context["requirements_intro"] = mark_safe(intro)
        context["requirements_rest"] = mark_safe(closing)
        html_parts += [intro, closing]
    elif layout == CatalogueLayout.POLICY:
        intro, _ = render_blocks(entry.body, render_context)
        context["policy_intro"] = mark_safe(intro)
        sections = []
        for number, block in enumerate(entry.sections or [], start=1):
            html, _ = render_blocks(block.value["body"], render_context)
            html_parts.append(html)
            sections.append(
                {
                    "number": number,
                    "title": block.value["title"],
                    "anchor": slugify(block.value["title"]) or f"section-{number}",
                    "html": mark_safe(html),
                }
            )
        context["policy_sections"] = sections
        html_parts.append(intro)
    elif layout == GENERIC_LAYOUT:
        # The template lays this out; rendered here only to find image origins.
        html, _ = render_blocks(entry.body, render_context)
        html_parts.append(html)
    elif layout != CatalogueLayout.TOOL or not is_front:
        html, headings = render_blocks(getattr(document, "body", None), render_context)
        context["document_html"] = mark_safe(html)
        context["contents"] = _contents(headings)
        html_parts.append(html)

    context["caption"] = entry.title if not is_front else ""
    if layout in (CatalogueLayout.REQUIREMENTS, CatalogueLayout.POLICY) and is_front:
        context["caption"] = context["entry"]["type"]
    if layout in (CatalogueLayout.REQUIREMENTS, CatalogueLayout.STEPS):
        context.update(_sequence_context(entry, document, request))
        if layout == CatalogueLayout.STEPS and context.get("sequence_number"):
            context["caption"] = (
                f"Step {context['sequence_number']} of {context['sequence_total']}"
            )
    if layout in (CatalogueLayout.GUIDE, CatalogueLayout.STEPS):
        context["entry_navigation"] = navigation(entry, document, request)
    context["masthead_facts"] = _masthead_facts(layout, context["review"])
    context["image_sources"] = image_sources("".join(html_parts))
    return context


def _entry_details(entry, request, vocab: dict) -> dict:
    from govuk.models import ContentEntryToolPage

    related_pages = [
        block.value.specific
        for block in entry.related_entries or []
        if block.value is not None and block.value.live
    ]
    contact_url = (
        f"mailto:{entry.contact_email}" if entry.contact_email else entry.contact_url
    )
    return {
        "id": entry.slug,
        "url": entry.get_url(request),
        "title": entry.title,
        "long_description": entry.long_description,
        "type": vocab["types"].get(entry.entry_type.key, {}).get("label", entry.entry_type.label),
        "scope": entry.scope.label,
        "tags": [tag.name for tag in entry.topics.all()],
        "professions": [profession.label for profession in entry.professions.all()],
        "owner": entry.owner_organisation,
        "team": entry.owner_team,
        "contact_url": contact_url,
        "contact_text": entry.contact_email or entry.contact_slack or "",
        "applies_to": entry.applies_to,
        "version": entry.version,
        "links": [block.value for block in entry.links] if isinstance(entry, ContentEntryToolPage) else [],
        "withdrawn": entry.withdrawn,
        "source_name": entry.source_name,
        "source_url": entry.source_url,
        "related": [card(view, vocab) for view in entry_views(related_pages, request)],
    }


def _review(entry) -> dict:
    next_review_on = entry.next_review_on
    return {
        "version": entry.version,
        "effective_from": entry.effective_from,
        "last_reviewed_on": entry.last_reviewed_on,
        "next_review_on": next_review_on,
        "overdue": bool(next_review_on and next_review_on < date.today()),
        "owner": entry.owner_organisation,
    }


def _masthead_facts(layout: str, review: dict) -> list[dict]:
    """Key facts for the title band; a policy shows all four, so readers see what is unstated."""
    def day(value):
        return f"{value.day} {value:%B %Y}" if value else ""

    facts = [
        ("Version", review.get("version", "")),
        ("Effective from", day(review.get("effective_from"))),
        ("Last reviewed", day(review.get("last_reviewed_on"))),
        ("Next review", day(review.get("next_review_on"))),
    ]
    if layout == "policy":
        return [{"label": label, "value": value or "Not stated"} for label, value in facts]
    if layout == "requirements":
        return [{"label": label, "value": value} for label, value in facts if value]
    return []


def _sequence_context(entry, document, request) -> dict:
    """A standard's points or a method's steps, numbered in tree order."""
    from govuk.models import ContentRequirementPage, ContentStepPage

    model = ContentRequirementPage if entry.layout == "requirements" else ContentStepPage
    pages = list(model.objects.child_of(entry).live().public().order_by("path"))
    items = [
        {
            "number": number,
            "title": page.title,
            "summary": page.summary,
            "url": page.get_url(request),
            "is_current": page.pk == document.pk,
        }
        for number, page in enumerate(pages, start=1)
    ]
    context = {"sequence": items, "sequence_total": len(items)}
    for position, item in enumerate(items):
        if item["is_current"]:
            context["sequence_number"] = item["number"]
            context["sequence_previous"] = items[position - 1] if position > 0 else None
            context["sequence_next"] = items[position + 1] if position + 1 < len(items) else None
    return context


def navigation(entry, document, request) -> list[dict]:
    """A guide's pages, or a method's reference pages, as one side-navigation group."""
    from govuk.models import ContentGuidePage

    pages = list(
        ContentGuidePage.objects.descendant_of(entry).live().public().order_by("path")
    )
    by_parent: dict[str, list] = {}
    for page in pages:
        by_parent.setdefault(page.path[: -page.steplen], []).append(page)

    def items(parent_path: str) -> list[dict]:
        return [
            {
                "title": page.title,
                "url": page.get_url(request),
                "path": page.slug,
                "is_current": page.pk == document.pk,
                "is_open": document.path.startswith(page.path),
                "children": items(page.path),
            }
            for page in by_parent.get(parent_path, [])
        ]

    top = items(entry.path)
    if not top:
        return []
    title = "Reference" if entry.layout == "steps" else "Pages"
    return [{"title": title, "items": top}]
