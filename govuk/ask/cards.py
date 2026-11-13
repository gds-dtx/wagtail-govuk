"""Search hits as cards: the catalogue's own, and the same shape for anything else.

One card an entry: a match within an entry (a point of a code, a step, a
page of a guide) is shown on its entry's card, as the part that matched
best, since the section pages list entries and not their parts. Other pages,
roles and skills get a card of the same shape, with their type.

Each card also carries ``source_documents`` and ``source_sections``, the ids
of the index's documents and sections it was found by (from every match
folded onto it), for the summary and the evaluation.
"""

from urllib.parse import urlsplit

from wagtail.models import Page

from govuk.catalogue import presentation
from govuk.content_index.extract import entry_for
from govuk.models import IndexedDocument, entry_page_models

from .scope import describe

MAX_CARDS = 6


def _local(url: str) -> str:
    """A site address as a path, so it works on whichever host is serving."""
    parts = urlsplit(url)
    return parts.path + (f"#{parts.fragment}" if parts.fragment else "")


def _section_line(hit) -> dict | None:
    if hit.section is None or not hit.section.heading_path:
        return None
    return {"title": hit.section.heading_path, "url": _local(hit.section.url)}


def _entry_card(entry, page, hit, request, vocab) -> dict:
    card = presentation.card(presentation.EntryView.from_page(entry, request), vocab)
    scope = getattr(entry, "scope", None)
    card["applies_to"] = describe(scope.label if scope else "", getattr(entry, "owner_organisation", ""))
    if page is not entry:
        card["section"] = {"title": page.title, "url": page.get_url(request) or ""}
    elif not card["external"]:
        # An external entry's card goes to its source, where the site's
        # sections of it do not exist.
        card["section"] = _section_line(hit)
    return card


def _other_card(document, page, hit, request) -> dict:
    url = page.get_url(request) if page is not None else _local(hit.url)
    kind_label = "" if document.kind == IndexedDocument.Kind.PAGE else document.get_kind_display()
    return {
        "title": document.title,
        "url": url or _local(hit.url),
        "external": False,
        "source_name": "",
        "description": document.description or hit.snippet,
        "type": document.type_label or kind_label,
        "primary": False,
        "withdrawn": False,
        "section": _section_line(hit),
    }


def cards_for(hits, request, *, limit: int = MAX_CARDS) -> list[dict]:
    """Up to ``limit`` cards for ``hits``, best first."""
    page_ids = [hit.document.page_id for hit in hits if hit.document.page_id]
    pages = {page.pk: page for page in Page.objects.filter(pk__in=page_ids).specific()}
    entry_models = entry_page_models()
    vocab = None
    cards: list[dict] = []
    seen: dict[tuple, dict] = {}
    for hit in hits:
        document = hit.document
        page = pages.get(document.page_id) if document.page_id else None
        entry = None
        if page is not None:
            entry = page if isinstance(page, entry_models) else entry_for(page)
        key = ("entry", entry.pk) if entry is not None else ("document", document.pk)
        if key in seen:
            seen[key]["source_documents"].append(document.pk)
            seen[key]["source_sections"].extend(hit.section_ids)
            continue
        if len(cards) >= limit:
            continue  # still folding later parts onto the cards there are
        if entry is not None:
            if vocab is None:
                vocab = presentation.vocabularies()
            card = _entry_card(entry, page, hit, request, vocab)
        else:
            card = _other_card(document, page, hit, request)
        card["source_documents"] = [document.pk]
        card["source_sections"] = list(hit.section_ids)
        seen[key] = card
        cards.append(card)
    return cards
