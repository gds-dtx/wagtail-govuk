"""Read a content catalogue repo: its vocabularies and every entry.

The catalogue repo has its own validator (``scripts/validate.py``, run on every
pull request). This checks only what the site depends on, so a bad entry that
got through is skipped and reported on the sync rather than breaking the
catalogue: required fields, values from the vocabularies, https links, and a
content directory for every hosted entry.

Entries are left out, without a warning, when they are drafts or their type has
no group (a blog): the catalogue lists them, but the site does not.
"""

import posixpath
import re
from dataclasses import dataclass, field

import yaml

from .bundle import GuidanceSyncError

CATALOGUE_FILE = "catalogue.yml"
CATALOGUE_SCHEMA_PREFIX = "guidance-catalogue/"
MANIFEST_FILE = "guidance.yml"
LAYOUTS = ("requirements", "policy", "guide", "steps", "tool", "media")
# The layouts whose entries are pages on the site, from hosted or repo content.
CONTENT_LAYOUTS = ("requirements", "policy", "guide", "steps")
REQUIRED_FIELDS = (
    "id", "title", "short_description", "long_description",
    "type", "scope", "tags", "professions", "owner", "source",
)
_ID = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_REPOSITORY = re.compile(r"^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$")


@dataclass
class EntryRecord:
    """One listed entry, normalised. ``has_pages`` decides card versus link."""

    id: str
    title: str
    short_description: str
    long_description: str
    type: str
    group: str
    layout: str
    primary: bool
    status: str
    scope: str
    tags: list[str]
    professions: list[str]
    owner: dict
    applies_to: str
    related: list[str]
    source: dict
    version: str
    links: list[dict]
    has_pages: bool
    external_url: str
    position: int


@dataclass
class Catalogue:
    site: dict
    vocabularies: dict
    entries: list[EntryRecord] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    unlisted: int = 0


def _text(files: dict[str, bytes | None], path: str) -> str | None:
    data = files.get(path)
    if data is None:
        return None
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise GuidanceSyncError(f"{path} is not UTF-8 text.") from exc


def _yaml(files, path: str) -> dict:
    text = _text(files, path)
    if text is None:
        raise GuidanceSyncError(f"The catalogue has no {path}.")
    try:
        value = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise GuidanceSyncError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(value, dict):
        raise GuidanceSyncError(f"{path}: expected a YAML mapping.")
    return value


def _vocabulary(raw, name: str) -> dict[str, dict]:
    values = raw.get(name) or {}
    if not isinstance(values, dict) or not values:
        raise GuidanceSyncError(f"vocabularies: '{name}' must list at least one value.")
    return {
        str(key): {**value, "label": str(value.get("label") or key)}
        if isinstance(value, dict)
        else {"label": str(key)}
        for key, value in values.items()
    }


def read_catalogue(files: dict[str, bytes | None]) -> Catalogue:
    """The catalogue in ``files`` (the repo's files, by path from its root)."""
    catalogue = _yaml(files, CATALOGUE_FILE)
    schema = str(catalogue.get("schema") or "")
    if not schema.startswith(CATALOGUE_SCHEMA_PREFIX):
        raise GuidanceSyncError(
            f"{CATALOGUE_FILE}: 'schema' must be '{CATALOGUE_SCHEMA_PREFIX}v0' "
            f"(found {schema or 'nothing'})."
        )
    raw = _yaml(files, str(catalogue.get("vocabularies") or "vocabularies.yml"))
    vocabularies = {
        name: _vocabulary(raw, name)
        for name in ("groups", "types", "scope", "professions", "tags")
    }
    for key, value in vocabularies["types"].items():
        if value.get("layout") not in LAYOUTS:
            raise GuidanceSyncError(f"vocabularies: types.{key} has no known layout.")
        if value.get("group") and value["group"] not in vocabularies["groups"]:
            raise GuidanceSyncError(f"vocabularies: types.{key} names an unknown group.")

    result = Catalogue(
        site={
            "title": str(catalogue.get("title") or "").strip(),
            "short_description": str(catalogue.get("short_description") or "").strip(),
            "long_description": str(catalogue.get("long_description") or "").strip(),
        },
        vocabularies=vocabularies,
    )
    entries_dir = str(catalogue.get("entries") or "entries/").strip("/")
    result.site["entries_dir"] = entries_dir
    paths = sorted(
        path for path in files
        if posixpath.dirname(path) == entries_dir and path.endswith((".yml", ".yaml"))
    )
    for path in paths:
        try:
            record = _entry(files, path, vocabularies, position=len(result.entries))
        except _Skip as skip:
            if skip.reason:
                result.warnings.append(f"{path}: {skip.reason}; left out")
            else:
                result.unlisted += 1
            continue
        result.entries.append(record)

    known = {entry.id for entry in result.entries}
    for entry in result.entries:
        entry.related = [related for related in entry.related if related in known]
    return result


