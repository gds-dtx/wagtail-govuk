"""Render a ``GuidanceBundle`` into the HTML and JSON the site stores.

Each page goes Markdown -> HTML -> sanitised -> GOV.UK-styled, in that order:

1. ``markdown-it-py`` in CommonMark mode with tables, which is the nearest
   Python match to the Redcarpet options tech-docs uses (fenced code, tables,
   raw HTML, no autolinking or footnotes);
2. ``nh3`` strips everything not on a short allow-list, including every
   ``class`` and ``style`` the source wrote. The source is somebody else's
   repo, so nothing in it is trusted, and the site's CSP would block inline
   styles anyway;
3. the result is then dressed in GOV.UK Frontend classes, given heading
   anchors, and has its links pointed at this site's copies of the pages.

Only step 3 adds markup, and it adds only what this code chose to, so the
stored HTML is safe to output as-is.

Links to other pages in the same entry are stored as ``guidance:/<path>/``
and expanded to the entry's URL when served (``ContentMainPage.expand_links``),
so moving the page, or the entry to another group, does not need a re-sync.
"""

import copy
import html as html_lib
import re
import unicodedata
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

import nh3
from bs4 import BeautifulSoup, NavigableString
from markdown_it import MarkdownIt

from .bundle import GuidanceBundle, clean_path
from .repository import RepositoryRef

INTERNAL_LINK_PREFIX = "guidance:/"

# Recorded on every snapshot. Raise it whenever a change here (or in the
# adapters) changes what a sync stores, so the next sync re-renders a repo
# whose commit has not moved instead of keeping a copy made by older code.
RENDER_VERSION = 2

_markdown = MarkdownIt("commonmark", {"html": True}).enable("table")

_SVG_TAGS = {"svg", "g", "path", "rect", "circle", "ellipse", "line", "polyline",
             "polygon", "text", "tspan", "title", "desc", "defs", "clippath", "clipPath"}
_SVG_ATTRIBUTES = {"viewbox", "width", "height", "fill", "fill-rule", "fill-opacity",
                   "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin",
                   "stroke-miterlimit", "stroke-opacity", "clip-rule", "clip-path", "d",
                   "x", "y", "x1", "y1", "x2", "y2", "cx", "cy", "r", "rx", "ry",
                   "points", "transform", "text-anchor", "font-size", "font-family",
                   "font-weight", "opacity", "role", "aria-label", "aria-hidden",
                   "focusable", "version", "xmlns", "id", "viewBox"}
_ALLOWED_TAGS = {
    "p", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "a", "strong", "em",
    "b", "i", "code", "pre", "blockquote", "hr", "br", "table", "thead", "tbody",
    "tr", "th", "td", "abbr", "div", "span", "img", "dl", "dt", "dd", "sup", "sub",
    "del", "ins", "kbd", "details", "summary", "figure", "figcaption",
} | _SVG_TAGS
_ALLOWED_ATTRIBUTES = {
    "*": {"id", "title", "lang", "dir"},
    "a": {"href"},
    "img": {"src", "alt", "width", "height"},
    "abbr": {"title"},
    "th": {"colspan", "rowspan", "scope", "align"},
    "td": {"colspan", "rowspan", "align"},
    "ol": {"start"},
    # The marker the ERB translation leaves for a ``warning_text`` helper.
    "div": {"data-guidance-component"},
    **{tag: _SVG_ATTRIBUTES for tag in _SVG_TAGS},
}

_HEADING_CLASSES = {
    "h1": "govuk-heading-xl",
    "h2": "govuk-heading-l",
    "h3": "govuk-heading-m",
    "h4": "govuk-heading-s",
    "h5": "govuk-heading-s",
    "h6": "govuk-heading-s",
}
_ALERT = re.compile(r"^\s*\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*", re.I)


@dataclass
class RenderedDocument:
    path: str
    source_path: str
    title: str
    markdown: str
    html: str
    headings: list[dict]
    metadata: dict


@dataclass
class RenderedBundle:
    site: dict
    documents: list[RenderedDocument]
    warnings: list[str] = field(default_factory=list)


def sanitise(html: str) -> str:
    return nh3.clean(
        html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRIBUTES,
        url_schemes={"http", "https", "mailto"},
        link_rel=None,
        strip_comments=True,
    )


