"""Searching the content index, and finding a document by its address.

What the MCP server's tools run, kept free of the MCP SDK so anything else
that wants the index (the site's own search, later tools) can use it too.

A search matches sections, each read with its document's title, so a query
whose words are split between a page's title and one of its sections still
finds that section. Documents are matched on their title and description as
well, which is how a document with no text of its own (a page type the index
only titles) is found. Results are one per document, at its best section.

PostgreSQL ranks with full-text search, as the site search does; SQLite,
which tests and local runs use, asks for every word and scores on where it
appears.
"""

import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector
from django.db import connections
from django.db.models import Q
from wagtail.models import Site

from govuk.models import IndexedChunk, IndexedDocument
from govuk.search_backend import SEARCH_CONFIG, SEARCH_WEIGHTS, UNMATCHED_RANK

MAX_QUERY_LENGTH = 200
MAX_LIMIT = 25
DEFAULT_LIMIT = 10
# Sections read before they are grouped into documents: enough that a long
# document's many matching sections cannot crowd out the rest.
CANDIDATE_SECTIONS = 300
OTHER_SECTIONS = 2
SNIPPET_CHARS = 300
EXACT_TITLE_BOOST = 1.0


class SearchError(ValueError):
    """A query that cannot be run. Its message is for the person asking, and
    never repeats what they asked."""


@dataclass
class Section:
    heading_path: str
    url: str


@dataclass
class SearchHit:
    document: IndexedDocument
    url: str
    score: float
    snippet: str
    section: Section | None = None
    other_sections: list[Section] = field(default_factory=list)


def document_url(document: IndexedDocument) -> str:
    root_url = document.site.root_url if document.site_id else ""
    return root_url + document.url_path


def section_url(document_address: str, anchor: str) -> str:
    return f"{document_address}#{anchor}" if anchor else document_address


def clean_query(query) -> str:
    cleaned = re.sub(r"\s+", " ", str(query or "")).strip()
    if not cleaned:
        raise SearchError("Give some words to search for.")
    if len(cleaned) > MAX_QUERY_LENGTH:
        raise SearchError(f"Keep a search to {MAX_QUERY_LENGTH} characters or fewer.")
    return cleaned


def _terms(query: str) -> list[str]:
    words = re.findall(r"\w+", query.lower())
    return [word for word in words if word != "or"] or [query.lower()]


