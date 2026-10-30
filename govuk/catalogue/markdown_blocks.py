"""Convert between the catalogue's Markdown and the blocks its pages hold.

The import turns a page's Markdown into the stream of blocks its page holds
(``markdown_to_blocks``); the export turns the blocks back into Markdown
(``blocks_to_markdown``). The two are close but not exact inverses:

| Markdown                                   | Block                             |
| ------------------------------------------ | --------------------------------- |
| paragraphs, headings, lists, links, emphasis | ``text`` (Wagtail rich text)     |
| a table of plain text                      | ``table``                         |
| a table with links or code in it           | ``raw_html`` (a GOV.UK table)     |
| ``> [!WARNING]``, ``[!CAUTION]``, ``[!IMPORTANT]`` | ``warning_text``          |
| ``> [!NOTE]``, ``[!TIP]``, a plain quote   | ``inset_text``                    |
| fenced or indented code                    | ``code``                          |
| ``<details>``                              | ``details``                       |
| images, SVG, other allowed HTML            | ``raw_html``                      |

The page's first ``#`` heading is its title, so it is left out of the body.
Cards and banners, which Markdown has no form of, export as their HTML.

Everything from Markdown is cleaned (``render.sanitise``) before it becomes a
block: the catalogue is a repo the site does not control.
"""

import html as html_lib
import re
from collections.abc import Callable

from bs4 import BeautifulSoup, NavigableString, Tag
from bs4.element import PreformattedString

from .render import _markdown, sanitise

_ALERT = re.compile(r"^\s*\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*", re.I)
_WARNINGS = ("WARNING", "CAUTION", "IMPORTANT")
_RICH_TEXT_TAGS = {"p", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "hr"}
# What rich text can hold: the catalogue's text block's features. A paragraph
# or list with anything else in it (an image, an SVG, ``<sup>``) is kept as an
# HTML block, which the editor would otherwise strip on its first save.
_RICH_TEXT_HOLDS = {
    "p", "h2", "h3", "h4", "ul", "ol", "li", "hr", "a", "b", "strong", "i", "em", "code", "br",
}
# Inline tags whose meaning is in their text: rich text keeps that.
_UNWRAP = {"abbr", "span", "mark", "small", "u", "ins", "cite", "dfn"}
_AS_CODE = {"kbd", "samp", "var", "tt"}
# A link the import can point at one of the catalogue's own pages: takes the
# href as the Markdown wrote it, and returns ``(page id, address)``, or None to
# leave it be. The address is empty for a link to the page itself, which
# becomes a page link; a link to a heading on it needs the address, as page
# links carry no fragment. A link that is not to a page may still be given an
# address (``(None, address)``): a file in the repo, on GitHub.
LinkTarget = Callable[[str], tuple[int | None, str] | None]
# Where an image the Markdown gives by a relative path is served from.
ImageSource = Callable[[str], str]


# -- Markdown to blocks -------------------------------------------------------------


def markdown_to_blocks(
    markdown: str,
    *,
    link_target: LinkTarget | None = None,
    image_src: ImageSource | None = None,
) -> list[dict]:
    """The stream data (``[{"type", "value"}]``) for a page's Markdown body."""
    soup = BeautifulSoup(_markdown.render(markdown or ""), "html.parser")
    if image_src is not None:
        for image in soup.find_all("img"):
            if image.get("src"):
                image["src"] = image_src(image["src"])
    blocks: list[dict] = []
    text: list[str] = []
    dropped_title = False

    def flush_text():
        if text:
            blocks.append({"type": "text", "value": "".join(text).strip()})
            text.clear()

    for node in soup.contents:
        if isinstance(node, PreformattedString):
            continue  # a comment, which the page does not show
        if isinstance(node, NavigableString):
            if node.strip():
                text.append(f"<p>{html_lib.escape(str(node).strip())}</p>")
            continue
        if not isinstance(node, Tag):
            continue
        if node.name == "h1" and not dropped_title and not blocks and not text:
            dropped_title = True
            continue
        if node.name == "h1":
            node.name = "h2"
        if node.name in ("h5", "h6"):
            node.name = "h4"
        if node.name in _RICH_TEXT_TAGS and _fits_rich_text(node):
            text.append(_rich_text(str(node), link_target))
            continue
        flush_text()
        block = _component(node, link_target)
        if block is not None:
            blocks.append(block)
    flush_text()
    return blocks


