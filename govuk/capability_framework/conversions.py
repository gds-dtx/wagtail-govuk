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
from datetime import date, datetime
from html.parser import HTMLParser

from django.utils.html import escape

# Link text and targets stop at the next bracket rather than running to the
# end of the note. Allowing "[" inside the text, or "(" inside the target,
# lets a note built from repeated "[a](" restart the scan at every character,
# which is quadratic in the length of the note.
MARKDOWN_LINK = re.compile(r"\[([^\][]+)\]\(([^()]+)\)")
EMPTY_MARKDOWN_LINK = re.compile(r"\[\s*+\]\([^()\[\]]*\)")

NOT_IN_USE = "NOT IN USE"
# The published exports use this sentence, without bullets, where a skill
# level has no description yet. The service publishes it in two wordings --
# 37 rows carry "currently" and one does not -- so it is recognised by shape
# rather than by the exact sentence. Matching the one sentence put the odd row
# out under a "You can:" heading in the CSV this service publishes back.
LEVEL_NOT_DEFINED = "This skill level is currently not defined."
_LEVEL_NOT_DEFINED = re.compile(
    r"^this skill level is (?:currently )?not defined\.?$", re.IGNORECASE
)

_BLOCK_TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "h6"}


class _RichTextLineParser(HTMLParser):
    """Turn rich text HTML into the framework's plain text line convention.

    Paragraphs and headings become lines, list items become ``- `` bullets.
    Parsing rather than pattern matching keeps this linear in the size of the
    input.
    """

    def __init__(self, *, preserve_links: bool = False):
        super().__init__(convert_charrefs=True)
        self.preserve_links = preserve_links
        self.lines: list[str] = []
        self._buffer: list[str] = []
        self._capturing: str | None = None
        self._link_url: str | None = None
        self._saw_block = False

    def handle_starttag(self, tag, attrs):
        if tag == "li":
            self._flush()
            self._capturing = "item"
            self._saw_block = True
        elif tag in _BLOCK_TAGS:
            self._flush()
            self._capturing = "block"
            self._saw_block = True
        elif tag == "a" and self.preserve_links and self._capturing:
            self._link_url = dict(attrs).get("href") or ""
            self._buffer.append("[")

    def handle_endtag(self, tag):
        if tag == "a" and self.preserve_links and self._link_url is not None:
            self._buffer.append(f"]({self._link_url})")
            self._link_url = None
        elif tag == "li" and self._capturing == "item":
            self._flush()
        elif tag in _BLOCK_TAGS and self._capturing == "block":
            self._flush()

    def handle_data(self, data):
        if self._capturing:
            self._buffer.append(data)

    def _flush(self):
        text = " ".join("".join(self._buffer).split())
        if text:
            if self._capturing == "item":
                self.lines.append(f"- {text}")
            else:
                # Consecutive paragraphs are separated by a blank line, but a
                # paragraph directly following bullets is not.
                if self.lines and not self.lines[-1].startswith("- "):
                    self.lines.append("")
                self.lines.append(text)
        self._buffer = []
        self._capturing = None

    def close(self):
        super().close()
        self._flush()

    @property
    def found_blocks(self) -> bool:
        return self._saw_block


class _PlainTextParser(HTMLParser):
    """Collect the text of a fragment that has no block-level markup."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []

    def handle_data(self, data):
        self._parts.append(data)

    @property
    def text(self) -> str:
        return " ".join("".join(self._parts).split())


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

    parser = _RichTextLineParser()
    parser.feed(str(html))
    parser.close()

    if not parser.found_blocks:
        # Content saved without block tags, for example a bare fragment.
        plain = _PlainTextParser()
        plain.feed(str(html))
        plain.close()
        return plain.text
    return "\n".join(parser.lines)


def is_level_not_defined(text: str) -> bool:
    """Whether a skill level's prose is the "no description yet" placeholder."""
    return bool(_LEVEL_NOT_DEFINED.match(" ".join((text or "").split())))


def points_to_text(points: list[str], *, prefix: str = "You can:") -> str:
    """Render skill level points back into the CSV prose convention."""
    if not points:
        return ""
    if len(points) == 1 and is_level_not_defined(points[0]):
        # Placeholder text is published without a prefix or bullet, and in the
        # wording it arrived in -- the round trip is not the place to settle
        # which of the service's two sentences is the right one.
        return points[0]
    bullets = "\n".join(f"- {point}" for point in points)
    return f"{prefix}\n{bullets}" if prefix else bullets