class LinkResolver:
    """Point an href from the source at this site, GitHub, or leave it be."""

    def __init__(self, bundle: GuidanceBundle, repository: RepositoryRef):
        self.bundle = bundle
        self.repository = repository
        self.paths = {document.path for document in bundle.documents}
        self.source_host = urlsplit(bundle.site.get("source_host") or "").netloc.lower()
        self.warnings: list[str] = []
        # The source's own redirects, so a link to a page's old address goes
        # where the source's site would have sent it.
        self.redirects: dict[str, dict] = {}
        for old, new in bundle.redirects.items():
            found = self._lookup(new, "/")
            if found is not None and clean_path(old) not in self.paths:
                self.redirects[clean_path(old)] = found

    def _lookup(self, href: str, base_url_path: str) -> dict | None:
        resolved = urlsplit(urljoin("https://source.invalid" + base_url_path, href))
        path = clean_path(resolved.path)
        if path in self.paths:
            return {"path": path, "fragment": resolved.fragment}
        return None

    def target(self, href: str, base_url_path: str) -> dict | None:
        """``{"path", "fragment"}`` for one of the guidance's own pages, else None."""
        href = (href or "").strip()
        if not href:
            return None
        parts = urlsplit(href)
        if parts.scheme or parts.netloc:
            if not self.source_host or parts.netloc.lower() != self.source_host:
                return None
            href = parts.path + (f"#{parts.fragment}" if parts.fragment else "")
        found = self._lookup(href, base_url_path)
        if found is not None:
            return found
        resolved = urlsplit(urljoin("https://source.invalid" + base_url_path, href))
        redirected = self.redirects.get(clean_path(resolved.path))
        if redirected is not None:
            return {**redirected, "fragment": resolved.fragment or redirected["fragment"]}
        return None

    def href(self, href: str, base_url_path: str, *, source_name: str) -> str:
        """The href to store for a link written as ``href`` in the source."""
        if not href or href.startswith("#"):
            return href
        found = self.target(href, base_url_path)
        if found is not None:
            return internal_href(found["path"], found["fragment"])
        parts = urlsplit(href)
        if parts.scheme or parts.netloc:
            return href
        # Not a page: perhaps a file in the repo (The GDS Way links to its
        # editorconfig), which is best shown on GitHub.
        resolved = urlsplit(urljoin("https://source.invalid" + base_url_path, href))
        file_path = self.bundle.content_root + resolved.path.lstrip("/")
        if file_path in self.bundle.files:
            return self.repository.blob_url(file_path)
        self.warnings.append(f"{source_name}: link to '{href}' matches no page or file")
        return href

    def src(self, src: str, base_url_path: str) -> str:
        """Images are served straight from the repo at the synced commit."""
        parts = urlsplit(src or "")
        if not src or parts.scheme or parts.netloc:
            return src
        resolved = urlsplit(urljoin("https://source.invalid" + base_url_path, src))
        return self.repository.raw_url(self.bundle.content_root + resolved.path.lstrip("/"))


def internal_href(path: str, fragment: str = "") -> str:
    value = f"{INTERNAL_LINK_PREFIX}{path}/" if path else INTERNAL_LINK_PREFIX
    return f"{value}#{fragment}" if fragment else value


class _TechDocsAnchors:
    """tech-docs' heading ids, so links into the source's pages still land.

    A port of tech-docs-gem's ``UniqueIdentifierGenerator``: Redcarpet's slug
    of the heading's escaped HTML, then the nearest higher heading's id as a
    prefix if that is taken, then a number.
    """

    def __init__(self):
        self.anchors: list[tuple[str, int]] = []

    @staticmethod
    def slug(text: str) -> str:
        escaped = (
            text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&#39;")
        )
        value = re.sub(r"[^0-9a-z]+", "-", escaped.lower().strip())
        return value.strip("-")

    def create(self, text: str, level: int) -> str:
        anchor = self.slug(text)
        if not self._unique(anchor):
            parent = next((a for a, lvl in reversed(self.anchors) if lvl < level), None)
            if parent is not None:
                anchor = f"{parent}-{anchor}"
        if not self._unique(anchor):
            number = 2
            while not self._unique(f"{anchor}-{number}"):
                number += 1
            anchor = f"{anchor}-{number}"
        self.anchors.append((anchor, level))
        return anchor

    def _unique(self, value: str) -> bool:
        return all(anchor != value for anchor, _ in self.anchors)