def split_sections(
    markdown: str,
    *,
    link_target: LinkTarget | None = None,
    image_src: ImageSource | None = None,
):
    """``(introduction blocks, [{"title", "body"}])``: a policy split at each ``##``."""
    intro: list[str] = []
    sections: list[dict] = []
    for line in (markdown or "").splitlines(keepends=True):
        heading = re.match(r"^##\s+(.+?)\s*#*\s*$", line)
        if heading:
            sections.append({"title": heading.group(1).strip(), "lines": []})
        elif sections:
            sections[-1]["lines"].append(line)
        else:
            intro.append(line)
    return (
        markdown_to_blocks("".join(intro), link_target=link_target, image_src=image_src),
        [
            {
                "title": section["title"],
                "body": markdown_to_blocks(
                    "".join(section["lines"]), link_target=link_target, image_src=image_src
                ),
            }
            for section in sections
        ],
    )


def _fits_rich_text(node: Tag) -> bool:
    """Whether ``node`` can be rich text, once its plain inline tags are simplified."""
    for tag in node.find_all(True):
        if tag.name in _UNWRAP:
            tag.unwrap()
        elif tag.name in _AS_CODE:
            tag.name = "code"
            tag.attrs = {}
    return all(tag.name in _RICH_TEXT_HOLDS for tag in [node, *node.find_all(True)])


def _rich_text(html: str, link_target: LinkTarget | None) -> str:
    """Clean HTML as Wagtail stores rich text, with the catalogue's links as page links."""
    soup = BeautifulSoup(sanitise(html), "html.parser")
    for link in soup.find_all("a"):
        href = link.get("href", "")
        if not link_target or not href or href.startswith("#"):
            continue
        target = link_target(href)
        if target is None:
            continue
        page_id, address = target
        if address:
            # Right until the page moves; a page link would follow it, but
            # would lose the heading.
            link["href"] = address
        else:
            link.attrs = {"linktype": "page", "id": str(page_id)}
    for tag in soup.find_all(["strong", "em"]):
        tag.name = "b" if tag.name == "strong" else "i"
    for tag in soup.find_all("p"):
        if not tag.get_text(strip=True) and not tag.find(True):
            tag.decompose()
    # Rich text keeps no ids: the page gives its headings their anchors.
    for tag in soup.find_all(True):
        if tag.name != "a":
            tag.attrs.pop("id", None)
    return str(soup).strip()


