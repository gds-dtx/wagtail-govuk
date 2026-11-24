"""A document's Markdown, split into sections at its h2 and h3 headings.

A part with ``anchors`` set counts heading ids as the catalogue's pages do, so
a section's ``anchor`` matches the id on the page; other pages have none.
"""

import html
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
    """The document as one Markdown text, each part's heading as an h2."""
    out = []
    for part in parts:
        if part.heading:
            out.append(f"## {part.heading}")
        if part.markdown.strip():
            out.append(part.markdown.strip())
    return "\n\n".join(out) + ("\n" if out else "")


def _heading_text(markdown: str) -> tuple[str, str]:
    """A heading's display text, and its text for the id (runs joined by spaces)."""
    rendered = _markdown.renderInline(markdown)
    if "<" not in rendered:
        # Skip BeautifulSoup: it warns on plain text that looks like a filename.
        text = html.unescape(rendered).strip()
        return re.sub(r"\s+", " ", text), text
    heading = BeautifulSoup(rendered, "html.parser")
    return re.sub(r"\s+", " ", heading.get_text()).strip(), heading.get_text(" ", strip=True)


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
                text, id_text = _heading_text(match.group(2))
                # Count h4s as the page does, so a later duplicate's suffix matches.
                anchor = anchors.create(id_text, level) if anchors and 2 <= level <= 4 else ""
                if level in (2, 3):
                    start(level, text, anchor)
                    continue
            sections[-1].lines.append(line)

    chunks = []
    for section in sections:
        text = "\n".join(section.lines).strip()
        if not text:
            continue
        heading_path = HEADING_SEPARATOR.join(section.path)
        for piece in _split(text, max_chars):
            chunks.append(Chunk(heading_path=heading_path, anchor=section.anchor, text=piece))
    return chunks


def _split(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    paragraphs = []
    for paragraph in re.split(r"\n\s*\n", text):
        # Keep a code block with blank lines whole.
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
