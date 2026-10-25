"""Write a synced guidance collection back out as a manifest repo.

The output is the guidance-as-code format (``guidance.yml`` plus plain
Markdown with frontmatter), whatever format it was synced from. For a tech-docs
repo that is the conversion: no ERB, the navigation in one manifest rather than
hand-written HTML partials, and the same addresses and anchors, so syncing the
output gives the same site as syncing the original.
"""

import re
from pathlib import Path

import yaml
from bs4 import BeautifulSoup

from .adapters import MANIFEST_SCHEMA_PREFIX

INDEX_FILE = "index.md"

_WARNING_MARKER = re.compile(
    r'<div data-guidance-component="warning">(?P<text>.*?)</div>', re.S
)


def document_file(document) -> str:
    """Where a document goes in the manifest repo."""
    if document.path == "":
        return INDEX_FILE
    name = document.source_path.rpartition("/")[2]
    if name.startswith("index."):
        return f"{document.path}/{INDEX_FILE}"
    return f"{document.path}.md"


def _frontmatter(document, default_owner: str) -> dict:
    metadata = document.metadata or {}
    values = {"title": document.title}
    for key in ("description", "last_reviewed_on", "review_in"):
        if metadata.get(key):
            values[key] = metadata[key]
    if metadata.get("owner") and metadata["owner"] != default_owner:
        values["owner"] = metadata["owner"]
    if metadata.get("status") and metadata["status"] != "current":
        values["status"] = metadata["status"]
    if metadata.get("hidden"):
        values["hidden"] = True
    return values


def _markdown(document) -> str:
    """The body, with the ``warning_text`` markers as GitHub alerts."""

    def alert(match):
        lines = match.group("text").strip().splitlines() or [""]
        return "> [!WARNING]\n" + "\n".join(f"> {line.strip()}" for line in lines)

    body = _WARNING_MARKER.sub(alert, document.markdown or "")
    if document.metadata.get("status") == "withdrawn" and not body.strip():
        body = f"# {document.title}\n"
    return body.rstrip() + "\n"


def _item(item: dict, files: dict) -> dict | None:
    if item.get("url"):
        return {"title": item["title"], "url": item["url"]}
    path = item.get("path")
    if path not in files:
        return None
    target = files[path] + (f"#{item['fragment']}" if item.get("fragment") else "")
    exported = {"title": item["title"], "path": target}
    children = _items(item.get("children", []), files)
    if children:
        exported["pages"] = children
    return exported


def _items(items: list[dict], files: dict) -> list[dict]:
    return [i for i in (_item(item, files) for item in items) if i]


def _banner_markdown(html: str) -> str:
    """The banner's words as Markdown, keeping its bold, italics and links."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(["strong", "b"]):
        tag.replace_with(f"**{tag.get_text()}**")
    for tag in soup.find_all(["em", "i"]):
        tag.replace_with(f"_{tag.get_text()}_")
    for tag in soup.find_all("a"):
        tag.replace_with(f"[{tag.get_text()}]({tag.get('href', '')})")
    return soup.get_text(" ", strip=True)


def manifest_for(snapshot, files: dict) -> dict:
    site = snapshot.site or {}
    owner = site.get("owner") or {}
    banner = _banner_markdown(site.get("banner_html") or "")

    def groups(entries):
        return [
            {
                "title": group.get("title", ""),
                "pages": _items(group["items"], files),
            }
            for group in entries
        ]

    manifest = {
        "schema": f"{MANIFEST_SCHEMA_PREFIX}v0",
        "title": site.get("title", ""),
        "description": site.get("description", ""),
        "banner": banner,
        "owner": {
            "default": owner.get("default", ""),
            "slack_workspace": owner.get("slack_workspace", ""),
        },
        "source_host": site.get("source_host", ""),
        # The source's anchors, so links into its pages still land.
        "anchors": "tech-docs" if site.get("format") == "tech-docs" else "github",
        "content_dir": ".",
        "index": INDEX_FILE,
        "navigation": groups(site.get("navigation", [])),
        "sections": [
            {"page": files[section["owner_path"]], **group}
            for section, group in zip(site.get("sections", []), groups(site.get("sections", [])))
            if section.get("owner_path") in files
        ],
        "redirects": {
            f"{old}.html": files[target["path"]]
            + (f"#{target['fragment']}" if target.get("fragment") else "")
            for old, target in (site.get("redirects") or {}).items()
            if target.get("path") in files
        },
    }
    return {key: value for key, value in manifest.items() if value not in ("", [], {})}


def export_snapshot(snapshot, out_dir: Path) -> int:
    """Write ``snapshot`` to ``out_dir``; returns the number of pages written."""
    documents = list(snapshot.documents.all())
    files = {document.path: document_file(document) for document in documents}
    default_owner = ((snapshot.site or {}).get("owner") or {}).get("default", "")

    for document in documents:
        target = out_dir / files[document.path]
        target.parent.mkdir(parents=True, exist_ok=True)
        frontmatter = yaml.safe_dump(
            _frontmatter(document, default_owner), sort_keys=False, allow_unicode=True
        )
        target.write_text(f"---\n{frontmatter}---\n\n{_markdown(document)}", encoding="utf-8")

    manifest = yaml.safe_dump(
        manifest_for(snapshot, files), sort_keys=False, allow_unicode=True, width=100
    )
    (out_dir / "guidance.yml").write_text(manifest, encoding="utf-8")
    return len(documents)
