"""What the catalogue's templates are given, for each kind of page.

The routes on ``ContentMainPage`` call these with the page, the request and the
synced snapshot. Nothing here imports the models: it reads the rows it is
handed, so the model module can import it.
"""

import re
from collections import deque
from datetime import date
from urllib.parse import urlsplit

from django.core.paginator import Paginator
from django.http import Http404
from django.utils.safestring import mark_safe

from .filtering import filter_entries
from .render import INTERNAL_LINK_PREFIX
from .repository import RepositoryRef

_IMG_SRC = re.compile(r'<img[^>]*\ssrc="([^"]+)"')
ALL_PAGE_SIZE = 24


# -- addresses -----------------------------------------------------------------


def base_url(page, request) -> str:
    url = page.get_url(request) or "/"
    return url if url.endswith("/") else f"{url}/"


def group_url(page, request, group: str) -> str:
    return f"{base_url(page, request)}{group}/"


def all_url(page, request) -> str:
    return f"{base_url(page, request)}all/"


def entry_url(page, request, entry, doc_path: str = "", fragment: str = "") -> str:
    url = f"{group_url(page, request, entry.group)}{entry.entry_id}/"
    if doc_path:
        url += f"{doc_path}/"
    return f"{url}#{fragment}" if fragment else url


def expand_links(html: str, base: str) -> str:
    """Stored HTML with its ``guidance:/`` links pointed at the entry's address."""
    return html.replace(f'href="{INTERNAL_LINK_PREFIX}', f'href="{base}')


def image_sources(html: str) -> list[str]:
    """The https origins a page's images load from, for its content security policy.

    The site's policy allows images from itself only. An entry's images come
    from its repo (raw.githubusercontent.com) or wherever its source links
    them (GOV.UK's assets), so only a page with images widens it, and only to
    those origins.
    """
    origins = set()
    for src in _IMG_SRC.findall(html):
        parts = urlsplit(src)
        if parts.scheme == "https" and parts.netloc:
            origins.add(f"https://{parts.netloc}")
    return sorted(origins)


def entry_repository(page, snapshot, entry) -> RepositoryRef | None:
    """Where an entry's content is, at the commit it was synced from."""
    source = entry.source or {}
    try:
        if source.get("type") == "hosted":
            ref = RepositoryRef.parse(
                page.catalogue_repository,
                branch=page.catalogue_branch,
                subdirectory=source.get("path", ""),
            )
            return ref.at(snapshot.commit_sha) if snapshot.commit_sha else ref
        if source.get("type") == "repo":
            ref = RepositoryRef.parse(
                source.get("path", ""),
                branch=source.get("branch", ""),
                subdirectory=source.get("directory", ""),
                pin=source.get("ref", ""),
            )
            return ref.at(entry.content_commit) if entry.content_commit else ref
    except ValueError:
        return None
    return None


# -- shared pieces -----------------------------------------------------------------


def vocabularies(snapshot) -> dict:
    return (snapshot.site or {}).get("vocabularies", {})


def _label(vocabulary: dict, key: str) -> str:
    return vocabulary.get(key, {}).get("label", key)


def card(page, request, entry, vocab: dict) -> dict:
    """One entry as a card: its own page, or straight to where it is published."""
    types = vocab.get("types", {})
    return {
        "title": entry.title,
        "url": entry_url(page, request, entry) if entry.has_pages else entry.external_url,
        "external": not entry.has_pages,
        # Content held in the catalogue itself is this site's, so it names no source.
        "source_name": ""
        if (entry.source or {}).get("type") == "hosted"
        else (entry.source or {}).get("name", ""),
        "description": entry.short_description,
        "type": _label(types, entry.type),
        "scope": _label(vocab.get("scope", {}), entry.scope),
        "tags": [_label(vocab.get("tags", {}), tag) for tag in entry.tags[:3]],
        "primary": entry.primary,
        "withdrawn": entry.status == "withdrawn",
    }


