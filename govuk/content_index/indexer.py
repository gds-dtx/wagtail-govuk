"""Stores documents in the index and keeps them current.

``_store`` skips an item whose hash is unchanged, so the publish hooks can
re-index generously. ``rebuild`` catches what they cannot see (renamed tags,
flag changes, direct database writes).
"""

import logging
from collections import Counter
from dataclasses import dataclass, field

from django.db import transaction
from wagtail.models import Page

from govuk.models import (
    FrameworkMainPage,
    FrameworkSkillsPage,
    GovukRole,
    GovukSkill,
    IndexedChunk,
    IndexedDocument,
    entry_page_models,
)

from .embeddings import document_text, section_text, text_hash
from .extract import (
    IndexContext,
    eligible_pages,
    entry_for,
    page_document,
    role_document,
    skill_document,
)

logger = logging.getLogger(__name__)

# Bump when extraction or chunking changes, or rebuilds skip unchanged sources.
INDEX_VERSION = 1

CREATED = "created"
UPDATED = "updated"
UNCHANGED = "unchanged"
REMOVED = "removed"
SKIPPED = "skipped"
FAILED = "failed"
OUTCOMES = (CREATED, UPDATED, UNCHANGED, REMOVED, SKIPPED, FAILED)


@dataclass
class IndexResult:
    counts: Counter = field(default_factory=Counter)
    failures: list[str] = field(default_factory=list)


def _store(source: dict, data, *, force: bool) -> str:
    existing = IndexedDocument.objects.filter(**source).first()
    if data is None:
        if existing is None:
            return SKIPPED
        existing.delete()
        return REMOVED
    digest = data.digest()
    if (
        existing is not None
        and not force
        and existing.content_hash == digest
        and existing.index_version == INDEX_VERSION
    ):
        return UNCHANGED
    # Hash the truncated values, as ``embeddings.wanted_texts`` reads them back.
    title, type_label = data.title[:255], data.type_label[:80]
    fields = {
        "kind": data.kind,
        "origin": data.origin,
        "site_id": data.site_id,
        "url_path": data.url_path[:500],
        "source_url": data.source_url[:500],
        "title": title,
        "description": data.description,
        "type_label": type_label,
        "breadcrumbs": data.breadcrumbs,
        "metadata": data.metadata,
        "markdown": data.markdown,
        "last_updated": data.last_updated,
        "content_hash": digest,
        "index_version": INDEX_VERSION,
        "embedding_hash": text_hash(document_text(title, type_label, data.description)),
    }
    if existing is None:
        document = IndexedDocument.objects.create(**source, **fields)
    else:
        document = existing
        for name, value in fields.items():
            setattr(document, name, value)
        document.save()
        document.chunks.all().delete()
    IndexedChunk.objects.bulk_create(
        IndexedChunk(
            document=document,
            position=position,
            heading_path=chunk.heading_path[:500],
            anchor=chunk.anchor[:200],
            text=chunk.text,
            embedding_hash=text_hash(section_text(title, type_label, chunk.heading_path[:500], chunk.text)),
        )
        for position, chunk in enumerate(data.chunks())
    )
    return CREATED if existing is None else UPDATED


def _run(result: IndexResult, label: str, source: dict, read, *, force: bool) -> None:
    """Store one item in its own savepoint; a failure is counted, not raised."""
    try:
        with transaction.atomic():
            outcome = _store(source, read(), force=force)
    except Exception as exc:
        logger.exception("Could not index %s", label)
        result.counts[FAILED] += 1
        result.failures.append(f"{label}: {exc}")
    else:
        result.counts[outcome] += 1


def index_pages(pages, *, ctx=None, force=False, result=None) -> IndexResult:
    ctx = ctx or IndexContext()
    result = result or IndexResult()
    pages = pages.filter(depth__gt=1)
    eligible = set(
        eligible_pages().filter(pk__in=pages.values("pk")).values_list("pk", flat=True)
    )
    for page in pages.order_by("path").specific():
        _run(
            result,
            f"page {page.pk}",
            {"page_id": page.pk},
            lambda page=page: page_document(page, ctx) if page.pk in eligible else None,
            force=force,
        )
    return result


def index_page(page, **kwargs) -> IndexResult:
    return index_pages(Page.objects.filter(pk=page.pk), **kwargs)


def index_subtree(page, **kwargs) -> IndexResult:
    return index_pages(Page.objects.descendant_of(page, inclusive=True), **kwargs)


