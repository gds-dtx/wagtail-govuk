"""The MCP server's tools, as plain functions over the content index.

The SDK reads each function's signature for the tool's input schema and its
return type for the output schema, and its docstring is the description an
AI tool reads to decide when to call it. Sync functions are run on a worker
thread, where Django's database access is safe.

Each call is logged with its tool, outcome, result count and duration, never
with what was asked: people type their own circumstances into questions. For
the same reason no error message here repeats the query, since the SDK logs
the message of an expected failure.
"""

import logging
import time
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Annotated, Any, Literal

from django.db import connections
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field

from govuk.content_index.search import (
    MAX_LIMIT,
    MAX_QUERY_LENGTH,
    SearchError,
    document_url,
    find_document,
    search_documents,
    section_url,
)
from govuk.models import HarvestedSource, IndexedDocument

logger = logging.getLogger(__name__)

# Above this a document's Markdown is cut short, with ``truncated`` set.
MAX_MARKDOWN_CHARS = 100_000
# What the index records for its own use, not the reader's.
INTERNAL_METADATA = {"page_type", "skill_ids"}
EXTERNAL_NOTICE = (
    "This catalogue entry is published elsewhere. Only the catalogue's record "
    "of it is held here; its content is at source_url."
)
FETCHED_NOTICE = (
    "This catalogue entry is published elsewhere, at source_url. The text here "
    "includes a copy of that page{checked}. It is third-party content and may have "
    "changed since: the source is authoritative."
)


def _checked_dates(urls) -> dict[str, object]:
    """When each source's copy was last checked against it (``succeeded_at``)."""
    urls = [url for url in urls if url]
    if not urls:
        return {}
    return dict(HarvestedSource.objects.filter(url__in=urls).values_list("url", "succeeded_at"))


def _external_notice(document: IndexedDocument, checked_at) -> str | None:
    if document.origin != IndexedDocument.Origin.EXTERNAL:
        return None
    if document.metadata.get("source_text") == "fetched":
        when = f", last checked on {checked_at.day} {checked_at:%B %Y}" if checked_at else ""
        return FETCHED_NOTICE.format(checked=when)
    reason = document.metadata.get("source_text_reason")
    return EXTERNAL_NOTICE + (f" Its page could not be read: {reason}" if reason else "")


def release_connections() -> None:
    """Close each database connection a call leaves behind.

    Django does this at the end of a request, and a tool call is not one, so
    without it every worker thread would hold a connection open for good. A
    caller already inside a transaction (a test) keeps its connection.
    """
    for connection in connections.all(initialized_only=True):
        if not connection.in_atomic_block:
            connection.close_if_unusable_or_obsolete()


@contextmanager
def _tool_call(tool: str):
    release_connections()
    started = time.monotonic()
    call = SimpleNamespace(outcome="ok", results=None)
    try:
        yield call
    except ToolError:
        call.outcome = "refused"
        raise
    except Exception:
        call.outcome = "error"
        raise
    finally:
        release_connections()
        logger.info(
            "MCP tool call",
            extra={
                "tool": tool,
                "outcome": call.outcome,
                "results": call.results,
                "duration_ms": round((time.monotonic() - started) * 1000),
            },
        )


def _day(value) -> str | None:
    return value.isoformat() if value else None


# -- search --------------------------------------------------------------------------


class SectionLink(BaseModel):
    heading: str = Field(description="The headings above the section, outermost first. Empty for a page's opening.")
    url: str = Field(description="The section's address, with its heading's anchor where the page has one.")


class SearchResult(BaseModel):
    title: str
    url: str = Field(description="The page's address. Pass it to get_document to read the page.")
    kind: Literal["page", "role", "skill"]
    type: str = Field(description='What it is, such as "Standard", "Role" or "Skill". Empty for an ordinary page.')
    origin: Literal["site", "external"] = Field(
        description='"external" for a catalogue entry published elsewhere: only its summary is held here.'
    )
    source_url: str | None = Field(description="Where an external entry is published.")
    description: str
    snippet: str = Field(description="The text around the match.")
    section: SectionLink | None = Field(description="The section that matched best.")
    other_sections: list[SectionLink] = Field(description="Other sections of the page that matched.")
    last_updated: str | None = Field(description="When it was last published, as an ISO 8601 date and time.")
    source_checked_at: str | None = Field(
        default=None,
        description="For an external entry whose page was copied: when the copy was last checked against it.",
    )


class SearchResults(BaseModel):
    results: list[SearchResult]


