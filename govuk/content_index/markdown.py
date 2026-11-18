"""Blocks and rich text as Markdown for the content index.

Unlike the catalogue export, which must round-trip and so keeps HTML, this
wants plain text. Links are absolute so a section read alone still works.
"""

import re

from bs4 import BeautifulSoup
from wagtail.documents import get_document_model
from wagtail.models import Page

from govuk.catalogue.markdown_blocks import _pipe_table, _quote, html_to_markdown

# Tags that start a new line when raw HTML is read as text.
_BLOCK_TAGS = (
    "address", "article", "aside", "blockquote", "br", "dd", "details", "div", "dl",
    "dt", "figcaption", "figure", "footer", "h1", "h2", "h3", "h4", "h5", "h6",
    "header", "hr", "li", "main", "nav", "ol", "p", "pre", "section", "summary", "ul",
)


class Links:
    """Absolute link URLs for one site, with page lookups cached."""

    def __init__(self, root_url: str):
        self.root_url = (root_url or "").rstrip("/")
        self._page_urls: dict[int, str] = {}

    def absolute(self, url: str) -> str:
        if url and url.startswith("/") and not url.startswith("//"):
            return self.root_url + url
        return url or ""

    def page(self, page) -> str:
        if page is None:
            return ""
        if page.pk not in self._page_urls:
            self._page_urls[page.pk] = page.get_full_url() or ""
        return self._page_urls[page.pk]

    def page_id(self, page_id: int) -> str:
        if page_id not in self._page_urls:
            page = Page.objects.filter(pk=page_id).first()
            self._page_urls[page_id] = (page.get_full_url() or "") if page else ""
        return self._page_urls[page_id]

    def href(self, attrs: dict) -> str | None:
        """Used as ``html_to_markdown``'s ``link_href``."""
        linktype = attrs.get("linktype")
        if linktype == "page":
            try:
                return self.page_id(int(attrs.get("id") or 0)) or None
            except ValueError:
                return None
        if linktype == "document":
            document = get_document_model().objects.filter(pk=attrs.get("id") or 0).first()
            return self.absolute(document.url) if document else None
        href = attrs.get("href", "")
        return self.absolute(href) if href.startswith("/") else None


def rich_text_markdown(value, links: Links | None = None) -> str:
    """Wagtail rich text as Markdown; an embedded image becomes its alt text."""
    html = getattr(value, "source", value) or ""
    if "<embed" in html:
        soup = BeautifulSoup(html, "html.parser")
        for embed in soup.find_all("embed"):
            alt = (embed.get("alt") or "").strip()
            if alt:
                embed.replace_with(alt)
            else:
                embed.decompose()
        html = str(soup)
    return html_to_markdown(html, links.href if links else None).strip()


def html_text(html: str) -> str:
    """Hand-written HTML as plain text, a line per block and table row."""
    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup(["script", "style", "template", "noscript"]):
        tag.decompose()
    for table in soup.find_all("table"):
        rows = []
        for row in table.find_all("tr"):
            cells = [
                cell.get_text(" ", strip=True).replace("|", "\\|")
                for cell in row.find_all(["th", "td"])
            ]
            if any(cells):
                rows.append("| " + " | ".join(cells) + " |")
        if rows:
            columns = rows[0].count(" | ") + 1
            rows.insert(1, "| " + " | ".join(["---"] * columns) + " |")
        table.replace_with("\n" + "\n".join(rows) + "\n")
    for tag in soup.find_all(_BLOCK_TAGS):
        tag.insert_before("\n")
        tag.insert_after("\n")
    lines = (re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in soup.get_text().splitlines())
    return "\n".join(line for line in lines if line)


def stream_markdown(stream, links: Links | None = None) -> str:
    parts = [block_markdown(child.block_type, child.value, links) for child in stream or []]
    return "\n\n".join(part.strip("\n") for part in parts if part and part.strip())


def block_markdown(block_type: str, value, links: Links | None = None) -> str:
    if block_type == "text":
        return rich_text_markdown(value, links)
    if block_type in ("inset_text", "warning_text"):
        text = rich_text_markdown(value, links)
        return _quote(text, "NOTE" if block_type == "inset_text" else "WARNING") if text else ""
    if block_type == "details":
        summary = (value.get("summary") or "").strip()
        text = rich_text_markdown(value.get("text"), links)
        return "\n\n".join(part for part in (f"**{summary}**" if summary else "", text) if part)
    if block_type == "code":
        return f"```{value.get('language') or ''}\n{value.get('code') or ''}\n```"
    if block_type == "table":
        return _table_markdown(value)
    if block_type == "raw_html":
        return html_text(str(value or ""))
    if block_type == "cards":
        return _cards_markdown(value, links)
    if block_type == "banner":
        return _banner_markdown(value, links)
    if block_type == "section":
        heading = (value.get("heading") or "").strip()
        level = 3 if value.get("level") == "h3" else 2
        text = rich_text_markdown(value.get("body"), links)
        return "\n\n".join(part for part in ("#" * level + " " + heading if heading else "", text) if part)
    if block_type == "skill_level_definitions":
        lines = []
        caption = (value.get("caption") or "").strip()
        if caption:
            lines.append(f"**{caption}**\n")
        for level in value.get("levels") or []:
            description = _one_line(rich_text_markdown(level.get("description"), links))
            lines.append(f"- **{level.get('name') or ''}**" + (f": {description}" if description else ""))
        return "\n".join(lines)
    return ""


def _one_line(markdown: str) -> str:
    return re.sub(r"\s*\n\s*", " ", markdown).strip()


def _table_markdown(value) -> str:
    # Tables saved before column widths existed are the grid itself.
    table = value.get("table") if "table" in value else value
    table = table or {}
    markdown = _pipe_table(table)
    caption = (table.get("table_caption") or "").strip()
    return f"**{caption}**\n\n{markdown}" if caption and markdown else markdown


def _link_url(link, links: Links | None) -> str:
    if not link:
        return ""
    external = (link.get("external_url") or "").strip()
    if external:
        return external
    page = link.get("page")
    return links.page(page) if links and page else ""


def _cards_markdown(value, links: Links | None) -> str:
    lines = []
    heading = (value.get("heading") or "").strip()
    if heading:
        lines.append(f"## {heading}\n")
    for card in value.get("cards") or []:
        title = (card.get("title") or "").strip()
        url = _link_url(card.get("link"), links)
        text = _one_line(rich_text_markdown(card.get("text"), links))
        label = f"[{title}]({url})" if title and url else title
        if label and text:
            lines.append(f"- **{label}**: {text}")
        elif label or text:
            lines.append(f"- **{label}**" if label else f"- {text}")
    return "\n".join(lines)


def _banner_markdown(value, links: Links | None) -> str:
    parts = []
    heading = (value.get("heading") or "").strip()
    if heading:
        parts.append(f"## {heading}")
    parts.append(rich_text_markdown(value.get("text"), links))
    parts.append(html_text(str(value.get("raw_html") or "")))
    button_text = (value.get("button_text") or "").strip()
    button_page = value.get("button_page")
    url = (links.page(button_page) if links and button_page else "") or (
        value.get("button_external_url") or ""
    ).strip()
    if button_text and url:
        parts.append(f"[{button_text}]({url})")
    return "\n\n".join(part for part in parts if part)
