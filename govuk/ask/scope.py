"""Who a match applies to, and which entry it is part of, for Claude to weigh.

A catalogue entry records its scope (Public sector, Central government,
Devolved government or Single organisation) and the organisation that owns
it, and the index holds both in the entry's metadata. A page within an entry
(a page of a guide, a point of a standard) holds neither, and takes its
entry's. Anything outside the catalogue has no scope.
"""

from dataclasses import dataclass

from wagtail.models import Page

from govuk.content_index.extract import entry_for
from govuk.models import IndexedDocument


@dataclass(frozen=True)
class Scope:
    applies_to: str = ""  # "Single organisation (Home Office)"
    part_of: str = ""  # the entry's title, for a page within one


def describe(scope_label: str, owner: str) -> str:
    scope_label, owner = (scope_label or "").strip(), (owner or "").strip()
    if scope_label and owner:
        return f"{scope_label} ({owner})"
    return scope_label or (f"Owned by {owner}" if owner else "")


def _from_metadata(metadata: dict) -> str:
    return describe(metadata.get("scope") or "", metadata.get("owner_organisation") or "")


def scopes_for(documents) -> dict[int, Scope]:
    """Each document's ``Scope``, by its id."""
    documents = list(documents)
    scopes: dict[int, Scope] = {}
    within: dict[int, IndexedDocument] = {}  # page id: document, for pages with no scope of their own
    for document in documents:
        applies_to = _from_metadata(document.metadata or {})
        if applies_to or not document.page_id:
            scopes[document.pk] = Scope(applies_to)
        else:
            within[document.page_id] = document
    if not within:
        return scopes
    entries = {}
    for page in Page.objects.filter(pk__in=within).specific():
        entry = entry_for(page)
        if entry is not None:
            entries[page.pk] = entry
    entry_metadata = dict(
        IndexedDocument.objects.filter(page_id__in=[entry.pk for entry in entries.values()]).values_list(
            "page_id", "metadata"
        )
    )
    for page_id, document in within.items():
        entry = entries.get(page_id)
        if entry is None:
            scopes[document.pk] = Scope()
            continue
        scopes[document.pk] = Scope(_from_metadata(entry_metadata.get(entry.pk) or {}), entry.title)
    return scopes
