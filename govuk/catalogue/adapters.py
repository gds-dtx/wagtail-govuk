"""Read a source repo's files into a ``GuidanceBundle``.

Two layouts are understood:

- a **manifest repo**, with a ``guidance.yml`` at its root naming the site, its
  navigation and where its Markdown lives. This is the format a catalogue's
  hosted content uses, and a repo can control its own presentation through;
- a **tech-docs repo** (alphagov/tech-docs-template, as The GDS Way is), read
  as it stands: the site from ``config/tech-docs.yml``, the pages from
  ``source/``, and the navigation from the index page's hand-written sidebar.

``files`` maps a path relative to the repo (or the configured directory inside
it) to the file's bytes; files the sync did not need the content of map to
None, and are only there so a link to them can be pointed at GitHub.
"""

import posixpath
import re

import yaml
from bs4 import BeautifulSoup

from . import erb
from .bundle import (
    GuidanceBundle,
    GuidanceSyncError,
    SourceDocument,
    clean_path,
    document_metadata,
    split_frontmatter,
)

MANIFEST_NAMES = ("guidance.yml", "guidance.yaml")
MANIFEST_SCHEMA_PREFIX = "guidance-as-code/"
# Either names the pages in the order they are numbered: a standard's
# requirements, or a method's steps.
SEQUENCE_KEYS = ("requirements", "steps")
TECH_DOCS_CONFIG = "config/tech-docs.yml"
TECH_DOCS_SOURCE = "source/"


def build_bundle(files: dict[str, bytes | None]) -> GuidanceBundle:
    """Read ``files`` with whichever adapter recognises the layout."""
    for name in MANIFEST_NAMES:
        if name in files:
            return ManifestAdapter(files, name).build()
    if TECH_DOCS_CONFIG in files and any(p.startswith(TECH_DOCS_SOURCE) for p in files):
        return TechDocsAdapter(files).build()
    raise GuidanceSyncError(
        "The repository has neither a guidance.yml manifest nor a tech-docs "
        "layout (config/tech-docs.yml and source/) at the configured directory."
    )


def _text(files: dict[str, bytes | None], path: str) -> str | None:
    data = files.get(path)
    if data is None:
        return None
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise GuidanceSyncError(f"{path} is not UTF-8 text.") from exc


def _yaml(text: str, source_name: str) -> dict:
    try:
        value = yaml.safe_load(text) if text.strip() else {}
    except yaml.YAMLError as exc:
        raise GuidanceSyncError(f"{source_name}: invalid YAML: {exc}") from exc
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise GuidanceSyncError(f"{source_name}: expected a YAML mapping.")
    return value


def _first_heading(markdown: str) -> str:
    match = re.search(r"^#\s+(.+?)\s*#*\s*$", markdown, re.M)
    return match.group(1).strip() if match else ""


def _title_from_path(path: str) -> str:
    stem = clean_path(path).rpartition("/")[2] or "Home"
    return stem.replace("-", " ").replace("_", " ").capitalize()


def _navigation_from_html(html: str) -> list[dict]:
    """Groups of links from a hand-written ``<ul>`` sidebar.

    Each top-level item is a group, titled by its own link, and the links in
    its nested list are the group's items (deeper lists are flattened into
    them). Top-level links with no list under them are gathered into one
    untitled group, so nothing the source put in its sidebar goes missing.
    """
    soup = BeautifulSoup(html, "html.parser")
    top = soup.find("ul")
    if top is None:
        return []
    groups: list[dict] = []
    loose: list[dict] = []
    for item in top.find_all("li", recursive=False):
        link = item.find("a", recursive=False)
        nested = item.find("ul")
        title = (link.get_text(" ", strip=True) if link else item.get_text(" ", strip=True))
        if nested is None:
            if link is not None:
                loose.append({"title": title, "href": link.get("href", "")})
            continue
        groups.append(
            {
                "title": title,
                "href": link.get("href", "") if link else "",
                "items": [
                    {"title": a.get_text(" ", strip=True), "href": a.get("href", "")}
                    for a in nested.find_all("a")
                ],
            }
        )
    if loose:
        groups.insert(0, {"title": "", "href": "", "items": loose})
    return groups


