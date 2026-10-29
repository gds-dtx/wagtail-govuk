"""Sync a content main page from its catalogue repo.

One sync reads the whole catalogue, then each entry with content of its own:

- a **hosted** entry from its directory in the catalogue's own download;
- a **repo** entry from its own repository, at its pinned ``ref`` or its
  branch's head. One whose commit has not moved since the last sync, and was
  rendered by this version of the code, keeps its stored pages, unfetched.

Everything is then swapped in within one transaction. A sync that fails before
then records why and leaves the last good copy serving. One entry whose content
fails does not fail the sync: it keeps its last good pages if it has any, and
otherwise its card links to its repository instead.
"""

import logging
from dataclasses import dataclass, field

from django.db import transaction
from django.utils import timezone

from .adapters import build_bundle
from .bundle import GuidanceSyncError
from .catalogue import read_catalogue
from .fetch import fetch_files, resolve_commit
from .layouts import shape
from .render import RENDER_VERSION, render_bundle
from .repository import RepositoryRef

logger = logging.getLogger(__name__)

ENTRY_FIELDS = (
    "group", "type", "layout", "primary", "status", "title", "short_description",
    "long_description", "scope", "tags", "professions", "owner", "applies_to",
    "related", "source", "version", "links", "has_pages", "external_url", "position",
)
DOCUMENT_FIELDS = (
    "path", "source_path", "title", "number", "markdown", "html", "intro_html",
    "sections", "headings", "metadata",
)


@dataclass
class SyncResult:
    commit: str
    unchanged: bool = False
    entries: int = 0
    pages: int = 0
    failed: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        short = self.commit[:7]
        if self.unchanged:
            return f"Already up to date at {short}."
        text = f"Synced {short}: {self.entries} entries, {self.pages} pages."
        if self.failed:
            text += f" {len(self.failed)} entr{'y' if len(self.failed) == 1 else 'ies'} failed."
        if self.warnings:
            text += f" {len(self.warnings)} warning(s)."
        return text


def _default_fetch(repository: RepositoryRef, *, timeout: float):
    commit = resolve_commit(repository, timeout=timeout)
    return commit, fetch_files(repository, commit, timeout=timeout)


def sync_catalogue_page(page, *, force: bool = False, timeout: float = 30.0, fetch=None) -> SyncResult:
    """Bring ``page``'s catalogue snapshot up to date.

    ``fetch`` replaces the network: it takes a ``RepositoryRef`` and returns
    ``(commit, files)``, for the catalogue and for each repo entry; tests use it.
    Where only the commit is wanted, the fake's commit is used.
    """
    from govuk.models import CatalogueDocument, CatalogueEntry, CatalogueSnapshot

    snapshot, _ = CatalogueSnapshot.objects.get_or_create(page=page)
    snapshot.last_attempt_at = timezone.now()

    def get(repository):
        if fetch is not None:
            return fetch(repository)
        return _default_fetch(repository, timeout=timeout)

    def head(repository):
        if fetch is not None:
            return fetch(repository)[0]
        return resolve_commit(repository, timeout=timeout)

    try:
        try:
            repository = RepositoryRef.parse(page.catalogue_repository, branch=page.catalogue_branch)
        except ValueError as exc:
            raise GuidanceSyncError(str(exc)) from exc

        existing = {entry.entry_id: entry for entry in snapshot.entries.all()}
        commit = head(repository)
        if (
            not force
            and snapshot.synced_at is not None
            and snapshot.commit_sha == commit
            and snapshot.source_key == repository.source_key
            and (snapshot.site or {}).get("render_version") == RENDER_VERSION
            and not _repo_entries_moved(existing.values(), head)
        ):
            snapshot.last_error = ""
            snapshot.save(update_fields=["last_attempt_at", "last_error"])
            return SyncResult(commit=commit, unchanged=True)

        commit, files = get(repository)
        catalogue = read_catalogue(files)
    except GuidanceSyncError as exc:
        snapshot.last_error = str(exc)
        snapshot.save(update_fields=["last_attempt_at", "last_error"])
        logger.warning("Catalogue sync failed for page %s: %s", page.pk, exc)
        raise

    result = SyncResult(commit=commit, warnings=list(catalogue.warnings))
    built = []
    for record in catalogue.entries:
        values = {name: getattr(record, name) for name in ENTRY_FIELDS}
        content = {"structure": {}, "content_key": "", "content_commit": "", "content_error": "",
                   "warnings": [], "documents": []}
        if record.has_pages and record.layout != "tool":
            previous = existing.get(record.id)
            try:
                content = _entry_content(
                    record, files, repository.at(commit), previous, get, head, force=force
                )
            except GuidanceSyncError as exc:
                result.failed.append(record.id)
                content = _fallback(record, previous, str(exc), repository.at(commit))
                if not content["documents"]:
                    values["has_pages"] = False
                    values["external_url"] = content.pop("fallback_url", "")
            result.warnings.extend(f"{record.id}: {w}" for w in content["warnings"])
        built.append((record.id, values, content))

    with transaction.atomic():
        snapshot.entries.all().delete()
        for entry_id, values, content in built:
            documents = content.pop("documents")
            content.pop("fallback_url", None)
            entry = CatalogueEntry.objects.create(
                snapshot=snapshot, entry_id=entry_id, **values, **content
            )
            CatalogueDocument.objects.bulk_create(
                CatalogueDocument(entry=entry, **document) for document in documents
            )
            result.pages += len(documents)
        result.entries = len(built)

        snapshot.source_key = repository.source_key
        snapshot.commit_sha = commit
        snapshot.synced_at = timezone.now()
        snapshot.last_error = ""
        snapshot.warnings = result.warnings
        snapshot.site = {
            **catalogue.site,
            "vocabularies": catalogue.vocabularies,
            "render_version": RENDER_VERSION,
        }
        snapshot.save()

    logger.info("Catalogue synced for page %s at %s: %s", page.pk, commit, result.summary())
    return result


