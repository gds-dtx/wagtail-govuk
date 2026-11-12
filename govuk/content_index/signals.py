"""Keeping the content index current as editors publish.

Each handler queues its update to run once the publish has committed, so it
reads what was saved, and nothing it does can stop the publish: a failure is
logged, and the next rebuild puts the row right. With the content index off
the handlers do nothing.

Pages and snippets both send Wagtail's generic ``published`` and
``unpublished``; pages also send ``page_published`` and ``page_unpublished``,
which are what the page handlers listen to, so the generic handlers only act
on roles and skills.

Publishing never fetches an external entry's source: that would hold up the
publish (an import publishes dozens at once) for each page fetched. Sources are
fetched on a timer (``harvest_external_sources``) or with the admin's Fetch now.
"""

import logging

from django.db import transaction
from django.db.models.signals import post_delete, post_save
from wagtail.models import PageViewRestriction
from wagtail.signals import (
    page_published,
    page_unpublished,
    post_page_move,
    published,
    unpublished,
)

from govuk.models import GovukRole, GovukSkill

from . import content_index_enabled, indexer

logger = logging.getLogger(__name__)


def _after_commit(update, *args, **kwargs) -> None:
    if not content_index_enabled():
        return

    def run():
        try:
            update(*args, **kwargs)
        except Exception:
            logger.exception("Could not update the content index (%s)", update.__name__)

    transaction.on_commit(run)


def _page_changed(sender, instance, **kwargs):
    _after_commit(indexer.page_changed, instance.pk)


def _page_moved(sender, instance, parent_page_before=None, **kwargs):
    _after_commit(
        indexer.subtree_changed,
        instance.pk,
        previous_parent_id=getattr(parent_page_before, "pk", None),
    )


def _snippet_changed(sender, instance, **kwargs):
    if isinstance(instance, GovukRole):
        _after_commit(indexer.role_changed, instance.pk)
    elif isinstance(instance, GovukSkill):
        _after_commit(indexer.skill_changed, instance.pk)


def _restriction_changed(sender, instance, **kwargs):
    _after_commit(indexer.subtree_changed, instance.page_id)


def _skill_deleted(sender, instance, **kwargs):
    _after_commit(indexer.skill_removed, instance.pk)


def connect() -> None:
    page_published.connect(_page_changed, dispatch_uid="govuk.content_index.page_published")
    page_unpublished.connect(_page_changed, dispatch_uid="govuk.content_index.page_unpublished")
    post_page_move.connect(_page_moved, dispatch_uid="govuk.content_index.post_page_move")
    published.connect(_snippet_changed, dispatch_uid="govuk.content_index.published")
    unpublished.connect(_snippet_changed, dispatch_uid="govuk.content_index.unpublished")
    post_save.connect(
        _restriction_changed,
        sender=PageViewRestriction,
        dispatch_uid="govuk.content_index.restriction_saved",
    )
    post_delete.connect(
        _restriction_changed,
        sender=PageViewRestriction,
        dispatch_uid="govuk.content_index.restriction_deleted",
    )
    post_delete.connect(
        _skill_deleted, sender=GovukSkill, dispatch_uid="govuk.content_index.skill_deleted"
    )
