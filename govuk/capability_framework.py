"""Conversions between Capability Framework CSV content and Wagtail rich text.

The framework publishes its content as CSV exports where prose uses a plain
text convention:

    You can:
    - do the first thing
    - do the second thing

These helpers move that convention to and from the HTML stored in Wagtail
rich text fields, so content can be imported from the published exports and
exported back out in the same shape.
"""

import re
from datetime import date

from django.utils.html import escape

MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_TAG = re.compile(r"<[^>]+>")
_LIST_ITEM = re.compile(r"<li[^>]*>(.*?)</li>", re.S)
_PARAGRAPH = re.compile(r"<p[^>]*>(.*?)</p>", re.S)
_BLOCK = re.compile(r"<(p|ul|ol|h[1-6])[^>]*>.*?</\1>", re.S)

NOT_IN_USE = "NOT IN USE"
# The published exports use this sentence, without bullets, where a skill
# level has no description yet.
LEVEL_NOT_DEFINED = "This skill level is currently not defined."


def _unescape(text: str) -> str:
    from html import unescape

    return unescape(text).strip()


def parse_points(text: str) -> list[str]:
    """Extract the bullet points from a ``You can:`` style block."""
    if not text or text.strip() == NOT_IN_USE:
        return []
    points = [
        line[2:].strip()
        for line in text.splitlines()
        if line.strip().startswith("- ")
    ]
    if points:
        return [point[:500] for point in points]
    cleaned = text.replace("You can:", "").strip()
    return [cleaned[:500]] if cleaned else []


def text_to_rich_html(text: str) -> str:
    """Convert CSV prose into rich text HTML."""
    if not text or text.strip() == NOT_IN_USE:
        return ""

    html_parts: list[str] = []
    bullets: list[str] = []

    def flush_bullets():
        nonlocal bullets
        if bullets:
            items = "".join(f"<li>{escape(b)}</li>" for b in bullets)
            html_parts.append(f"<ul>{items}</ul>")
            bullets = []

    for line in text.splitlines():
        line = line.strip()
        if not line:
            flush_bullets()
        elif line.startswith("- "):
            bullets.append(line[2:].strip())
        else:
            flush_bullets()
            html_parts.append(f"<p>{escape(line)}</p>")
    flush_bullets()
    return "".join(html_parts)


def rich_html_to_text(html) -> str:
    """Convert rich text HTML back into the CSV prose convention.

    The inverse of :func:`text_to_rich_html` for the shapes the framework
    uses: paragraphs become lines, list items become ``- `` bullets.
    Accepts a string or any rich text value that renders to HTML.
    """
    if not html:
        return ""
    html = str(html)

    lines: list[str] = []
    for match in _BLOCK.finditer(html):
        block = match.group(0)
        if block.startswith("<ul") or block.startswith("<ol"):
            for item in _LIST_ITEM.finditer(block):
                text = _unescape(_TAG.sub("", item.group(1)))
                if text:
                    lines.append(f"- {text}")
        else:
            text = _unescape(_TAG.sub("", block))
            if text:
                # Consecutive paragraphs are separated by a blank line, but a
                # paragraph directly following bullets is not, and bullets
                # follow their introducing line directly.
                if lines and not lines[-1].startswith("- "):
                    lines.append("")
                lines.append(text)

    if not lines:
        # Content saved without block tags, for example a bare fragment.
        text = _unescape(_TAG.sub("", html))
        return text
    return "\n".join(lines)


def points_to_text(points: list[str], *, prefix: str = "You can:") -> str:
    """Render skill level points back into the CSV prose convention."""
    if not points:
        return ""
    if points == [LEVEL_NOT_DEFINED]:
        # Placeholder text is published without a prefix or bullet.
        return LEVEL_NOT_DEFINED
    bullets = "\n".join(f"- {point}" for point in points)
    return f"{prefix}\n{bullets}" if prefix else bullets


def changelog_note_to_html(text: str) -> str:
    """Convert a change note into rich text HTML.

    Notes use Markdown-style links and newline-separated statements, for
    example ``[Data engineer](/role/data-engineer) has updated skills.``
    """
    paragraphs = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        linked = MARKDOWN_LINK.sub(
            lambda match: f'<a href="{match.group(2)}">{match.group(1)}</a>',
            escape(line),
        )
        paragraphs.append(f"<p>{linked}</p>")
    return "".join(paragraphs)


def changelog_html_to_note(html: str) -> str:
    """Convert changelog rich text back into Markdown-style note text."""
    if not html:
        return ""
    lines: list[str] = []
    for match in _PARAGRAPH.finditer(html):
        paragraph = re.sub(
            r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
            lambda m: f"[{m.group(2)}]({m.group(1)})",
            match.group(1),
            flags=re.S,
        )
        text = _unescape(_TAG.sub("", paragraph))
        if text:
            lines.append(text)
    return "\n".join(lines)


def parse_iso_date(value: str) -> date | None:
    try:
        return date.fromisoformat((value or "").strip())
    except ValueError:
        return None