def _repo_ref(record) -> RepositoryRef:
    source = record.source
    try:
        return RepositoryRef.parse(
            source.get("path", ""),
            branch=source.get("branch", ""),
            subdirectory=source.get("directory", ""),
            pin=source.get("ref", ""),
        )
    except ValueError as exc:
        raise GuidanceSyncError(str(exc)) from exc


def _repo_entries_moved(entries, head) -> bool:
    """Whether any repo entry's branch (or pin) now points at another commit."""
    for entry in entries:
        if (entry.source or {}).get("type") != "repo" or not entry.has_pages:
            continue
        try:
            if head(_repo_ref(entry)) != entry.content_commit:
                return True
        except GuidanceSyncError:
            return True
    return False


def _entry_content(record, catalogue_files, catalogue_repository, previous, get, head, *, force):
    """The structure and documents for one hosted or repo entry."""
    source = record.source
    if source["type"] == "hosted":
        directory = source["path"].strip("/") + "/"
        files = {
            path[len(directory) :]: data
            for path, data in catalogue_files.items()
            if path.startswith(directory)
        }
        repository = RepositoryRef(
            owner=catalogue_repository.owner,
            name=catalogue_repository.name,
            branch=catalogue_repository.branch,
            subdirectory=directory.rstrip("/"),
            commit=catalogue_repository.commit,
        )
        content_key = f"hosted:{directory}"
        commit = catalogue_repository.commit
    else:
        repository = _repo_ref(record)
        content_key = f"repo:{repository.source_key}"
        if (
            not force
            and previous is not None
            and previous.content_key == content_key
            and previous.content_commit
            and not previous.content_error
            and (previous.structure or {}).get("render_version") == RENDER_VERSION
            and previous.documents.exists()
            and head(repository) == previous.content_commit
        ):
            return _reuse(previous)
        commit, files = get(repository)

    rendered = render_bundle(build_bundle(files), repository.at(commit))
    structure, documents = shape(record.layout, rendered)
    if not documents:
        raise GuidanceSyncError("its content has no pages")
    return {
        "structure": structure,
        "content_key": content_key,
        "content_commit": commit,
        "content_error": "",
        "warnings": list(rendered.warnings),
        "documents": documents,
    }


def _reuse(previous) -> dict:
    return {
        "structure": previous.structure,
        "content_key": previous.content_key,
        "content_commit": previous.content_commit,
        "content_error": "",
        "warnings": list(previous.warnings),
        "documents": [
            {name: getattr(document, name) for name in DOCUMENT_FIELDS}
            for document in previous.documents.all()
        ],
    }


def _fallback(record, previous, error: str, catalogue_repository: RepositoryRef) -> dict:
    """A failed entry: its last good pages, or none and a link to its source on GitHub."""
    content = _reuse(previous) if previous is not None and previous.documents.exists() else {
        "structure": {}, "content_key": "", "content_commit": "", "content_error": "",
        "warnings": [], "documents": [],
    }
    content["content_error"] = error
    if not content["documents"]:
        if record.source.get("type") == "repo":
            content["fallback_url"] = f"https://github.com/{record.source.get('path', '')}"
        else:
            content["fallback_url"] = (
                f"{catalogue_repository.html_url}/tree/{catalogue_repository.ref}/"
                f"{record.source.get('path', '').strip('/')}"
            )
    return content