class _GithubAnchors:
    """GitHub's heading ids, so a manifest repo's links work there and here."""

    def __init__(self):
        self.counts: dict[str, int] = {}

    def create(self, text: str, level: int) -> str:
        value = unicodedata.normalize("NFKC", text).strip().lower()
        value = re.sub(r"[^\w\- ]", "", value).replace(" ", "-")
        count = self.counts.get(value, 0)
        self.counts[value] = count + 1
        return f"{value}-{count}" if count else value


def render_markdown(markdown: str) -> str:
    """Markdown to sanitised (but not yet styled) HTML."""
    return sanitise(_markdown.render(markdown))


def _add_class(tag, *classes: str) -> None:
    existing = tag.get("class") or []
    tag["class"] = [*existing, *(c for c in classes if c not in existing)]


def _warning_text(soup, content_nodes) -> object:
    wrapper = soup.new_tag("div", attrs={"class": "govuk-warning-text"})
    icon = soup.new_tag(
        "span", attrs={"class": "govuk-warning-text__icon", "aria-hidden": "true"}
    )
    icon.string = "!"
    text = soup.new_tag("strong", attrs={"class": "govuk-warning-text__text"})
    hidden = soup.new_tag("span", attrs={"class": "govuk-visually-hidden"})
    hidden.string = "Warning"
    text.append(hidden)
    text.append(" ")
    for node in content_nodes:
        text.append(node)
    wrapper.append(icon)
    wrapper.append(text)
    return wrapper


def style_html(
    html: str,
    *,
    resolver: LinkResolver | None,
    base_url_path: str,
    source_name: str,
    anchors=None,
    drop_first_h1: bool = False,
) -> tuple[str, list[dict]]:
    """Dress sanitised HTML in GOV.UK classes; return it and its h2/h3 outline."""
    soup = BeautifulSoup(html, "html.parser")
    headings: list[dict] = []

    if drop_first_h1:
        first = soup.find(["h1", "h2", "h3", "h4", "h5", "h6", "p", "ul", "ol", "table"])
        if first is not None and first.name == "h1":
            if anchors is not None:
                anchors.create(first.get_text(), 1)
            first.decompose()

    for tag in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        level = int(tag.name[1])
        text = tag.get_text()
        if anchors is not None:
            tag["id"] = anchors.create(text, level)
        _add_class(tag, _HEADING_CLASSES[tag.name])
        if level in (2, 3) and tag.get("id"):
            headings.append({"level": level, "text": text.strip(), "anchor": tag["id"]})

    for tag in soup.find_all("div", attrs={"data-guidance-component": "warning"}):
        tag.replace_with(_warning_text(soup, list(tag.contents)))

    for quote in soup.find_all("blockquote"):
        first = quote.find("p")
        alert = _ALERT.match(first.get_text()) if first is not None else None
        if alert and alert.group(1).upper() in ("WARNING", "CAUTION", "IMPORTANT"):
            _strip_alert_marker(first)
            nodes = []
            for child in list(quote.children):
                if getattr(child, "name", None) == "p":
                    if nodes:
                        nodes.append(soup.new_tag("br"))
                    nodes.extend(list(child.contents))
                elif not isinstance(child, NavigableString):
                    nodes.append(child)
            quote.replace_with(_warning_text(soup, nodes))
            continue
        if alert:
            _strip_alert_marker(first)
        quote.name = "div"
        _add_class(quote, "govuk-inset-text")

    for tag in soup.find_all("p"):
        if "govuk-warning-text__text" not in (tag.parent.get("class") or []):
            _add_class(tag, "govuk-body")
    for tag in soup.find_all("ul"):
        _add_class(tag, "govuk-list", "govuk-list--bullet")
    for tag in soup.find_all("ol"):
        _add_class(tag, "govuk-list", "govuk-list--number")
    for tag in soup.find_all("hr"):
        _add_class(
            tag, "govuk-section-break", "govuk-section-break--l", "govuk-section-break--visible"
        )
    for tag in soup.find_all("pre"):
        _add_class(tag, "app-guidance__code")
        tag["tabindex"] = "0"
    for tag in soup.find_all("svg"):
        _add_class(tag, "app-guidance__figure")
        tag.attrs.pop("width", None)
        tag.attrs.pop("height", None)

    for table in soup.find_all("table"):
        _style_table(soup, table)
    for details in soup.find_all("details"):
        _style_details(soup, details)

    for link in soup.find_all("a"):
        _add_class(link, "govuk-link")
        if resolver is not None and link.get("href"):
            link["href"] = resolver.href(link["href"], base_url_path, source_name=source_name)
    for image in soup.find_all("img"):
        if resolver is not None and image.get("src"):
            image["src"] = resolver.src(image["src"], base_url_path)
        _add_class(image, "app-guidance__image")

    return str(soup), headings