class TechDocsAdapter:
    """A tech-docs (Middleman) repo, read without changing anything in it."""

    format = "tech-docs"

    def __init__(self, files: dict[str, bytes | None]):
        self.files = files
        self.warnings: list[str] = []
        self.config = _yaml(_text(files, TECH_DOCS_CONFIG) or "", TECH_DOCS_CONFIG)

    # Middleman resolves ``partial 'partials/nav-x'`` to
    # ``source/partials/_nav-x.html.erb`` (or one of its siblings).
    def load_partial(self, name: str) -> str | None:
        directory, _, stem = name.rpartition("/")
        base = posixpath.join(TECH_DOCS_SOURCE, directory)
        for candidate_stem in (f"_{stem}", stem):
            for suffix in (".html.erb", ".erb", ".html.md.erb", ".html", ".md"):
                text = _text(self.files, posixpath.join(base, candidate_stem + suffix))
                if text is not None:
                    return text
        return None

    def _layout_partials(self) -> list[str]:
        """The partials the default layout wraps every page in.

        For The GDS Way that is the site banner. It is shown once by the page
        template rather than repeated in every page body, so these render as
        nothing where a page calls them itself.
        """
        config_rb = _text(self.files, "config.rb") or ""
        layout = re.search(r"""set\s+:layout\s*,\s*['"]([\w-]+)['"]""", config_rb)
        if not layout:
            return []
        source = _text(self.files, f"{TECH_DOCS_SOURCE}layouts/{layout.group(1)}.erb")
        if not source:
            return []
        return re.findall(r"""partial\s*\(?\s*['"]([^'"]+)['"]""", source)

    def build(self) -> GuidanceBundle:
        banner_partials = self._layout_partials()
        banner_html = ""
        for name in banner_partials:
            source = self.load_partial(name)
            if source:
                translated = erb.translate(
                    source, title="", load_partial=self.load_partial, source_name=name
                )
                self.warnings.extend(translated.warnings)
                banner_html += translated.text

        default_owner = str(self.config.get("default_owner_slack") or "").strip()
        documents: list[SourceDocument] = []
        navigation: list[dict] = []
        sections: list[dict] = []
        seen: dict[str, str] = {}

        for source_path in sorted(self.files):
            if not self._is_page(source_path):
                continue
            relative = source_path[len(TECH_DOCS_SOURCE) :]
            url_path = "/" + re.sub(r"(\.html)?(\.md)?(\.erb)?$", "", relative) + ".html"
            path = clean_path(url_path)
            if path in seen:
                self.warnings.append(
                    f"{source_path}: served at the same address as {seen[path]}; left out"
                )
                continue
            seen[path] = source_path

            frontmatter_yaml, body = split_frontmatter(_text(self.files, source_path) or "")
            frontmatter = _yaml(frontmatter_yaml, source_path)
            title = str(frontmatter.get("title") or "").strip()
            title = title or _first_heading(body) or _title_from_path(relative)
            withdrawn = frontmatter.get("layout") == "withdrawn"

            translated = erb.translate(
                body,
                title=title,
                load_partial=self.load_partial,
                skip_partials=frozenset(banner_partials),
                source_name=source_path,
            )
            self.warnings.extend(translated.warnings)

            metadata = document_metadata(
                {
                    **frontmatter,
                    "owner": frontmatter.get("owner_slack"),
                    "hidden": frontmatter.get("hide_in_navigation"),
                },
                default_owner=default_owner,
                status="withdrawn" if withdrawn else "current",
            )
            sidebar = translated.captures.get("sidebar", "")
            if sidebar:
                groups = _navigation_from_html(sidebar)
                if path == "":
                    navigation = [{**group, "base": url_path} for group in groups]
                else:
                    sections.extend(
                        {**group, "owner_path": path, "base": url_path} for group in groups
                    )

            documents.append(
                SourceDocument(
                    path=path,
                    source_path=source_path,
                    url_path=url_path,
                    title=title,
                    # The withdrawn layout shows the title and a warning, never
                    # the body: withdrawing a page is how it stops being advice.
                    markdown="" if withdrawn else translated.text,
                    metadata=metadata,
                )
            )

        self._fill_withdrawn_lists(documents)
        redirects = {
            str(old): str(new) for old, new in (self.config.get("redirects") or {}).items()
        }
        return GuidanceBundle(
            format=self.format,
            site={
                "title": str(self.config.get("service_name") or "").strip(),
                "description": str(self.config.get("description") or "").strip(),
                "banner_html": banner_html.strip(),
                "owner": {
                    "default": default_owner,
                    "slack_workspace": str(
                        self.config.get("owner_slack_workspace") or ""
                    ).strip(),
                },
                "source_host": str(self.config.get("host") or "").rstrip("/"),
            },
            navigation=navigation,
            sections=sections,
            documents=documents,
            redirects=redirects,
            content_root=TECH_DOCS_SOURCE,
            files=set(self.files),
            anchor_style="tech-docs",
            warnings=self.warnings,
        )

    @staticmethod
    def _is_page(path: str) -> bool:
        if not path.startswith(TECH_DOCS_SOURCE):
            return False
        relative = path[len(TECH_DOCS_SOURCE) :]
        if relative.split("/", 1)[0] in ("partials", "layouts", "javascripts", "stylesheets"):
            return False
        name = relative.rpartition("/")[2]
        return not name.startswith("_") and (
            name.endswith(".html.md.erb") or name.endswith(".html.md") or name.endswith(".md")
        )

    @staticmethod
    def _fill_withdrawn_lists(documents: list[SourceDocument]) -> None:
        withdrawn = [d for d in documents if d.metadata.get("status") == "withdrawn"]
        items = "\n".join(
            f'<li><a href="{d.url_path}">{d.title}</a></li>'
            for d in sorted(withdrawn, key=lambda d: d.title.lower())
        )
        for document in documents:
            if erb.WITHDRAWN_LIST_MARKER in document.markdown:
                document.markdown = document.markdown.replace(erb.WITHDRAWN_LIST_MARKER, items)