def _component(node: Tag, link_target: LinkTarget | None) -> dict | None:
    if node.name == "blockquote":
        first = node.find("p")
        alert = _ALERT.match(first.get_text()) if first is not None else None
        if alert:
            _strip_alert(first)
        inner = "".join(str(child) for child in node.contents).strip()
        kind = "warning_text" if alert and alert.group(1).upper() in _WARNINGS else "inset_text"
        if kind == "warning_text":
            # Warning text is one line of bold text; paragraphs become breaks.
            soup = BeautifulSoup(_rich_text(inner, link_target), "html.parser")
            parts = [p.decode_contents() for p in soup.find_all("p")] or [str(soup)]
            return {"type": kind, "value": "<p>" + "<br/>".join(parts) + "</p>"}
        return {"type": kind, "value": _rich_text(inner, link_target)}
    if node.name == "pre":
        code = node.find("code")
        language = ""
        if code is not None:
            for name in code.get("class", []):
                if name.startswith("language-"):
                    language = name[len("language-") :]
        text = (code or node).get_text()
        return {"type": "code", "value": {"language": language, "code": text.rstrip("\n")}}
    if node.name == "table":
        if node.find(["a", "code", "img", "strong", "em"]):
            return {"type": "raw_html", "value": _govuk_table_html(node)}
        return {"type": "table", "value": _table_value(node)}
    if node.name == "details":
        summary = node.find("summary")
        summary_text = summary.get_text(" ", strip=True) if summary else "More information"
        if summary is not None:
            summary.decompose()
        # The Markdown between <details> and </details> is already rendered:
        # the parser nests it inside the element.
        inner = "".join(str(child) for child in node.contents).strip()
        return {
            "type": "details",
            "value": {"summary": summary_text, "text": _rich_text(inner, link_target)},
        }
    if node.name == "div" and node.get("data-guidance-component") == "warning":
        return {"type": "warning_text", "value": _rich_text(f"<p>{node.decode_contents()}</p>", link_target)}
    cleaned = sanitise(str(node)).strip()
    return {"type": "raw_html", "value": cleaned} if cleaned else None


def _strip_alert(paragraph: Tag) -> None:
    for text in paragraph.find_all(string=True):
        if text.strip():
            text.replace_with(_ALERT.sub("", str(text), count=1))
            return


def _table_value(table: Tag) -> dict:
    """A plain table as the table block holds it: rows of cell text, a header row."""
    rows = []
    for row in table.find_all("tr"):
        rows.append([cell.get_text(" ", strip=True) for cell in row.find_all(["th", "td"])])
    width = max((len(row) for row in rows), default=0)
    rows = [row + [""] * (width - len(row)) for row in rows]
    has_header = table.find("thead") is not None
    if has_header and rows and not any(rows[0]):
        # A pipe table must have a header row; an empty one means it has none.
        rows, has_header = rows[1:], False
    return {
        "table": {
            "data": rows,
            "first_row_is_table_header": has_header,
            "first_col_is_header": False,
            "table_caption": "",
            "cell": [],
            "mergeCells": [],
        },
        "column_widths": [],
    }


def _govuk_table_html(table: Tag) -> str:
    """A table whose cells hold links or code, in GOV.UK table markup."""
    soup = BeautifulSoup(sanitise(str(table)), "html.parser")
    for tag, name in (
        ("table", "govuk-table"), ("thead", "govuk-table__head"), ("tbody", "govuk-table__body"),
        ("tr", "govuk-table__row"), ("th", "govuk-table__header"), ("td", "govuk-table__cell"),
        ("a", "govuk-link"),
    ):
        for element in soup.find_all(tag):
            element["class"] = name
    return str(soup)


# -- blocks to Markdown -------------------------------------------------------------


# The href to write for a rich text link, given its attributes (a page link's
# are ``linktype`` and ``id``); None to keep the href it has.
LinkHref = Callable[[dict], str | None]


def blocks_to_markdown(stream, *, link_href: LinkHref | None = None, warnings=None) -> str:
    """A stream of blocks (a StreamValue, or its raw data) as Markdown."""
    parts = []
    for block_type, value, child in _blocks(stream):
        part = _block_markdown(block_type, value, link_href, warnings)
        if part is None:
            # No Markdown form (cards, a banner): its HTML, which the import
            # keeps as an HTML block.
            if warnings is not None:
                warnings.append(f"a {block_type} block has no Markdown form; written as HTML")
            part = child.render() if child is not None else ""
        if part:
            parts.append(part.strip("\n"))
    return "\n\n".join(parts) + ("\n" if parts else "")


def _blocks(stream):
    for child in stream or []:
        if isinstance(child, dict):
            yield child.get("type"), child.get("value"), None
        else:
            yield child.block_type, child.block.get_prep_value(child.value), child