def _style_details(soup, details) -> None:
    """A source's ``<details>`` as the GOV.UK details component."""
    _add_class(details, "govuk-details")
    summary = details.find("summary", recursive=False)
    if summary is None:
        summary = soup.new_tag("summary")
        summary.string = "More information"
        details.insert(0, summary)
    _add_class(summary, "govuk-details__summary")
    text = soup.new_tag("span", attrs={"class": "govuk-details__summary-text"})
    for child in list(summary.contents):
        text.append(child.extract())
    summary.append(text)
    body = soup.new_tag("div", attrs={"class": "govuk-details__text"})
    for child in list(details.contents):
        if child is not summary:
            body.append(child.extract())
    details.append(body)


def _strip_alert_marker(paragraph) -> None:
    for node in paragraph.descendants:
        if isinstance(node, NavigableString) and node.strip():
            node.replace_with(_ALERT.sub("", str(node), count=1))
            return


def _style_table(soup, table) -> None:
    _add_class(table, "govuk-table")
    for tag in table.find_all("thead"):
        _add_class(tag, "govuk-table__head")
    for tag in table.find_all("tbody"):
        _add_class(tag, "govuk-table__body")
    for tag in table.find_all("tr"):
        _add_class(tag, "govuk-table__row")
    for tag in table.find_all("td"):
        # tech-docs' row headings: a body cell that starts with "#".
        first = tag.contents[0] if tag.contents else None
        if isinstance(first, NavigableString) and str(first).startswith("#"):
            tag.name = "th"
            tag["scope"] = "row"
            first.replace_with(re.sub(r"^#\s*", "", str(first)))
    for tag in table.find_all("th"):
        _add_class(tag, "govuk-table__header")
    for tag in table.find_all("td"):
        _add_class(tag, "govuk-table__cell")
    wrapper = soup.new_tag("div", attrs={"class": "app-guidance__table"})
    table.wrap(wrapper)


def render_bundle(bundle: GuidanceBundle, repository: RepositoryRef) -> RenderedBundle:
    """Render every document, and resolve the navigation and redirects."""
    resolver = LinkResolver(bundle, repository)
    anchor_class = _TechDocsAnchors if bundle.anchor_style == "tech-docs" else _GithubAnchors
    documents = []
    for document in bundle.documents:
        html, headings = style_html(
            render_markdown(document.markdown),
            resolver=resolver,
            base_url_path=document.url_path,
            source_name=document.source_path,
            anchors=anchor_class(),
            drop_first_h1=True,
        )
        documents.append(
            RenderedDocument(
                path=document.path,
                source_path=document.source_path,
                title=document.title,
                markdown=document.markdown.strip(),
                html=html,
                headings=headings,
                metadata=document.metadata,
            )
        )

    titles = {document.path: document.title for document in bundle.documents}
    site = {
        **{k: v for k, v in bundle.site.items() if k not in ("banner_html", "banner_markdown")},
        "format": bundle.format,
        "render_version": RENDER_VERSION,
        "banner_html": _banner(bundle, resolver),
        **_navigation(bundle, resolver, titles),
        "redirects": _redirects(bundle, resolver),
        "sequence": _sequence(bundle, resolver),
    }
    return RenderedBundle(
        site=site,
        documents=documents,
        warnings=[*bundle.warnings, *resolver.warnings],
    )