class ManifestAdapter:
    """A repo that describes itself in ``guidance.yml``."""

    format = "manifest"

    def __init__(self, files: dict[str, bytes | None], manifest_name: str):
        self.files = files
        self.manifest_name = manifest_name
        self.warnings: list[str] = []
        self.manifest = _yaml(_text(files, manifest_name) or "", manifest_name)

    def build(self) -> GuidanceBundle:
        manifest = self.manifest
        schema = str(manifest.get("schema") or "")
        if not schema.startswith(MANIFEST_SCHEMA_PREFIX):
            raise GuidanceSyncError(
                f"{self.manifest_name}: 'schema' must be "
                f"'{MANIFEST_SCHEMA_PREFIX}v0' (found {schema or 'nothing'})."
            )

        content_dir = clean_dir(str(manifest.get("content_dir") or "."))
        content_root = f"{content_dir}/" if content_dir else ""
        index_name = str(manifest.get("index") or "index.md")
        owner = manifest.get("owner") or {}
        if isinstance(owner, str):
            owner = {"default": owner}
        default_owner = str(owner.get("default") or "").strip()

        documents: list[SourceDocument] = []
        seen: dict[str, str] = {}
        # The named index first, so it wins the site root over a README.
        candidates = sorted(
            (p for p in self.files if self._is_page(p, content_root)),
            key=lambda p: (p != content_root + index_name, p),
        )
        for source_path in candidates:
            relative = source_path[len(content_root) :]
            path = "" if relative == index_name else clean_path(relative)
            if path in seen:
                self.warnings.append(
                    f"{source_path}: served at the same address as {seen[path]}; left out"
                )
                continue
            seen[path] = source_path

            frontmatter_yaml, body = split_frontmatter(_text(self.files, source_path) or "")
            frontmatter = _yaml(frontmatter_yaml, source_path)
            title = str(frontmatter.get("title") or "").strip()
            title = title or _first_heading(body) or _title_from_path(relative)
            status = str(frontmatter.get("status") or "current").strip().lower()
            documents.append(
                SourceDocument(
                    path=path,
                    source_path=source_path,
                    url_path="/" + relative,
                    title=title,
                    markdown="" if status == "withdrawn" else body,
                    metadata=document_metadata(
                        frontmatter, default_owner=default_owner, status=status
                    ),
                )
            )

        if "" not in seen:
            self.warnings.append(
                f"{self.manifest_name}: no index page ({content_root}{index_name}) found"
            )

        return GuidanceBundle(
            format=self.format,
            site={
                "title": str(manifest.get("title") or "").strip(),
                "description": str(manifest.get("description") or "").strip(),
                "banner_markdown": str(manifest.get("banner") or "").strip(),
                "owner": {
                    "default": default_owner,
                    "slack_workspace": str(owner.get("slack_workspace") or "").strip(),
                },
                "source_host": str(manifest.get("source_host") or "").rstrip("/"),
            },
            navigation=self._groups(manifest.get("navigation") or [], "navigation"),
            sections=self._sections(manifest.get("sections") or [], index_name),
            documents=documents,
            redirects={
                str(old): str(new)
                for old, new in (manifest.get("redirects") or {}).items()
            },
            content_root=content_root,
            files=set(self.files),
            anchor_style=str(manifest.get("anchors") or "github"),
            warnings=self.warnings,
            sequence=self._sequence(manifest),
        )

    def _sequence(self, manifest: dict) -> list[str]:
        """The ``requirements:`` or ``steps:`` list: pages in numbered order."""
        for key in SEQUENCE_KEYS:
            pages = manifest.get(key)
            if pages is None:
                continue
            if not isinstance(pages, list) or not all(isinstance(p, str) for p in pages):
                self.warnings.append(f"{self.manifest_name}: '{key}' must be a list of pages")
                return []
            return ["/" + page.lstrip("/") for page in pages]
        return []

    def _groups(self, entries, key: str) -> list[dict]:
        if not isinstance(entries, list):
            self.warnings.append(f"{self.manifest_name}: '{key}' must be a list")
            return []
        groups = []
        for entry in entries:
            if not isinstance(entry, dict):
                self.warnings.append(f"{self.manifest_name}: each '{key}' entry needs a title")
                continue
            groups.append(self._group(entry))
        return groups

    def _group(self, entry: dict) -> dict:
        return {
            "title": str(entry.get("title") or ""),
            "href": "",
            "items": self._items(entry.get("pages") or []),
            "base": "/",
        }

    def _items(self, pages) -> list[dict]:
        """Navigation items; a page may list its own ``pages`` beneath it."""
        items = []
        for page in pages if isinstance(pages, list) else []:
            if isinstance(page, str):
                page = {"path": page}
            if not isinstance(page, dict) or not (page.get("path") or page.get("url")):
                self.warnings.append(f"{self.manifest_name}: a navigation item needs a path or url")
                continue
            items.append(
                {
                    "title": str(page.get("title") or ""),
                    # Manifest paths are relative to content_dir, which the
                    # renderer resolves them against as if from the root.
                    "href": str(page.get("url") or "/" + str(page["path"]).lstrip("/")),
                    "children": self._items(page.get("pages") or []),
                }
            )
        return items

    def _sections(self, entries, index_name: str) -> list[dict]:
        """Section sidebars: a page's own list of related pages.

        Shown above the main navigation on that page and on the pages it lists
        -- the manifest form of a tech-docs page's ``content_for :sidebar``.
        """
        sections = []
        for entry in entries if isinstance(entries, list) else []:
            owner = str(entry.get("page") or "").lstrip("/") if isinstance(entry, dict) else ""
            if not owner:
                self.warnings.append(f"{self.manifest_name}: each section needs a 'page'")
                continue
            owner_path = "" if owner == index_name else clean_path(owner)
            sections.append({**self._group(entry), "owner_path": owner_path})
        return sections

    @staticmethod
    def _is_page(path: str, content_root: str) -> bool:
        if not path.startswith(content_root) or not path.endswith(".md"):
            return False
        relative = path[len(content_root) :]
        return not any(part.startswith((".", "_")) for part in relative.split("/"))


def clean_dir(value: str) -> str:
    """A repo-relative directory with no leading, trailing or ``..`` parts."""
    value = posixpath.normpath("/" + value.strip()).strip("/")
    return "" if value in ("", ".") else value
