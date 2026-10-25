"""Sync a guidance collection page from its repo.

Fetch, read, render, then swap the new copy in within one transaction. A sync
that fails anywhere records why on the snapshot and leaves the last good copy
serving.
"""

import logging
from dataclasses import dataclass, field

from django.db import transaction
from django.utils import timezone

from .adapters import build_bundle
from .bundle import GuidanceSyncError
from .fetch import fetch_files, resolve_commit
from .render import RENDER_VERSION, render_bundle
from .repository import RepositoryRef

logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    commit: str
    unchanged: bool = False
    created: int = 0
    updated: int = 0
    removed: int = 0
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        short = self.commit[:7]
        if self.unchanged:
            return f"Already up to date at {short}."
        text = (
            f"Synced {short}: {self.created} added, {self.updated} updated, "
            f"{self.removed} removed."
        )
        if self.warnings:
            text += f" {len(self.warnings)} warning(s)."
        return text


def sync_guidance_page(
    page, *, force: bool = False, timeout: float = 30.0, fetch=None
) -> SyncResult:
    """Bring ``page``'s snapshot up to date with its repo.

    ``fetch`` replaces the network step, taking a ``RepositoryRef`` and
    returning ``(commit, files)``; tests use it.
    """
    from govuk.models import GuidanceDocument, GuidanceSnapshot

    snapshot, _ = GuidanceSnapshot.objects.get_or_create(page=page)
    snapshot.last_attempt_at = timezone.now()
    try:
        try:
            repository = RepositoryRef.from_page(page)
        except ValueError as exc:
            raise GuidanceSyncError(str(exc)) from exc

        if fetch is None:
            commit = resolve_commit(repository, timeout=timeout)
            unchanged = (
                not force
                and snapshot.synced_at is not None
                and snapshot.commit_sha == commit
                and snapshot.source_key == repository.source_key
                and (snapshot.site or {}).get("render_version") == RENDER_VERSION
            )
            if unchanged:
                snapshot.last_error = ""
                snapshot.save(update_fields=["last_attempt_at", "last_error"])
                return SyncResult(commit=commit, unchanged=True)
            files = fetch_files(repository, commit, timeout=timeout)
        else:
            commit, files = fetch(repository)

        rendered = render_bundle(build_bundle(files), repository.at(commit))
    except GuidanceSyncError as exc:
        snapshot.last_error = str(exc)
        snapshot.save(update_fields=["last_attempt_at", "last_error"])
        logger.warning("Guidance sync failed for page %s: %s", page.pk, exc)
        raise

    result = SyncResult(commit=commit, warnings=rendered.warnings)
    with transaction.atomic():
        existing = {document.path: document for document in snapshot.documents.all()}
        incoming = {document.path for document in rendered.documents}
        stale = [path for path in existing if path not in incoming]
        if stale:
            snapshot.documents.filter(path__in=stale).delete()
        result.removed = len(stale)

        to_create, to_update = [], []
        for document in rendered.documents:
            values = {
                "source_path": document.source_path,
                "title": document.title,
                "markdown": document.markdown,
                "html": document.html,
                "headings": document.headings,
                "metadata": document.metadata,
            }
            row = existing.get(document.path)
            if row is None:
                to_create.append(GuidanceDocument(snapshot=snapshot, path=document.path, **values))
            elif any(getattr(row, name) != value for name, value in values.items()):
                for name, value in values.items():
                    setattr(row, name, value)
                to_update.append(row)
        GuidanceDocument.objects.bulk_create(to_create)
        if to_update:
            GuidanceDocument.objects.bulk_update(to_update, list(values))
        result.created, result.updated = len(to_create), len(to_update)

        snapshot.source_key = repository.source_key
        snapshot.commit_sha = commit
        snapshot.synced_at = timezone.now()
        snapshot.last_error = ""
        snapshot.warnings = rendered.warnings
        snapshot.site = rendered.site
        snapshot.save()

    logger.info(
        "Guidance synced for page %s at %s: %s", page.pk, commit, result.summary()
    )
    return result