def search_documents(
    query,
    *,
    origin: str | None = None,
    kind: str | None = None,
    type_label: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[SearchHit]:
    query = clean_query(query)
    limit = max(1, min(int(limit), MAX_LIMIT))
    documents = IndexedDocument.objects.all()
    if origin:
        documents = documents.filter(origin=origin)
    if kind:
        documents = documents.filter(kind=kind)
    if type_label:
        documents = documents.filter(type_label__iexact=type_label.strip())
    chunks = IndexedChunk.objects.filter(document__in=documents.values("pk"))

    if connections[documents.db].vendor == "postgresql":
        section_scores = _postgres_sections(chunks, query)
        document_scores = _postgres_documents(documents, query)
    else:
        section_scores = _sqlite_sections(chunks, query)
        document_scores = _sqlite_documents(documents, query)

    found: dict[int, dict] = {}
    for chunk, score in section_scores:
        entry = found.setdefault(
            chunk.document_id, {"document": chunk.document, "score": 0.0, "sections": []}
        )
        entry["sections"].append(chunk)
        entry["score"] = max(entry["score"], score)
    for document, score in document_scores:
        entry = found.setdefault(document.pk, {"document": document, "score": 0.0, "sections": []})
        entry["score"] += score
    folded = query.casefold()
    for entry in found.values():
        if entry["document"].title.casefold() == folded:
            entry["score"] += EXACT_TITLE_BOOST

    ranked = sorted(found.values(), key=lambda entry: (-entry["score"], entry["document"].title.lower()))
    terms = _terms(query)
    return [_hit(entry, terms) for entry in ranked[:limit]]


def _hit(entry: dict, terms: list[str]) -> SearchHit:
    document = entry["document"]
    address = document_url(document)
    sections = entry["sections"]
    if not sections:
        return SearchHit(
            document=document,
            url=address,
            score=entry["score"],
            snippet=_snippet(document.description, terms),
        )
    best, others = sections[0], sections[1 : 1 + OTHER_SECTIONS]
    return SearchHit(
        document=document,
        url=address,
        score=entry["score"],
        snippet=_snippet(best.text, terms),
        section=Section(best.heading_path, section_url(address, best.anchor)),
        other_sections=[
            Section(chunk.heading_path, section_url(address, chunk.anchor)) for chunk in others
        ],
    )


def _postgres_sections(chunks, query: str) -> list[tuple[IndexedChunk, float]]:
    vector = (
        SearchVector("document__title", weight="A", config=SEARCH_CONFIG)
        + SearchVector("heading_path", weight="B", config=SEARCH_CONFIG)
        + SearchVector("text", weight="C", config=SEARCH_CONFIG)
    )
    search_query = SearchQuery(query, search_type="websearch", config=SEARCH_CONFIG)
    ranked = (
        chunks.select_related("document__site")
        .annotate(rank=SearchRank(vector, search_query, weights=SEARCH_WEIGHTS))
        .filter(rank__gt=UNMATCHED_RANK)
        .order_by("-rank", "document_id", "position")[:CANDIDATE_SECTIONS]
    )
    return [(chunk, float(chunk.rank)) for chunk in ranked]


def _postgres_documents(documents, query: str) -> list[tuple[IndexedDocument, float]]:
    vector = SearchVector("title", weight="A", config=SEARCH_CONFIG) + SearchVector(
        "description", weight="B", config=SEARCH_CONFIG
    )
    search_query = SearchQuery(query, search_type="websearch", config=SEARCH_CONFIG)
    ranked = (
        documents.select_related("site")
        .annotate(rank=SearchRank(vector, search_query, weights=SEARCH_WEIGHTS))
        .filter(rank__gt=UNMATCHED_RANK)
        .order_by("-rank")[:CANDIDATE_SECTIONS]
    )
    return [(document, float(document.rank)) for document in ranked]


def _every_term(terms: list[str], *fields: str) -> Q:
    condition = Q()
    for term in terms:
        any_field = Q()
        for name in fields:
            any_field |= Q(**{f"{name}__icontains": term})
        condition &= any_field
    return condition


def _relevance(terms: list[str], weighted: tuple[tuple[str, float], ...]) -> float:
    score = 0.0
    for text, weight in weighted:
        lowered = (text or "").lower()
        score += sum(weight for term in terms if term in lowered)
    return score


def _sqlite_sections(chunks, query: str) -> list[tuple[IndexedChunk, float]]:
    terms = _terms(query)
    matching = chunks.select_related("document__site").filter(
        _every_term(terms, "document__title", "heading_path", "text")
    )
    scored = [
        (
            chunk,
            _relevance(
                terms,
                ((chunk.document.title, 3.0), (chunk.heading_path, 2.0), (chunk.text, 1.0)),
            ),
        )
        for chunk in matching
    ]
    scored.sort(key=lambda pair: (-pair[1], pair[0].document_id, pair[0].position))
    return scored[:CANDIDATE_SECTIONS]


def _sqlite_documents(documents, query: str) -> list[tuple[IndexedDocument, float]]:
    terms = _terms(query)
    matching = documents.select_related("site").filter(_every_term(terms, "title", "description"))
    return [
        (document, _relevance(terms, ((document.title, 3.0), (document.description, 1.0))))
        for document in matching
    ]


def _snippet(text: str, terms: list[str]) -> str:
    """About ``SNIPPET_CHARS`` of ``text``, around the first word searched for."""
    flat = re.sub(r"\s+", " ", text or "").strip()
    if len(flat) <= SNIPPET_CHARS:
        return flat
    lowered = flat.lower()
    positions = []
    for term in terms:
        # The stem as well: "testing" should land on "tests".
        for form in (term, term[:5]):
            position = lowered.find(form)
            if position >= 0:
                positions.append(position)
                break
    start = max(0, min(positions) - SNIPPET_CHARS // 3) if positions else 0
    if start:
        start = flat.rfind(" ", 0, start) + 1
    end = start + SNIPPET_CHARS
    snippet = flat[start:end]
    if end < len(flat):
        snippet = snippet[: snippet.rfind(" ")] if " " in snippet else snippet
        snippet += " …"
    return ("… " if start else "") + snippet


def find_document(url) -> tuple[IndexedDocument, str] | None:
    """The document at ``url``, and the ``#fragment`` it named, or None.

    ``url`` may be a full address on this site (its host is checked, not its
    port, so a local server on another port still matches), a path, or an
    external catalogue entry's own source address. A role or skill address
    ends in its fragment; any other fragment names a section of the page.
    """
    raw = str(url or "").strip()
    if not raw:
        return None
    parts = urlsplit(raw)
    documents = IndexedDocument.objects.select_related("site")
    if parts.netloc:
        host = (parts.hostname or "").lower()
        sites = [site for site in Site.objects.all() if site.hostname.lower() == host]
        if not sites:
            document = (
                documents.filter(origin=IndexedDocument.Origin.EXTERNAL)
                .filter(Q(source_url=raw) | Q(source_url=raw.rstrip("/")) | Q(source_url=raw.rstrip("/") + "/"))
                .first()
            )
            return (document, "") if document else None
        documents = documents.filter(site__in=sites)
    path = parts.path or "/"
    if not path.startswith("/"):
        path = "/" + path
    if not path.endswith("/"):
        path += "/"
    if parts.fragment:
        document = documents.filter(url_path=f"{path}#{parts.fragment}").first()
        if document:
            return document, ""
    document = documents.filter(url_path=path).first()
    return (document, parts.fragment) if document else None