def _breadcrumbs(page, request, trail: list[tuple[str, str | None]]) -> list[dict]:
    """The site's ancestors of the page, the page, then ``trail`` (title, url)."""
    site = page.get_site()
    root_path = site.root_page.path if site else ""
    crumbs = [
        {"title": ancestor.title, "url": ancestor.get_url(request), "is_current": False}
        for ancestor in page.get_ancestors().specific()
        if root_path and ancestor.path.startswith(root_path)
    ]
    crumbs.append({"title": page.title, "url": base_url(page, request), "is_current": False})
    for title, url in trail:
        crumbs.append({"title": title, "url": url, "is_current": url is None})
    return crumbs


def _common(page, request, snapshot) -> dict:
    vocab = vocabularies(snapshot)
    return {
        "catalogue_groups": [
            {"slug": key, "label": group["label"], "url": group_url(page, request, key)}
            for key, group in vocab.get("groups", {}).items()
        ],
        "catalogue_all_url": all_url(page, request),
        "catalogue_home_url": base_url(page, request),
        # The catalogue's own pages always carry the trail: the page's own
        # switch is for its own page.
        "show_breadcrumbs": True,
    }


def _filter_form(entry_filter, vocab: dict, *, exclude=()) -> dict:
    labels = {"tag": "Topic", "profession": "Profession", "type": "Type", "scope": "Scope"}
    return {
        "query": entry_filter.query,
        "active": entry_filter.active,
        "fields": [
            {"name": name, "label": labels[name], "options": entry_filter.options[name]}
            for name in ("tag", "profession", "type", "scope")
            if name not in exclude and entry_filter.options.get(name)
        ],
    }


def _primary_first(entries):
    return sorted(entries, key=lambda e: (not e.primary, e.title.lower()))


# -- group and "all" pages ------------------------------------------------------------


def group_context(page, request, snapshot, group: str) -> dict:
    vocab = vocabularies(snapshot)
    groups = vocab.get("groups", {})
    if group not in groups:
        raise Http404
    entries = list(snapshot.entries.filter(group=group))
    visible, entry_filter = filter_entries(entries, request.GET, vocab)

    sections, results = [], []
    if entry_filter.query:
        # A search reads best as one list, best match first.
        results = [card(page, request, e, vocab) for e in visible]
    else:
        # A section for each of the group's types; in each, its primary
        # entries first, then the rest, each by title.
        for key, entry_type in vocab.get("types", {}).items():
            if entry_type.get("group") != group:
                continue
            items = _primary_first(e for e in visible if e.type == key)
            if items:
                sections.append(
                    {
                        "title": entry_type.get("plural") or entry_type["label"],
                        "slug": key,
                        "cards": [card(page, request, e, vocab) for e in items],
                    }
                )
    return {
        **_common(page, request, snapshot),
        "page_heading": groups[group]["label"],
        "catalogue_group": {"slug": group, **groups[group]},
        "type_sections": sections,
        "result_cards": results,
        "result_count": len(visible),
        "filter_form": _filter_form(entry_filter, vocab),
        "breadcrumbs": _breadcrumbs(page, request, [(groups[group]["label"], None)]),
    }


def all_context(page, request, snapshot) -> dict:
    vocab = vocabularies(snapshot)
    entries = list(snapshot.entries.all())
    visible, entry_filter = filter_entries(entries, request.GET, vocab)
    if not entry_filter.query:
        visible = _primary_first(visible)
    paginator = Paginator(visible, ALL_PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get("page"))
    query = request.GET.copy()
    query.pop("page", None)
    return {
        **_common(page, request, snapshot),
        "page_heading": "All content",
        "result_cards": [card(page, request, e, vocab) for e in page_obj.object_list],
        "result_count": paginator.count,
        "page_obj": page_obj,
        "pagination_query": query.urlencode(),
        "filter_form": _filter_form(entry_filter, vocab),
        "breadcrumbs": _breadcrumbs(page, request, [("All content", None)]),
    }


# -- entry pages --------------------------------------------------------------------