def _changelog_line_to_html(line: str) -> str:
    """Escape a change note line and turn its Markdown links into anchors."""
    # Links with no text render as a stray "[](/skills)" in the published
    # exports, so drop them rather than carrying the literal through.
    line = EMPTY_MARKDOWN_LINK.sub("", line).strip()
    return MARKDOWN_LINK.sub(
        lambda match: f'<a href="{match.group(2)}">{match.group(1)}</a>',
        escape(line),
    )


def changelog_note_to_html(text: str) -> str:
    """Convert a change note into rich text HTML.

    Notes use Markdown-style links and newline-separated statements, for
    example ``[Data engineer](/role/data-engineer) has updated skills.``
    Lines opening with ``- `` are the export's bullets and become a list.
    """
    html_parts: list[str] = []
    bullets: list[str] = []

    def flush_bullets():
        nonlocal bullets
        if bullets:
            items = "".join(f"<li>{bullet}</li>" for bullet in bullets)
            html_parts.append(f"<ul>{items}</ul>")
            bullets = []

    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("- "):
            bullet = _changelog_line_to_html(line[2:])
            if bullet:
                bullets.append(bullet)
            continue
        flush_bullets()
        paragraph = _changelog_line_to_html(line)
        if paragraph:
            html_parts.append(f"<p>{paragraph}</p>")
    flush_bullets()
    return "".join(html_parts)


# "Everything up to the closing tag", written so the engine never rescans:
# ``[^<]*`` cannot cross a "<", and the group that follows only starts at one.
# Spelt ``(?:(?!</p>).)*?`` instead, a paragraph that never closes is re-read
# from every position the leading whitespace can backtrack to, so a note of a
# few hundred KB takes minutes. The whitespace runs are possessive for the
# same reason: giving a space back can never help "-" or "<" match.
_PARAGRAPH_BODY = r"[^<]*(?:<(?!/p>)[^<]*)*"
_BULLET_PARAGRAPH = re.compile(rf"<p>\s*+-\s*+({_PARAGRAPH_BODY})</p>")
_BULLET_RUN = re.compile(rf"(?:<p>\s*+-\s*+{_PARAGRAPH_BODY}</p>\s*+)+")


def repair_changelog_html(html: str) -> str:
    """Repair change notes stored before the import understood the exports.

    Bullets arrived as hyphen paragraphs and links with no text arrived as a
    literal ``[](/skills)``. Rewriting the stored HTML in place keeps any
    formatting an editor has since added, which converting back to note text
    and re-rendering would drop.
    """
    if not html:
        return ""

    def to_list(match: re.Match) -> str:
        items = "".join(
            # The pattern reads up to the closing tag rather than stopping
            # short of the trailing whitespace, so strip it here.
            f"<li>{bullet.group(1).strip()}</li>"
            for bullet in _BULLET_PARAGRAPH.finditer(match.group(0))
        )
        return f"<ul>{items}</ul>" if items else match.group(0)

    repaired = _BULLET_RUN.sub(to_list, str(html))
    repaired = EMPTY_MARKDOWN_LINK.sub("", repaired)
    # Removing a trailing empty link can leave a paragraph holding nothing.
    return re.sub(r"<p>\s*</p>", "", repaired)


def changelog_html_to_note(html: str) -> str:
    """Convert changelog rich text back into Markdown-style note text."""
    if not html:
        return ""

    parser = _RichTextLineParser(preserve_links=True)
    parser.feed(str(html))
    parser.close()
    # Change notes are a list of statements, one per line.
    return "\n".join(line for line in parser.lines if line)


LEADERSHIP_HEADING = "Examples of leadership using this skill:"


def split_leadership_examples(text: str) -> tuple[str, list[str]]:
    """Separate a Senior Civil Service skill's description from its examples.

    These skills are published as one block of prose::

        You can:
        - do the thing
        Examples of leadership using this skill:
        - lead on the thing

    Returns the description text and the leadership bullet points.
    """
    if not text or LEADERSHIP_HEADING not in text:
        return text or "", []

    description, _, examples = text.partition(LEADERSHIP_HEADING)
    return description.strip(), parse_points(examples)


def parse_iso_date(value: str) -> date | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        pass
    # The source column is named "Timestamp": some exports carry a full
    # timestamp ("2026-05-29T09:00:00", a trailing "Z", or a space separator).
    # Take the date part rather than silently dropping the changelog row.
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None