def _sequence(bundle: GuidanceBundle, resolver: LinkResolver) -> list[str]:
    """The numbered pages' paths, in order; a listed page that is missing is reported."""
    paths = []
    for href in bundle.sequence:
        found = resolver.target(href, "/")
        if found is None or found["path"] in paths:
            resolver.warnings.append(f"numbered page '{href}' matches no page; left out")
            continue
        paths.append(found["path"])
    return paths


def _banner(bundle: GuidanceBundle, resolver: LinkResolver) -> str:
    if bundle.site.get("banner_markdown"):
        raw = render_markdown(bundle.site["banner_markdown"])
    elif bundle.site.get("banner_html"):
        # The tech-docs banner is a partial wrapped in its own styled div;
        # only its words are wanted, in the template's own banner markup.
        raw = sanitise(bundle.site["banner_html"])
        soup = BeautifulSoup(raw, "html.parser")
        for div in soup.find_all("div"):
            div.unwrap()
        raw = str(soup)
    else:
        return ""
    html, _ = style_html(raw, resolver=resolver, base_url_path="/", source_name="banner")
    return html.strip()


def _navigation(bundle: GuidanceBundle, resolver: LinkResolver, titles: dict) -> dict:
    """The side navigation as a tree, and any sections that did not fit in it.

    Each item is ``{"title", "path", "fragment", "url", "children"}``. Two
    rules shape it:

    - a link to a heading on the index page, or on the page an item sits
      under, is in-page navigation rather than site navigation, so it is left
      out (the page's own contents list covers it). For The GDS Way that is
      the index sidebar's first group, which then has nothing left and goes;
    - a section -- a page's own sidebar list -- becomes the children of that
      page's entry in the navigation, so the sidebar keeps its shape and opens
      in place. A section whose page is not in the navigation is kept apart in
      ``sections`` and shown on its own pages, as before.
    """
    navigation = []
    for group in bundle.navigation:
        items = _navigation_items(
            group.get("items", []), group.get("base") or "/", resolver, titles,
            group_title=group.get("title", ""), parent_path="",
        )
        navigation.append({"title": html_lib.unescape(group.get("title", "")), "items": items})

    sections = []
    for section in bundle.sections:
        owner = section["owner_path"]
        items = _navigation_items(
            section.get("items", []), section.get("base") or "/", resolver, titles,
            group_title=section.get("title", ""), parent_path=owner,
        )
        hosts = [
            item for item in _walk(navigation)
            if item["path"] == owner and not item["fragment"] and not item["children"]
        ]
        for host in hosts:
            host["children"] = copy.deepcopy(items)
        if not hosts and items:
            sections.append(
                {"title": html_lib.unescape(section.get("title", "")), "items": items, "owner_path": owner}
            )

    return {
        "navigation": [group for group in navigation if group["items"]],
        "sections": sections,
    }


def _navigation_items(
    raw_items, base: str, resolver: LinkResolver, titles: dict, *, group_title: str, parent_path: str
) -> list[dict]:
    items = []
    for item in raw_items:
        href = item.get("href", "")
        found = resolver.target(href, base)
        if found is not None:
            if found["fragment"] and found["path"] in ("", parent_path):
                continue
            items.append(
                {
                    "title": item.get("title") or titles.get(found["path"], ""),
                    "path": found["path"],
                    "fragment": found["fragment"],
                    "url": "",
                    "children": _navigation_items(
                        item.get("children", []), base, resolver, titles,
                        group_title=group_title, parent_path=found["path"],
                    ),
                }
            )
        elif urlsplit(href).scheme in ("http", "https"):
            items.append(
                {
                    "title": item.get("title") or href,
                    "path": None,
                    "fragment": "",
                    "url": href,
                    "children": [],
                }
            )
        elif not href.startswith("#"):
            resolver.warnings.append(
                f"navigation '{group_title}': '{href}' matches no page; left out"
            )
    return items


def _walk(groups: list[dict]):
    """Every item in a navigation tree, parents before their children."""
    stack = [item for group in reversed(groups) for item in reversed(group["items"])]
    while stack:
        item = stack.pop()
        yield item
        stack.extend(reversed(item["children"]))


def _redirects(bundle: GuidanceBundle, resolver: LinkResolver) -> dict:
    for old, new in bundle.redirects.items():
        if clean_path(old) not in resolver.redirects:
            resolver.warnings.append(f"redirect '{old}' -> '{new}' left out")
    return dict(resolver.redirects)