class _Skip(Exception):
    """An entry the site does not list. A reason means it was a mistake."""

    def __init__(self, reason: str = ""):
        super().__init__(reason)
        self.reason = reason


def _strings(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str | int)]


def _entry(files, path: str, vocabularies: dict, *, position: int) -> EntryRecord:
    try:
        raw = yaml.safe_load(_text(files, path) or "")
    except yaml.YAMLError as exc:
        raise _Skip(f"invalid YAML ({exc})") from exc
    if not isinstance(raw, dict):
        raise _Skip("not a YAML mapping")
    missing = [name for name in REQUIRED_FIELDS if not raw.get(name)]
    if missing:
        raise _Skip(f"missing {', '.join(missing)}")

    entry_id = str(raw["id"])
    stem = posixpath.splitext(posixpath.basename(path))[0]
    if entry_id != stem or not _ID.match(entry_id):
        raise _Skip(f"id '{entry_id}' must be lower case and hyphens, and match the file name")

    status = str(raw.get("status") or "current")
    entry_type = vocabularies["types"].get(str(raw["type"]))
    if entry_type is None:
        raise _Skip(f"type '{raw['type']}' is not in the vocabularies")
    if status == "draft" or not entry_type.get("group"):
        raise _Skip()
    if str(raw["scope"]) not in vocabularies["scope"]:
        raise _Skip(f"scope '{raw['scope']}' is not in the vocabularies")

    tags = [tag for tag in _strings(raw["tags"]) if tag in vocabularies["tags"]]
    professions = [p for p in _strings(raw["professions"]) if p in vocabularies["professions"]]
    if not tags or not professions:
        raise _Skip("needs at least one known tag and profession")

    owner = raw["owner"]
    if not isinstance(owner, dict) or not owner.get("organisation"):
        raise _Skip("owner needs an organisation")

    layout = entry_type["layout"]
    source = raw["source"]
    if not isinstance(source, dict):
        raise _Skip("source must be a mapping")
    source = {key: str(value) for key, value in source.items() if value is not None}
    source_type = source.get("type")
    links = _links(raw.get("links"), layout)
    if source_type == "external":
        if not source.get("path", "").startswith("https://"):
            raise _Skip("an external source's path must be an https:// address")
        has_pages = bool(links)
    elif source_type == "repo":
        if not _REPOSITORY.match(source.get("path", "")):
            raise _Skip("a repo source's path must be owner/name")
        has_pages = True
    elif source_type == "hosted":
        directory = source.get("path", "").strip("/")
        if not directory.startswith("content/") or ".." in directory.split("/"):
            raise _Skip("a hosted source's path must be a directory under content/")
        if posixpath.join(directory, MANIFEST_FILE) not in files:
            raise _Skip(f"{directory}/{MANIFEST_FILE} does not exist")
        has_pages = True
    else:
        raise _Skip(f"source type '{source_type}' is not external, repo or hosted")
    if has_pages and layout not in (*CONTENT_LAYOUTS, "tool"):
        raise _Skip(f"the {layout} layout has no pages; use an external source")

    return EntryRecord(
        id=entry_id,
        title=str(raw["title"]).strip(),
        short_description=str(raw["short_description"]).strip(),
        long_description=str(raw["long_description"]).strip(),
        type=str(raw["type"]),
        group=entry_type["group"],
        layout=layout,
        primary=raw.get("primary") is True,
        status=status,
        scope=str(raw["scope"]),
        tags=tags,
        professions=professions,
        owner={
            "organisation": str(owner["organisation"]),
            "team": str(owner.get("team") or ""),
            "contact": {
                key: str(value)
                for key, value in (owner.get("contact") or {}).items()
                if key in ("email", "url", "slack") and value
            },
        },
        applies_to=str(raw.get("applies_to") or "").strip(),
        related=_strings(raw.get("related")),
        source=source,
        version=str(raw.get("version") or "").strip() if layout == "tool" else "",
        links=links,
        has_pages=has_pages,
        external_url="" if has_pages else source["path"],
        position=position,
    )


def _links(value, layout: str) -> list[dict]:
    """A tool's links: only https ones, and only on the tool layout."""
    if layout != "tool" or not isinstance(value, list):
        return []
    links = []
    for item in value:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "")
        title = str(item.get("title") or "").strip()
        if url.startswith("https://") and title:
            links.append(
                {"title": title, "url": url, "description": str(item.get("description") or "").strip()}
            )
    return links