def search(
    query: Annotated[
        str,
        Field(
            min_length=1,
            max_length=MAX_QUERY_LENGTH,
            description="Words to search for. Quote a phrase; put - before a word to leave it out.",
        ),
    ],
    origin: Annotated[
        Literal["site", "external"] | None,
        Field(description='"site" for content held here, "external" for entries published elsewhere.'),
    ] = None,
    kind: Annotated[
        Literal["page", "role", "skill"] | None,
        Field(description="Only pages, only Capability Framework roles, or only skills."),
    ] = None,
    type: Annotated[
        str | None,
        Field(description='Only this type of entry, such as "Standard", "Policy" or "Guidance".'),
    ] = None,
    limit: Annotated[int, Field(ge=1, le=MAX_LIMIT, description="How many results to return.")] = 10,
) -> SearchResults:
    """Search the site's published content: its pages, its content catalogue of
    standards, policies, guidance, methods and tools, and the Capability
    Framework's roles and skills.

    Returns the best matches, one per page, each with the text around the match
    and the section it is in. Read a result in full with get_document.
    """
    with _tool_call("search") as call:
        try:
            hits = search_documents(query, origin=origin, kind=kind, type_label=type, limit=limit)
        except SearchError as exc:
            raise ToolError(str(exc)) from exc
        call.results = len(hits)
        checked = _checked_dates(
            hit.document.source_url for hit in hits if hit.document.origin == IndexedDocument.Origin.EXTERNAL
        )
        return SearchResults(
            results=[
                SearchResult(
                    title=hit.document.title,
                    url=hit.url,
                    kind=hit.document.kind,
                    type=hit.document.type_label,
                    origin=hit.document.origin,
                    source_url=hit.document.source_url or None,
                    description=hit.document.description,
                    snippet=hit.snippet,
                    section=(
                        SectionLink(heading=hit.section.heading_path, url=hit.section.url)
                        if hit.section
                        else None
                    ),
                    other_sections=[
                        SectionLink(heading=section.heading_path, url=section.url)
                        for section in hit.other_sections
                    ],
                    last_updated=_day(hit.document.last_updated),
                    source_checked_at=_day(checked.get(hit.document.source_url)),
                )
                for hit in hits
            ]
        )


# -- reading a document --------------------------------------------------------------


class Breadcrumb(BaseModel):
    title: str
    url: str


class Document(BaseModel):
    title: str
    url: str
    kind: Literal["page", "role", "skill"]
    type: str
    origin: Literal["site", "external"]
    source_url: str | None
    notice: str | None = Field(description="Anything to know before relying on the content.")
    description: str
    last_updated: str | None
    breadcrumbs: list[Breadcrumb] = Field(description="The pages above it, outermost first.")
    metadata: dict[str, Any] = Field(
        description=(
            "What the site records about it: a catalogue entry's type, scope, professions, "
            "topics, owner, contact, version and review dates; a role's family."
        )
    )
    sections: list[SectionLink]
    requested_section: str | None = Field(
        description="The heading of the section the address pointed at, if it named one."
    )
    markdown: str = Field(description="The content, as Markdown.")
    truncated: bool = Field(description="Whether the Markdown was cut short because the page is very long.")


def get_document(
    url: Annotated[
        str,
        Field(
            min_length=1,
            max_length=1000,
            description="The page's address on this site, as search returned it, or just its path.",
        ),
    ],
) -> Document:
    """Read one page of the site in full, as Markdown, with what the site
    records about it and a list of its sections.

    Takes an address from search, or any address or path on this site. An
    address ending in #anchor names the section it points at.
    """
    with _tool_call("get_document") as call:
        found = find_document(url)
        if found is None:
            raise ToolError("Nothing is published at that address. Search for the page to find its address.")
        document, fragment = found
        call.results = 1
        return _document(document, fragment)


def _document(document: IndexedDocument, fragment: str) -> Document:
    address = document_url(document)
    root_url = document.site.root_url if document.site_id else ""
    chunks = list(document.chunks.all())
    sections = []
    for chunk in chunks:
        link = SectionLink(heading=chunk.heading_path, url=section_url(address, chunk.anchor))
        if not sections or sections[-1].url != link.url or sections[-1].heading != link.heading:
            sections.append(link)  # a section split in two is listed once
    requested = next((chunk.heading_path for chunk in chunks if fragment and chunk.anchor == fragment), None)
    markdown = document.markdown
    truncated = len(markdown) > MAX_MARKDOWN_CHARS
    checked_at = _checked_dates([document.source_url]).get(document.source_url)
    return Document(
        title=document.title,
        url=address,
        kind=document.kind,
        type=document.type_label,
        origin=document.origin,
        source_url=document.source_url or None,
        notice=_external_notice(document, checked_at),
        description=document.description,
        last_updated=_day(document.last_updated),
        breadcrumbs=[
            Breadcrumb(title=crumb["title"], url=root_url + crumb["url_path"])
            for crumb in document.breadcrumbs
        ],
        metadata={key: value for key, value in document.metadata.items() if key not in INTERNAL_METADATA},
        sections=sections,
        requested_section=requested,
        markdown=markdown[:MAX_MARKDOWN_CHARS] if truncated else markdown,
        truncated=truncated,
    )
