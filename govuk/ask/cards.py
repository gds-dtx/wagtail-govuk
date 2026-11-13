"""Search hits as cards: the catalogue's own, and the same shape for anything else.

One card an entry: a match within an entry (a point of a code, a step, a
page of a guide) is shown on its entry's card, as the part that matched
best, since the section pages list entries and not their parts. Other pages,
roles and skills get a card of the same shape, with their type.
"""

from urllib.parse import urlsplit

from wagtail.models import Page

from govuk.catalogue import presentation
from govuk.content_index.extract import entry_for
from govuk.models import IndexedDocument, entry_page_models

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
    seen: set[tuple] = set()
    for hit in hits:
        document = hit.document
        page = pages.get(document.page_id) if document.page_id else None
        entry = None
        if page is not None:
            entry = page if isinstance(page, entry_models) else entry_for(page)
        if entry is not None:
            key = ("entry", entry.pk)
            if key in seen:
                continue
            if vocab is None:
                vocab = presentation.vocabularies()
            card = _entry_card(entry, page, hit, request, vocab)
        else:
            key = ("document", document.pk)
            card = _other_card(document, page, hit, request)
        seen.add(key)
        cards.append(card)
        if len(cards) >= limit:
            break
    return cards