def index_roles(roles=None, *, ctx=None, force=False, result=None) -> IndexResult:
    ctx = ctx or IndexContext()
    result = result or IndexResult()
    for role in GovukRole.objects.all() if roles is None else roles:
        _run(
            result,
            f"role {role.pk}",
            {"role_id": role.pk},
            lambda role=role: role_document(role, ctx),
            force=force,
        )
    return result


def index_skills(skills=None, *, ctx=None, force=False, result=None) -> IndexResult:
    ctx = ctx or IndexContext()
    result = result or IndexResult()
    for skill in GovukSkill.objects.all() if skills is None else skills:
        _run(
            result,
            f"skill {skill.pk}",
            {"skill_id": skill.pk},
            lambda skill=skill: skill_document(skill, ctx),
            force=force,
        )
    return result


REBUILD_SCOPES = ("pages", "roles", "skills")


def rebuild(*, only: str | None = None, force: bool = False, dry_run: bool = False) -> IndexResult:
    ctx = IndexContext()
    result = IndexResult()
    with transaction.atomic():
        if only in (None, "pages"):
            index_pages(Page.objects.all(), ctx=ctx, force=force, result=result)
        if only in (None, "roles"):
            index_roles(ctx=ctx, force=force, result=result)
        if only in (None, "skills"):
            index_skills(ctx=ctx, force=force, result=result)
        if dry_run:
            transaction.set_rollback(True)
    return result


# -- keeping it current ----------------------------------------------------------------


def _identity(page) -> tuple | None:
    """The title and address a page's descendants copy into their breadcrumbs and URLs."""
    return IndexedDocument.objects.filter(page_id=page.pk).values_list("title", "url_path").first()


def _entry_containing(page):
    if page is None:
        return None
    page = page.specific
    if isinstance(page, entry_page_models()):
        return page
    return entry_for(page)


def _framework_dependents(page, ctx: IndexContext) -> None:
    """Re-index roles and skills when ``page``'s subtree holds the pages that serve them."""
    subtree = Page.objects.descendant_of(page, inclusive=True)
    skills_page = subtree.type(FrameworkSkillsPage).exists()
    if skills_page or subtree.type(FrameworkMainPage).exists():
        index_roles(ctx=ctx)
    if skills_page:
        index_skills(ctx=ctx)


def page_changed(page_id: int) -> None:
    """A catalogue sub page re-indexes its whole entry (numbering); a new title or URL its subtree."""
    page = Page.objects.filter(pk=page_id).first()
    if page is None:
        return
    ctx = IndexContext()
    entry = entry_for(page)
    target = entry if entry is not None else page.specific
    before = _identity(target)
    index_page(target, ctx=ctx)
    after = _identity(target)
    if entry is not None or (after is not None and after != before):
        index_subtree(target, ctx=ctx)
    _framework_dependents(target, ctx)


def subtree_changed(page_id: int, *, previous_parent_id: int | None = None) -> None:
    """After a move or view-restriction change; also renumbers any entries left or joined."""
    page = Page.objects.filter(pk=page_id).first()
    if page is None:
        return
    ctx = IndexContext()
    index_subtree(page, ctx=ctx)
    previous_parent = Page.objects.filter(pk=previous_parent_id).first() if previous_parent_id else None
    entries = {}
    for parent in (page.get_parent(), previous_parent):
        entry = _entry_containing(parent)
        if entry is not None:
            entries[entry.pk] = entry
    for entry in entries.values():
        index_subtree(entry, ctx=ctx)
    _framework_dependents(page, ctx)


def role_changed(role_id: int) -> None:
    """Re-index the role and the roles whose progression lists name it."""
    role = GovukRole.objects.filter(pk=role_id).first()
    if role is None:
        return
    ctx = IndexContext()
    related = {other.pk for other in role.get_roles_that_could_lead_here()}
    related |= {other.pk for other in ctx.senior_roles_by_source.get(role.pk, [])}
    index_roles(GovukRole.objects.filter(pk__in={role.pk, *related}), ctx=ctx)


def _roles_using(skill_id: int):
    return GovukRole.objects.filter(
        pk__in=[role.pk for role in GovukRole.objects.all() if skill_id in role.get_skill_ids()]
    )


def skill_changed(skill_id: int) -> None:
    """Re-index the skill and the roles that list it."""
    ctx = IndexContext()
    index_skills(GovukSkill.objects.filter(pk=skill_id), ctx=ctx)
    index_roles(_roles_using(skill_id), ctx=ctx)


def skill_removed(skill_id: int) -> None:
    """The skill's row cascades; only its roles need re-indexing."""
    index_roles(_roles_using(skill_id))
