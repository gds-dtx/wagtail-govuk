"""Reading a source page's text, as Markdown, by what kind of source it is.

- **www.gov.uk**: the GOV.UK Content API (``/api/content<path>``), which gives
  a page's body without the page around it. A guide's parts come under their
  own titles; an index-style page whose body is empty (the Service Manual,
  the Service Standard) is the list of pages beneath it, with their
  descriptions.
- **github.com**: a repository page's rendered README.
- **Anything else**: the page's main content (``<main>``, or what stands for
  it), without its navigation, header, footer, scripts or forms.

The page alone, not the pages it links to. HTML is read into Markdown here,
not with the rich text converter (``catalogue.markdown_blocks``), which
expects Wagtail's own small set of tags at the top level: a source page is
anybody's HTML, nested however deep. Links are made absolute.
"""

import json
import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

from .fetch import HTML_ACCEPT, FetchError, Response, fetch

GOVUK_HOST = "www.gov.uk"
GOVUK_API = "https://www.gov.uk/api/content"
# Under this much text a page has nothing to index: most often it is drawn
# by JavaScript, which a fetch does not run.
MIN_TEXT_CHARS = 200
MAX_MARKDOWN_CHARS = 200_000
# A refusal that is the site's robots.txt rather than an HTTP status.
ROBOTS_REFUSAL = 999

_DROP_TAGS = (
    "script", "style", "noscript", "template", "svg", "iframe", "form", "button", "nav",
    "header", "footer", "aside", "select", "input", "textarea", "dialog", "canvas",
    "video", "audio", "picture", "img", "object", "embed", "link", "meta",
)
# The GOV.UK Design System's furniture, which sits inside <main> on many sites.
_DROP_CLASSES = (
    "govuk-skip-link", "govuk-breadcrumbs", "govuk-back-link", "govuk-cookie-banner",
    "govuk-phase-banner", "govuk-visually-hidden", "gem-c-contextual-sidebar",
    "gem-c-print-link", "gem-c-single-page-notification-button",
)
_BLOCK_TAGS = {
    "address", "article", "blockquote", "center", "dd", "details", "div", "dl", "dt",
    "fieldset", "figcaption", "figure", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "li",
    "main", "ol", "p", "pre", "section", "summary", "table", "ul",
}


class SourceSkipped(Exception):
    """Not a page the index reads (not https, not HTML)."""


class NoText(Exception):
    """A page that was read and has no text worth indexing."""


class NotModified(Exception):
    """The source answered 304: the copy already held is current."""


@dataclass
class SourceText:
    title: str
    markdown: str
    fetched_url: str
    etag: str = ""
    last_modified: str = ""
    updated_at: datetime | None = None


def read_source(url: str, *, fetcher=None, robots=None, etag: str = "", last_modified: str = "") -> SourceText:
    """The text at ``url``. Raises ``FetchError`` (with ``status`` for a refusal
    by robots.txt of 999), ``SourceSkipped``, ``NoText`` or ``NotModified``."""
    fetcher = fetcher or fetch
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise SourceSkipped("Only https addresses are fetched.")
    host = (parts.hostname or "").lower()
    target = GOVUK_API + (parts.path.rstrip("/") or "/") if host == GOVUK_HOST else url
    if robots is not None and not robots.allows(target):
        raise FetchError("The site's robots.txt does not allow it.", status=ROBOTS_REFUSAL)
    response = fetcher(
        target,
        accept="application/json" if host == GOVUK_HOST else HTML_ACCEPT,
        etag=etag,
        last_modified=last_modified,
    )
    if response.status == 304:
        raise NotModified()
    if host == GOVUK_HOST:
        text = _govuk_content(response, url)
    else:
        text = _html_page(response, readme=host == "github.com")
    text.etag = response.headers.get("etag", "")
    text.last_modified = response.headers.get("last-modified", "")
    if len(text.markdown) > MAX_MARKDOWN_CHARS:
        text.markdown = text.markdown[:MAX_MARKDOWN_CHARS].rsplit("\n", 1)[0].rstrip()
    return text



# -- GOV.UK --------------------------------------------------------------------------