def _block_markdown(block_type, value, link_href, warnings) -> str | None:
    if block_type == "text":
        return html_to_markdown(_source(value), link_href)
    if block_type == "inset_text":
        return _quote(html_to_markdown(_source(value), link_href), "NOTE")
    if block_type == "warning_text":
        return _quote(html_to_markdown(_source(value), link_href), "WARNING")
    if block_type == "code":
        return f"```{value.get('language') or ''}\n{value.get('code', '')}\n```"
    if block_type == "details":
        text = html_to_markdown(_source(value.get("text")), link_href)
        return f"<details>\n<summary>{html_lib.escape(value.get('summary', ''))}</summary>\n\n{text}\n\n</details>"
    if block_type == "table":
        return _pipe_table(value.get("table") or value)
    if block_type == "raw_html":
        return str(value or "")
    return None


def _source(value) -> str:
    return getattr(value, "source", value) or ""


def _quote(markdown: str, kind: str) -> str:
    lines = [f"> [!{kind}]"] + [f"> {line}" if line else ">" for line in markdown.splitlines()]
    return "\n".join(lines)


def _pipe_table(table: dict) -> str:
    rows = [[str(cell or "").replace("|", "\\|") for cell in row] for row in table.get("data") or []]
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    if table.get("first_row_is_table_header"):
        header, body = rows[0], rows[1:]
    else:
        header, body = [""] * width, rows
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join(["---"] * width) + " |"]
    lines += ["| " + " | ".join(row) + " |" for row in body]
    return "\n".join(lines)


def html_to_markdown(html: str, link_href: LinkHref | None = None) -> str:
    """Wagtail rich text as Markdown: the subset rich text holds."""
    soup = BeautifulSoup(html or "", "html.parser")
    out = []
    for node in soup.contents:
        if isinstance(node, PreformattedString):
            continue  # a comment, which the page does not show
        if isinstance(node, NavigableString):
            if node.strip():
                out.append(node.strip())
            continue
        out.append(_element_markdown(node, link_href))
    return "\n\n".join(part for part in out if part.strip())


def _element_markdown(node: Tag, link_href) -> str:
    if node.name in ("h2", "h3", "h4", "h5", "h6"):
        return "#" * int(node.name[1]) + " " + _inline(node, link_href)
    if node.name in ("ul", "ol"):
        return _list(node, link_href, depth=0)
    if node.name == "hr":
        return "---"
    return _inline(node, link_href)


def _list(node: Tag, link_href, depth: int) -> str:
    lines = []
    for number, item in enumerate(node.find_all("li", recursive=False), start=1):
        marker = f"{number}." if node.name == "ol" else "-"
        nested = [child for child in item.children if isinstance(child, Tag) and child.name in ("ul", "ol")]
        for child in nested:
            child.extract()
        lines.append("  " * depth + f"{marker} {_inline(item, link_href)}")
        for child in nested:
            lines.append(_list(child, link_href, depth + 1))
    return "\n".join(lines)


def _inline(node, link_href) -> str:
    parts = []
    for child in node.children:
        if isinstance(child, NavigableString):
            parts.append(re.sub(r"\s+", " ", str(child)))
            continue
        inner = _inline(child, link_href)
        if child.name in ("b", "strong"):
            parts.append(f"**{inner}**")
        elif child.name in ("i", "em"):
            parts.append(f"_{inner}_")
        elif child.name == "code":
            parts.append(f"`{child.get_text()}`")
        elif child.name == "br":
            parts.append("  \n")
        elif child.name not in ("a", "p", "span", "li"):
            # No Markdown form (``<sup>``, ``<abbr>``): Markdown passes HTML through.
            parts.append(str(child))
        elif child.name == "a":
            href = child.get("href", "")
            if link_href is not None:
                href = link_href(dict(child.attrs)) or href
            parts.append(f"[{inner}]({href})")
        else:
            parts.append(inner)
    return "".join(parts).strip()
