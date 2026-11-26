"""Searches the content index (keyword, hybrid or semantic) and finds a document by URL.

Kept free of the MCP SDK so the site's own search can use it too.
"""

import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from django.contrib.contenttypes.models import ContentType
from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector
from django.db import connections
from django.db.models import OuterRef, Q, Subquery
from wagtail.models import Site

from govuk.features import catalogue_enabled
from govuk.models import (
    ContentEmbedding,
    IndexedChunk,
    IndexedDocument,
    SearchSettings,
    entry_page_models,
)
from govuk.search_backend import SEARCH_CONFIG, SEARCH_WEIGHTS, UNMATCHED_RANK

from . import embeddings
from .extract import SUB_PAGE_MODELS

logger = logging.getLogger(__name__)

MAX_QUERY_LENGTH = 200
MAX_LIMIT = 25
DEFAULT_LIMIT = 10
# High enough that one long document's sections cannot crowd out the rest.
CANDIDATE_SECTIONS = 300
OTHER_SECTIONS = 2
SNIPPET_CHARS = 300
EXACT_TITLE_BOOST = 1.0

KEYWORD, HYBRID, SEMANTIC = "keyword", "hybrid", "semantic"
MODES = (KEYWORD, HYBRID, SEMANTIC)
# Reciprocal rank fusion: rank r adds weight / (RRF_K + r); 60 is the usual constant.
RRF_K = 60
# How much each list counts. Tune with manage.py evaluate_content_search.
FUSION_WEIGHTS = {
    "every_word_sections": 1.0,
    "every_word_documents": 1.0,
    "any_word_sections": 0.5,
    "any_word_documents": 0.5,
    "meaning_sections": 1.0,
    "meaning_documents": 1.0,
}
MEANING_SECTIONS = 50
MEANING_DOCUMENTS = 20

SEMANTIC_OK, SEMANTIC_OFF, SEMANTIC_UNAVAILABLE = "ok", "off", "unavailable"


class SearchError(ValueError):
    """Its message is shown to the person asking and never repeats the query."""


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
    # "every" word, "some" words, or None for meaning alone.
    matched_words: str | None = "every"
    # Best cosine similarity; None without embeddings.
    similarity: float | None = None
    # For AI search's summary.
    section_ids: list[int] = field(default_factory=list)


@dataclass
class SearchOutcome:
    hits: list[SearchHit]
    semantic: str  # SEMANTIC_OK, SEMANTIC_OFF or SEMANTIC_UNAVAILABLE


def is_close(hit: SearchHit, min_similarity: float, *, semantic: str = SEMANTIC_OK) -> bool:
    """Whether a hit is close enough to offer as an answer.

    Without embeddings, a hit with some of the words will do.
    """
    if hit.matched_words == "every":
        return True
    if hit.similarity is not None and hit.similarity >= min_similarity:
        return True
    return semantic != SEMANTIC_OK and hit.matched_words == "some"


def document_url(document: IndexedDocument) -> str:
    root_url = document.site.root_url if document.site_id else ""
    return root_url + document.url_path


def section_url(document_address: str, anchor: str) -> str:
    return f"{document_address}#{anchor}" if anchor else document_address


def clean_query(query, max_length: int = MAX_QUERY_LENGTH) -> str:
    cleaned = re.sub(r"\s+", " ", str(query or "")).strip()
    if not cleaned:
        raise SearchError("Give some words to search for.")
    if len(cleaned) > max_length:
        raise SearchError(f"Keep a search to {max_length} characters or fewer.")
    return cleaned


def _terms(query: str) -> list[str]:
    words = re.findall(r"\w+", query.lower())
    return [word for word in words if word != "or"] or [query.lower()]


def _content_terms(query: str) -> list[str]:
    """The query's words without stopwords, for the any-word lists."""
    words = re.findall(r"\w+", query.lower().replace("’", "'"))
    kept = []
    for word in words:
        if word in embeddings.STOPWORDS or len(word) < 2 or word in kept:
            continue
        kept.append(word)
    return kept