def _govuk_content(response: Response, url: str) -> SourceText:
    try:
        data = json.loads(response.body)
    except ValueError as exc:
        raise FetchError("GOV.UK's Content API did not answer with JSON.") from exc
    details = data.get("details") or {}
    links = data.get("links") or {}
    sections = []
    body = _govuk_html(details.get("body"))
    if body:
        sections.append(html_markdown(body, "https://www.gov.uk")[1])
    for part in details.get("parts") or []:
        part_body = _govuk_html(part.get("body"))
        heading = (part.get("title") or "").strip()
        text = html_markdown(part_body, "https://www.gov.uk")[1] if part_body else ""
        sections.append("\n\n".join(item for item in (f"## {heading}" if heading else "", text) if item))
    for collection in details.get("collections") or []:
        # The Service Toolkit: groups of links, each with a description.
        head = [f"## {collection['title']}"] if collection.get("title") else []
        if collection.get("description"):
            head.append(collection["description"])
        links_list = "\n".join(_govuk_link(link, link.get("url", "")) for link in collection.get("links") or [])
        sections.append("\n\n".join(head + ([links_list] if links_list else [])))
    attachments = details.get("attachments") or []
    if attachments:
        lines = [_govuk_link(item, item.get("url", "")) for item in attachments if item.get("title")]
        sections.append("## Documents\n\n" + "\n".join(lines))
    children = links.get("children") or []
    if children and not body:
        # In the API's own order, which is not the pages': "1." to "14." for the
        # Service Standard's points, so they are put back in order by title.
        children = sorted((child for child in children if child.get("title")), key=_natural_title)
        lines = [_govuk_link(child, child.get("base_path", "")) for child in children]
        sections.append("## Pages\n\n" + "\n".join(lines))
    markdown = "\n\n".join(section for section in sections if section.strip())
    description = (data.get("description") or "").strip()
    if description and len(markdown) < MIN_TEXT_CHARS:
        markdown = "\n\n".join(item for item in (description, markdown) if item)
    if len(markdown) < MIN_TEXT_CHARS:
        raise NoText("GOV.UK holds no text for this page.")
    return SourceText(
        title=(data.get("title") or "").strip(),
        markdown=markdown,
        fetched_url=url,
        updated_at=_iso_datetime(data.get("public_updated_at")),
    )


def _natural_title(item: dict) -> list:
    """A title's sort key with its numbers as numbers, so "2." sorts before "10."."""
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", item["title"])]


def _govuk_html(body) -> str:
    """A Content API body: HTML, or a list of renderings of which one is HTML."""
    if isinstance(body, str):
        return body
    for rendering in body or []:
        if isinstance(rendering, dict) and rendering.get("content_type") == "text/html":
            return rendering.get("content") or ""
    return ""


def _govuk_link(item: dict, href: str) -> str:
    title = (item.get("title") or "").strip()
    line = f"- [{title}]({urljoin('https://www.gov.uk', href)})" if href else f"- {title}"
    description = (item.get("description") or "").strip()
    return f"{line}: {description}" if description else line


def _iso_datetime(value) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if value else None
    except ValueError:
        return None


# -- any page ------------------------------------------------------------------------


def _html_page(response: Response, *, readme: bool = False) -> SourceText:
    if response.content_type not in ("text/html", "application/xhtml+xml", ""):
        raise SourceSkipped(f"It is not a web page ({response.content_type}).")
    soup = BeautifulSoup(response.body, "html.parser")
    page_title = soup.title.get_text(" ", strip=True) if soup.title else ""
    node = (
        (soup.select_one("article.markdown-body") if readme else None)
        or soup.find("main")
        or soup.find(attrs={"role": "main"})
        or soup.find(id="main-content")
        or soup.body
    )
    if node is None:
        raise NoText("The page has no content.")
    title, markdown = html_markdown(node, response.url)
    if len(markdown) < MIN_TEXT_CHARS:
        raise NoText("The page has almost no text. It may be drawn by JavaScript.")
    return SourceText(title=title or page_title, markdown=markdown, fetched_url=response.url)


def html_markdown(html, base_url: str) -> tuple[str, str]:
    """``html`` (a string or a parsed node) as Markdown, and the text of its first
    h1, which is taken as the page's title rather than repeated in it."""
    node = BeautifulSoup(html, "html.parser") if isinstance(html, str) else html
    for tag in node.find_all(_DROP_TAGS):
        tag.decompose()
    for tag in node.find_all(True):
        if tag.decomposed:
            continue
        classes = tag.get("class") or []
        if (
            tag.get("hidden") is not None
            or tag.get("aria-hidden") == "true"
            or any(name in classes for name in _DROP_CLASSES)
        ):
            tag.decompose()
    converter = _Converter(base_url)
    blocks = converter.blocks(node)
    return converter.title, "\n\n".join(block for block in blocks if block.strip())