def entry_context(page, request, snapshot, entry, doc_path: str) -> dict:
    vocab = vocabularies(snapshot)
    documents = {document.path: document for document in entry.documents.all()}
    if entry.layout == "tool":
        if doc_path:
            raise Http404
        document = None
    else:
        document = documents.get(doc_path)
        if document is None:
            raise Http404

    base = entry_url(page, request, entry)
    group = vocab.get("groups", {}).get(entry.group, {"label": entry.group})
    trail = [(group["label"], group_url(page, request, entry.group))]
    if doc_path:
        trail.append((entry.title, base))
    context = {
        **_common(page, request, snapshot),
        "page_heading": document.title if document and doc_path else entry.title,
        "is_entry_front": not doc_path,
        "entry": _entry_details(page, request, snapshot, entry, vocab),
        "document": document,
        "document_html": mark_safe(expand_links(document.html, base)) if document else "",
        "contents": _contents(document.headings or []) if document else [],
        "review": _review(document.metadata if document else {}, entry),
        "image_sources": image_sources(document.html) if document else [],
    }

    structure = entry.structure or {}
    # Above a page's heading: the entry it belongs to on its other pages, and on
    # a standard's or policy's front page (in its title band) what kind of
    # document it is. A step says where it is in the method.
    context["caption"] = entry.title if doc_path else ""
    if entry.layout in ("requirements", "policy") and not doc_path:
        context["caption"] = context["entry"]["type"]
    if entry.layout in ("requirements", "steps"):
        context.update(_sequence_context(page, request, entry, documents, structure, doc_path))
        if entry.layout == "steps" and context.get("sequence_number"):
            context["caption"] = (
                f"Step {context['sequence_number']} of {context['sequence_total']}"
            )
    context["masthead_facts"] = _masthead_facts(entry.layout, context["review"])
    if entry.layout == "requirements" and document is not None and not doc_path:
        # The standard's own introduction, then its points, then the rest.
        html = expand_links(document.html, base)
        split = html.find("<h2")
        split = len(html) if split < 0 else split
        context["requirements_intro"] = mark_safe(html[:split])
        context["requirements_rest"] = mark_safe(html[split:])
    if entry.layout == "policy" and document is not None:
        context["policy_intro"] = mark_safe(expand_links(document.intro_html, base))
        context["policy_sections"] = [
            {**section, "html": mark_safe(expand_links(section["html"], base))}
            for section in document.sections
        ]
    if entry.layout in ("guide", "steps"):
        navigation, ancestors = navigation_tree(page, request, entry, structure, doc_path)
        context["entry_navigation"] = navigation
        for item in ancestors:
            if item.get("path") is not None:
                trail.append((item.get("title", ""), entry_url(page, request, entry, item["path"])))
    if structure.get("banner_html") and not doc_path:
        context["entry_banner"] = mark_safe(expand_links(structure["banner_html"], base))
    trail.append((context["page_heading"], None))
    context["breadcrumbs"] = _breadcrumbs(page, request, trail)
    return context


def _entry_details(page, request, snapshot, entry, vocab: dict) -> dict:
    owner = entry.owner or {}
    contact = owner.get("contact") or {}
    contact_url = (
        f"mailto:{contact['email']}" if contact.get("email") else contact.get("url", "")
    )
    repository = entry_repository(page, snapshot, entry)
    related = [
        card(page, request, other, vocab)
        for other in snapshot.entries.filter(entry_id__in=entry.related)
    ]
    try:
        catalogue = RepositoryRef.parse(page.catalogue_repository, branch=page.catalogue_branch)
    except ValueError:
        catalogue = None
    entries_dir = (snapshot.site or {}).get("entries_dir", "entries")
    # Problems with the content go to wherever it is kept; a change to what
    # the catalogue says about it, to the catalogue.
    issues = repository or catalogue
    return {
        "row": entry,
        "id": entry.entry_id,
        "url": entry_url(page, request, entry),
        "title": entry.title,
        "long_description": entry.long_description,
        "type": _label(vocab.get("types", {}), entry.type),
        "scope": _label(vocab.get("scope", {}), entry.scope),
        "tags": [_label(vocab.get("tags", {}), tag) for tag in entry.tags],
        "professions": [_label(vocab.get("professions", {}), p) for p in entry.professions],
        "owner": owner.get("organisation", ""),
        "team": owner.get("team", ""),
        "contact_url": contact_url,
        "contact_text": contact.get("email") or contact.get("slack") or "",
        "applies_to": entry.applies_to,
        "version": entry.version,
        "links": entry.links,
        "withdrawn": entry.status == "withdrawn",
        "source_name": (entry.source or {}).get("name", ""),
        # Where an external entry is published (a tool with a page of its own).
        "source_url": (entry.source or {}).get("path", "")
        if (entry.source or {}).get("type") == "external"
        else "",
        "repository_url": repository.tree_url() if repository else "",
        "report_url": issues.new_issue_url(f"Feedback on: {entry.title}") if issues else "",
        "edit_url": (
            f"{catalogue.html_url}/blob/{catalogue.branch}/{entries_dir}/{entry.entry_id}.yml"
            if catalogue
            else ""
        ),
        "related": related,
    }


