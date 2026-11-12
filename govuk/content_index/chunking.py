"""A document's Markdown, split into sections at its headings.

A document is read as ``Part``s: each a run of Markdown as one stream renders
on the page, optionally opened by a heading the page prints around it (a
policy's numbered section, a role level's title). A section starts at each h2
and h3 and carries the headings above it; one longer than ``MAX_CHUNK_CHARS``
is split between paragraphs, never inside a code block.

The catalogue's pages give their headings GitHub-style ids
(``presentation.add_heading_anchors``), counted afresh for each stream they
render. A part with ``anchors`` set is counted the same way, so a section's
``anchor`` is the id on the page. Other pages make their heading ids in the
browser, so their sections have none.
"""

import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

from govuk.catalogue.render import _GithubAnchors, _markdown

MAX_CHUNK_CHARS = 1500
HEADING_SEPARATOR = " › "

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")


@dataclass
class Part:
    markdown: str
    heading: str = ""
    anchor: str = ""
    anchors: bool = False


@dataclass
class Chunk:
    heading_path: str
    anchor: str
    text: str


@dataclass
class _Section:
    path: list[str] = field(default_factory=list)
    anchor: str = ""
    lines: list[str] = field(default_factory=list)


def parts_markdown(parts: list[Part]) -> str:
    """The whole document as one Markdown text, each part's heading as an h2."""
    out = []
    for part in parts:
        if part.heading:
            out.append(f"## {part.heading}")
        if part.markdown.strip():
            out.append(part.markdown.strip())
    return "\n\n".join(out) + ("\n" if out else "")


def chunk_parts(parts: list[Part], max_chars: int = MAX_CHUNK_CHARS) -> list[Chunk]:
    sections = [_Section()]
    open_headings: list[tuple[int, str]] = []

    def start(level: int, text: str, anchor: str) -> None:
        while open_headings and open_headings[-1][0] >= level:
            open_headings.pop()
        open_headings.append((level, text))
        sections.append(_Section(path=[heading for _, heading in open_headings], anchor=anchor))

    for part in parts:
        anchors = _GithubAnchors() if part.anchors else None
        if part.heading:
            start(2, part.heading, part.anchor)
        elif sections[-1].lines:
            sections[-1].lines.append("")  # parts are paragraphs apart, as on the page
        in_fence = False
        for line in part.markdown.splitlines():
            if _FENCE.match(line):
                in_fence = not in_fence
            match = None if in_fence else _HEADING.match(line)
            if match:
                level = len(match.group(1))
                heading = BeautifulSoup(_markdown.renderInline(match.group(2)), "html.parser")
                text = re.sub(r"\s+", " ", heading.get_text()).strip()
                # Read as the page reads its own heading for the id, text runs
                # joined by spaces; h4s are counted too, as the page counts
                # them, so a later duplicate's suffix matches.
                anchor = (
                    anchors.create(heading.get_text(" ", strip=True), level)
                    if anchors and 2 <= level <= 4
                    else ""
                )
                if level in (2, 3):
                    start(level, text, anchor)
                    continue
            sections[-1].lines.append(line)

    chunks = []
    for section in sections:
        text = "\n".join(section.lines).strip()
        if not text:
            # A heading straight into another: the next section's path names it.
            continue
        heading_path = HEADING_SEPARATOR.join(section.path)
        for piece in _split(text, max_chars):
            chunks.append(Chunk(heading_path=heading_path, anchor=section.anchor, text=piece))
    return chunks


def _split(text: str, max_chars: int) -> list[str]:
    """``text`` in pieces of about ``max_chars``, broken between paragraphs."""
    if len(text) <= max_chars:
        return [text]
    paragraphs = []
    for paragraph in re.split(r"\n\s*\n", text):
        # A code block may hold blank lines: keep it whole.
        if paragraphs and paragraphs[-1].count("```") % 2:
            paragraphs[-1] += "\n\n" + paragraph
        else:
            paragraphs.append(paragraph)
    pieces = []
    current = ""
    for paragraph in paragraphs:
        if current and len(current) + len(paragraph) + 2 > max_chars:
            pieces.append(current)
            current = paragraph
        else:
            current = f"{current}\n\n{paragraph}" if current else paragraph
    if current:
        pieces.append(current)
    return pieces