class _Converter:
    def __init__(self, base_url: str):
        self.base_url = base_url
        self.title = ""

    def blocks(self, node) -> list[str]:
        out: list[str] = []
        inline: list = []

        def flush():
            text = self.inline_text(inline)
            if text:
                out.append(text)
            inline.clear()

        for child in node.children:
            if isinstance(child, Comment):
                continue
            if isinstance(child, NavigableString) or (isinstance(child, Tag) and child.name not in _BLOCK_TAGS):
                inline.append(child)
                continue
            flush()
            out.extend(self.block(child))
        flush()
        return out

    def block(self, tag: Tag) -> list[str]:
        name = tag.name
        if name in ("h1", "h2", "h3", "h4", "h5", "h6"):
            text = self.inline_text(tag.children)
            if not text:
                return []
            if name == "h1" and not self.title:
                self.title = text
                return []
            level = min(max(int(name[1]), 2), 4)
            return ["#" * level + " " + text]
        if name in ("ul", "ol"):
            text = self.list(tag, 0)
            return [text] if text else []
        if name == "table":
            text = self.table(tag)
            return [text] if text else []
        if name == "pre":
            code = tag.get_text().strip("\n")
            return [f"```\n{code}\n```"] if code.strip() else []
        if name == "blockquote":
            inner = "\n\n".join(self.blocks(tag))
            return ["\n".join(f"> {line}" if line else ">" for line in inner.splitlines())] if inner else []
        if name == "hr":
            return ["---"]
        if name == "dl":
            lines = []
            for item in tag.find_all(["dt", "dd"], recursive=False):
                text = self.inline_text(item.children)
                if text:
                    lines.append(f"**{text}**" if item.name == "dt" else text)
            return ["\n\n".join(lines)] if lines else []
        if name == "summary":
            text = self.inline_text(tag.children)
            return [f"**{text}**"] if text else []
        if name == "p":
            text = self.inline_text(tag.children)
            return [text] if text else []
        return self.blocks(tag)

    def list(self, tag: Tag, depth: int) -> str:
        lines = []
        items = tag.find_all("li", recursive=False)
        for number, item in enumerate(items, start=1):
            nested = [child for child in item.children if isinstance(child, Tag) and child.name in ("ul", "ol")]
            text = self.inline_text(child for child in item.children if child not in nested)
            marker = f"{number}." if tag.name == "ol" else "-"
            if text:
                lines.append("  " * depth + f"{marker} {text}")
            for child in nested:
                nested_text = self.list(child, depth + 1)
                if nested_text:
                    lines.append(nested_text)
        return "\n".join(lines)

    def table(self, tag: Tag) -> str:
        rows = []
        for row in tag.find_all("tr"):
            cells = [self.inline_text(cell.children).replace("|", "\\|") for cell in row.find_all(["th", "td"])]
            if any(cells):
                rows.append(cells)
        if not rows:
            return ""
        width = max(len(row) for row in rows)
        rows = [row + [""] * (width - len(row)) for row in rows]
        lines = ["| " + " | ".join(rows[0]) + " |", "| " + " | ".join(["---"] * width) + " |"]
        lines += ["| " + " | ".join(row) + " |" for row in rows[1:]]
        return "\n".join(lines)

    def inline_text(self, nodes) -> str:
        return re.sub(r"[ \t]+", " ", "".join(self._inline(node) for node in nodes)).strip()

    def _inline(self, node) -> str:
        if isinstance(node, Comment):
            return ""
        if isinstance(node, NavigableString):
            return re.sub(r"\s+", " ", str(node))
        if not isinstance(node, Tag):
            return ""
        if node.name == "br":
            return " "
        if node.name in _BLOCK_TAGS:
            # A block inside a sentence (a list in a table cell): its text, on one line.
            return " " + " ".join(self.blocks(node)).replace("\n", " ") + " "
        inner = "".join(self._inline(child) for child in node.children)
        stripped = inner.strip()
        if not stripped:
            return inner
        if node.name in ("strong", "b"):
            return f"**{stripped}**"
        if node.name in ("em", "i"):
            return f"_{stripped}_"
        if node.name in ("code", "kbd", "samp"):
            return f"`{node.get_text()}`"
        if node.name == "a":
            href = (node.get("href") or "").strip()
            if href and not href.startswith(("#", "javascript:")):
                return f"[{stripped}]({urljoin(self.base_url, href)})"
        return inner