def _masthead_facts(layout: str, review: dict) -> list[dict]:
    """The key facts in a standard's or policy's title band.

    A policy always shows all four, saying where one is not stated: a reader
    needs to know which version is in force, and since when. A standard shows
    the ones it has.
    """
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


def _sequence_context(page, request, entry, documents, structure, doc_path) -> dict:
    """The numbered requirements or steps, and where the current page sits."""
    sequence = [path for path in structure.get("sequence") or [] if path in documents]
    items = [
        {
            "number": number,
            "title": documents[path].title,
            "summary": documents[path].metadata.get("summary", ""),
            "url": entry_url(page, request, entry, path),
            "is_current": path == doc_path,
        }
        for number, path in enumerate(sequence, start=1)
    ]
    context = {"sequence": items, "sequence_total": len(items)}
    if doc_path in sequence:
        position = sequence.index(doc_path)
        context["sequence_number"] = position + 1
        context["sequence_previous"] = items[position - 1] if position > 0 else None
        context["sequence_next"] = items[position + 1] if position + 1 < len(items) else None
    return context


def navigation_tree(page, request, entry, structure: dict, current_path=None):
    """The entry's side navigation for ``current_path``, and its ancestors in it.

    Returns ``(groups, ancestors)``. Each group is ``{"title", "items"}`` and
    each item ``{"title", "url", "is_current", "is_open", "children"}``: an
    item's children show only while it ``is_open``, which is while the current
    page is that item or below it. A page listed more than once is current at
    its shallowest, earliest listing. A section that is not placed in the
    navigation shows above it while reading that section.
    """
    groups = []
    if current_path is not None:
        for section in structure.get("sections", []):
            paths = {item.get("path") for item in _walk_items(section.get("items", []))}
            if current_path == section.get("owner_path") or current_path in paths:
                groups.append({"title": section.get("title", ""), "items": section["items"]})
    groups.extend(structure.get("navigation", []))

    route = _find_route(groups, current_path) if current_path else None
    built = [
        {
            "title": group.get("title") or entry.title,
            "items": _navigation_items(page, request, entry, group.get("items", []), (index,), route),
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


def _navigation_items(page, request, entry, items, prefix, route) -> list[dict]:
    built = []
    for position, item in enumerate(items):
        here = (*prefix, position)
        built.append(
            {
                "title": item.get("title", ""),
                "url": item.get("url")
                or entry_url(page, request, entry, item.get("path") or "", item.get("fragment", "")),
                "path": item.get("path"),
                "is_current": route == here,
                "is_open": bool(route) and route[: len(here)] == here,
                "children": _navigation_items(
                    page, request, entry, item.get("children", []), here, route
                ),
            }
        )
    return built


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
        item = {"text": heading["text"], "anchor": heading["anchor"], "children": []}
        if heading.get("level") == 3 and contents:
            contents[-1]["children"].append(item)
        elif heading.get("level") == 2:
            contents.append(item)
    return contents


def _as_date(value):
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _review(metadata: dict, entry) -> dict:
    """A page's version, dates and review, from its front matter."""
    next_review_on = _as_date(metadata.get("next_review_on"))
    return {
        "version": metadata.get("version", ""),
        "effective_from": _as_date(metadata.get("effective_from")),
        "last_reviewed_on": _as_date(metadata.get("last_reviewed_on")),
        "next_review_on": next_review_on,
        "overdue": bool(next_review_on and next_review_on < date.today()),
        "owner": metadata.get("owner") or (entry.owner or {}).get("organisation", ""),
    }
