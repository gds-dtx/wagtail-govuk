"""Storing documents in the index, and keeping them current.

Every write goes through ``_store``: it compares what an item reads into now
with the hash on its row, so re-indexing something that has not changed costs
a read and nothing more. That is what lets the update after a publish
re-index more than strictly changed (a whole entry for one of its points, a
whole subtree for a renamed page) without thinking about it.

The ``*_changed`` functions are what the signal handlers call, by id, once
the publish has committed. A rebuild (``rebuild``) walks everything, and is
what catches what they cannot see: a renamed tag or catalogue type, a change
of feature flag, content written straight to the database.
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

from .extract import (
    IndexContext,
    eligible_pages,
    entry_for,
    page_document,
    role_document,
    skill_document,
)

logger = logging.getLogger(__name__)

# Bump when a change alters what an item is read into or how it is split
# (``extract``, ``markdown``, ``chunking``): a row's hash is of its source, so
# without this the next rebuild would skip every row whose source is unchanged.
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
    fields = {
        "kind": data.kind,
        "origin": data.origin,
        "site_id": data.site_id,
        "url_path": data.url_path[:500],
        "source_url": data.source_url[:500],
        "title": data.title[:255],
        "description": data.description,
        "type_label": data.type_label[:80],
        "breadcrumbs": data.breadcrumbs,
        "metadata": data.metadata,
        "markdown": data.markdown,
        "last_updated": data.last_updated,
        "content_hash": digest,
        "index_version": INDEX_VERSION,
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
        )
        for position, chunk in enumerate(data.chunks())
    )
    return CREATED if existing is None else UPDATED


def _run(result: IndexResult, label: str, source: dict, read, *, force: bool) -> None:
    """Read and store one item in its own savepoint; a failure is counted, not raised."""
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
    """Index each of ``pages``, or remove it if readers can no longer reach it."""
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
    """Every item on the site, indexed or removed; ``dry_run`` writes nothing."""
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
    """What a page's descendants copy from it: its title (their breadcrumbs) and address."""
    return IndexedDocument.objects.filter(page_id=page.pk).values_list("title", "url_path").first()


def _entry_containing(page):
    """The catalogue entry ``page`` is, or is within; None outside the catalogue."""
    if page is None:
        return None
    page = page.specific
    if isinstance(page, entry_page_models()):
        return page
    return entry_for(page)


def _framework_dependents(page, ctx: IndexContext) -> None:
    """Roles and skills, when ``page`` or a page beneath it is where they are served.

    A role's address is under the framework main page, and a skill's (and every
    role's link to one) under the skills A to Z, so publishing, moving or
    unpublishing either changes them all.
    """
    subtree = Page.objects.descendant_of(page, inclusive=True)
    skills_page = subtree.type(FrameworkSkillsPage).exists()
    if skills_page or subtree.type(FrameworkMainPage).exists():
        index_roles(ctx=ctx)
    if skills_page:
        index_skills(ctx=ctx)


def page_changed(page_id: int) -> None:
    """After a page is published or unpublished.

    A page within a catalogue entry changes the entry too (its list of points
    or steps, and every page's number in it), so the whole entry is re-indexed.
    A page that comes out under a new title or address changes its
    descendants' breadcrumbs and addresses, so they are as well.
    """
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
    """After a page is moved, or a view restriction on it is added or removed.

    Both change what is beneath it: its addresses, or who may read it. A page
    moved within, into or out of a catalogue entry renumbers the entries it
    left and joined.
    """
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
    """After a role is published or unpublished: it, and the roles whose
    progression lists name it."""
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
    """After a skill is published or unpublished: it, and the roles that list it."""
    ctx = IndexContext()
    index_skills(GovukSkill.objects.filter(pk=skill_id), ctx=ctx)
    index_roles(_roles_using(skill_id), ctx=ctx)


def skill_removed(skill_id: int) -> None:
    """After a skill is deleted: its row went with it, and its roles no longer list it."""
    index_roles(_roles_using(skill_id))