def in_search_scope(documents):
    """What each document's site lets search find (Settings > Search): with "Catalogue
    entries only", its catalogue entries and their pages and nothing else."""
    if not catalogue_enabled():
        return documents
    whole_sites = SearchSettings.objects.filter(search_scope=SearchSettings.SearchScope.ALL).values("site_id")
    catalogue_types = ContentType.objects.get_for_models(*entry_page_models(), *SUB_PAGE_MODELS).values()
    return documents.filter(Q(site__in=whole_sites) | Q(page__content_type__in=catalogue_types))


def search_documents(query, **kwargs) -> list[SearchHit]:
    return search(query, **kwargs).hits


def search(
    query,
    *,
    origin: str | None = None,
    kind: str | None = None,
    type_label: str | None = None,
    limit: int = DEFAULT_LIMIT,
    mode: str = KEYWORD,
    min_similarity: float | None = None,
    max_length: int = MAX_QUERY_LENGTH,
) -> SearchOutcome:
    """``min_similarity`` drops meaning-only hits below it; hybrid search otherwise always finds something."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    query = clean_query(query, max_length)
    limit = max(1, min(int(limit), MAX_LIMIT))
    documents = in_search_scope(IndexedDocument.objects.all())
    if origin:
        documents = documents.filter(origin=origin)
    if kind:
        documents = documents.filter(kind=kind)
    if type_label:
        documents = documents.filter(type_label__iexact=type_label.strip())
    chunks = IndexedChunk.objects.filter(document__in=documents.values("pk"))
    postgres = connections[documents.db].vendor == "postgresql"
    terms = _terms(query)

    if mode == KEYWORD:
        ranked = _keyword_ranking(chunks, documents, query, postgres)
        return SearchOutcome([_hit(entry, terms) for entry in ranked[:limit]], SEMANTIC_OFF)

    # Snippets centre on a word that matters, not on "how" or "I".
    terms = _content_terms(query) or terms
    section_lists: dict[str, list] = {}
    document_lists: dict[str, list] = {}
    similarity: dict[tuple[str, int], float] = {}
    if mode == HYBRID:
        every = _search_query(query) if postgres else None
        section_lists["every_word_sections"] = _sections(chunks, query, every, postgres)
        document_lists["every_word_documents"] = _documents(documents, query, every, postgres)
        content_terms = _content_terms(query)
        if content_terms:
            some = _search_query(" or ".join(content_terms)) if postgres else None
            section_lists["any_word_sections"] = _sections(chunks, content_terms, some, postgres)
            document_lists["any_word_documents"] = _documents(documents, content_terms, some, postgres)

    semantic = SEMANTIC_OFF
    embedder = embeddings.get_embedder()
    if embedder is not None:
        try:
            vector = embeddings.embed_query(query, embedder)
        except embeddings.EmbeddingError as exc:
            # Never log the query.
            logger.warning("Searching by words alone: %s", exc)
            semantic = SEMANTIC_UNAVAILABLE
        else:
            semantic = SEMANTIC_OK
            found_sections = _meaning_sections(chunks, vector, embedder.key, postgres)
            found_documents = _meaning_documents(documents, vector, embedder.key, postgres)
            section_lists["meaning_sections"] = [chunk for chunk, _ in found_sections]
            document_lists["meaning_documents"] = [document for document, _ in found_documents]
            similarity.update((("chunk", chunk.pk), score) for chunk, score in found_sections)
            similarity.update((("document", document.pk), score) for document, score in found_documents)

    ranked = _fuse(section_lists, document_lists, similarity, query)
    if min_similarity is not None:
        ranked = [
            entry
            for entry in ranked
            if entry["matched_words"] is not None or (entry["similarity"] or 0) >= min_similarity
        ]
    return SearchOutcome([_hit(entry, terms) for entry in ranked[:limit]], semantic)


def _search_query(text: str) -> SearchQuery:
    return SearchQuery(text, search_type="websearch", config=SEARCH_CONFIG)


def _sections(chunks, query, search_query, postgres: bool) -> list[IndexedChunk]:
    if postgres:
        return [chunk for chunk, _ in _postgres_sections(chunks, search_query)]
    return [chunk for chunk, _ in _sqlite_sections(chunks, query)]


def _documents(documents, query, search_query, postgres: bool) -> list[IndexedDocument]:
    if postgres:
        return [document for document, _ in _postgres_documents(documents, search_query)]
    return [document for document, _ in _sqlite_documents(documents, query)]


def _keyword_ranking(chunks, documents, query: str, postgres: bool) -> list[dict]:
    if postgres:
        search_query = _search_query(query)
        section_scores = _postgres_sections(chunks, search_query)
        document_scores = _postgres_documents(documents, search_query)
    else:
        section_scores = _sqlite_sections(chunks, query)
        document_scores = _sqlite_documents(documents, query)

    found: dict[int, dict] = {}
    for chunk, score in section_scores:
        entry = found.setdefault(chunk.document_id, _entry(chunk.document))
        entry["sections"].append(chunk)
        entry["score"] = max(entry["score"], score)
    for document, score in document_scores:
        entry = found.setdefault(document.pk, _entry(document))
        entry["score"] += score
    _boost_exact_titles(found, query)
    return sorted(found.values(), key=lambda entry: (-entry["score"], entry["document"].title.lower()))


def _entry(document) -> dict:
    return {"document": document, "score": 0.0, "sections": [], "matched_words": "every", "similarity": None}


def _boost_exact_titles(found: dict, query: str) -> None:
    folded = query.casefold()
    for entry in found.values():
        if entry["document"].title.casefold() == folded:
            entry["score"] += EXACT_TITLE_BOOST


def _fuse(section_lists: dict, document_lists: dict, similarity: dict, query: str) -> list[dict]:
    """Merge ranked lists into one entry per document by reciprocal rank fusion."""
    section_score: dict[int, float] = defaultdict(float)
    sections: dict[int, IndexedChunk] = {}
    found: dict[int, dict] = {}
    words: dict[int, str] = {}

    def note_words(document_id: int, list_name: str) -> None:
        if list_name.startswith("every_word"):
            words[document_id] = "every"
        elif list_name.startswith("any_word"):
            words.setdefault(document_id, "some")

    for name, ranked in section_lists.items():
        weight = FUSION_WEIGHTS[name]
        for rank, chunk in enumerate(ranked, start=1):
            section_score[chunk.pk] += weight / (RRF_K + rank)
            sections.setdefault(chunk.pk, chunk)
            note_words(chunk.document_id, name)
    for chunk in sections.values():
        entry = found.setdefault(chunk.document_id, _entry(chunk.document))
        entry["sections"].append(chunk)
    for entry in found.values():
        entry["sections"].sort(key=lambda chunk: (-section_score[chunk.pk], chunk.position))
        entry["score"] = section_score[entry["sections"][0].pk]

    for name, ranked in document_lists.items():
        weight = FUSION_WEIGHTS[name]
        for rank, document in enumerate(ranked, start=1):
            entry = found.setdefault(document.pk, _entry(document))
            entry["score"] += weight / (RRF_K + rank)
            note_words(document.pk, name)

    for document_id, entry in found.items():
        entry["matched_words"] = words.get(document_id)
        scores = [similarity[("chunk", chunk.pk)] for chunk in entry["sections"] if ("chunk", chunk.pk) in similarity]
        if ("document", document_id) in similarity:
            scores.append(similarity[("document", document_id)])
        entry["similarity"] = max(scores) if scores else None
    _boost_exact_titles(found, query)
    return sorted(found.values(), key=lambda entry: (-entry["score"], entry["document"].title.lower()))


def _hit(entry: dict, terms: list[str]) -> SearchHit:
    document = entry["document"]
    address = document_url(document)
    sections = entry["sections"]
    how = {"matched_words": entry["matched_words"], "similarity": entry["similarity"]}
    if not sections:
        return SearchHit(
            document=document,
            url=address,
            score=entry["score"],
            snippet=_snippet(document.description, terms),
            **how,
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
        section_ids=[best.pk, *(chunk.pk for chunk in others)],
        **how,
    )


def _postgres_sections(chunks, search_query: SearchQuery) -> list[tuple[IndexedChunk, float]]:
    vector = (
        SearchVector("document__title", weight="A", config=SEARCH_CONFIG)
        + SearchVector("heading_path", weight="B", config=SEARCH_CONFIG)
        + SearchVector("text", weight="C", config=SEARCH_CONFIG)
    )
    ranked = (
        chunks.select_related("document__site")
        .annotate(rank=SearchRank(vector, search_query, weights=SEARCH_WEIGHTS))
        .filter(rank__gt=UNMATCHED_RANK)
        .order_by("-rank", "document_id", "position")[:CANDIDATE_SECTIONS]
    )
    return [(chunk, float(chunk.rank)) for chunk in ranked]


def _postgres_documents(documents, search_query: SearchQuery) -> list[tuple[IndexedDocument, float]]:
    vector = SearchVector("title", weight="A", config=SEARCH_CONFIG) + SearchVector(
        "description", weight="B", config=SEARCH_CONFIG
    )
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


def _any_term(terms: list[str], *fields: str) -> Q:
    condition = Q()
    for term in terms:
        for name in fields:
            condition |= Q(**{f"{name}__icontains": term})
    return condition


def _sqlite_condition(query, *fields: str) -> tuple[list[str], Q]:
    """A query string asks for every word; a list of words, for any of them."""
    if isinstance(query, list):
        return query, _any_term(query, *fields)
    terms = _terms(query)
    return terms, _every_term(terms, *fields)


def _sqlite_sections(chunks, query) -> list[tuple[IndexedChunk, float]]:
    terms, condition = _sqlite_condition(query, "document__title", "heading_path", "text")
    matching = chunks.select_related("document__site").filter(condition)
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


def _sqlite_documents(documents, query) -> list[tuple[IndexedDocument, float]]:
    terms, condition = _sqlite_condition(query, "title", "description")
    scored = [
        (document, _relevance(terms, ((document.title, 3.0), (document.description, 1.0))))
        for document in documents.select_related("site").filter(condition)
    ]
    scored.sort(key=lambda pair: (-pair[1], pair[0].title.lower()))
    return scored[:CANDIDATE_SECTIONS]


# -- by meaning ----------------------------------------------------------------------


def _meaning_sections(chunks, vector, model_key: str, postgres: bool) -> list[tuple[IndexedChunk, float]]:
    chunks = chunks.exclude(embedding_hash="").select_related("document__site")
    if postgres:
        return _postgres_closest(chunks, vector, model_key, MEANING_SECTIONS, ("document_id", "position"))
    return _python_closest(chunks, vector, model_key, MEANING_SECTIONS)


def _meaning_documents(documents, vector, model_key: str, postgres: bool) -> list[tuple[IndexedDocument, float]]:
    documents = documents.exclude(embedding_hash="").select_related("site")
    if postgres:
        return _postgres_closest(documents, vector, model_key, MEANING_DOCUMENTS, ("title",))
    return _python_closest(documents, vector, model_key, MEANING_DOCUMENTS)


def _postgres_closest(rows, vector, model_key: str, limit: int, tie_break: tuple) -> list[tuple]:
    """Exact nearest neighbours by cosine distance; no ANN index needed at this size."""
    from pgvector.django import CosineDistance

    stored = ContentEmbedding.objects.filter(model=model_key, text_hash=OuterRef("embedding_hash")).values(
        "vector"
    )[:1]
    ranked = (
        rows.annotate(distance=CosineDistance(Subquery(stored), vector))
        .filter(distance__isnull=False)
        .order_by("distance", *tie_break)[:limit]
    )
    return [(row, 1.0 - float(row.distance)) for row in ranked]


def _python_closest(rows, vector, model_key: str, limit: int) -> list[tuple]:
    """As ``_postgres_closest``, in Python, since SQLite has no vector type."""
    rows = list(rows)
    stored = dict(
        ContentEmbedding.objects.filter(
            model=model_key, text_hash__in={row.embedding_hash for row in rows}
        ).values_list("text_hash", "vector")
    )
    scored = [
        (row, embeddings.cosine_similarity(stored[row.embedding_hash], vector))
        for row in rows
        if row.embedding_hash in stored
    ]
    scored.sort(key=lambda pair: -pair[1])
    return scored[:limit]


def _snippet(text: str, terms: list[str]) -> str:
    flat = re.sub(r"\s+", " ", text or "").strip()
    if len(flat) <= SNIPPET_CHARS:
        return flat
    lowered = flat.lower()
    positions = []
    for term in terms:
        # Crude stem, so "testing" lands on "tests".
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
    """The document at ``url`` and its leftover ``#fragment``, or None.

    Matches on host, not port, so a local server on another port still matches.
    """
    raw = str(url or "").strip()
    if not raw:
        return None
    parts = urlsplit(raw)
    # Nothing search would not list.
    documents = in_search_scope(IndexedDocument.objects.select_related("site"))
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
