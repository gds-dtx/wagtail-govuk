"""Admin for guidance collection pages: "Sync now", and syncing on publish."""

from django.conf import settings
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from wagtail import hooks
from wagtail.admin import messages
from wagtail.admin.auth import permission_denied, require_admin_access

from govuk.guidance.bundle import GuidanceSyncError
from govuk.guidance.sync import sync_guidance_page
from govuk.models import GuidanceCollectionPage


@require_admin_access
def guidance_sync_view(request, page_id: int):
    """Confirm, then sync a guidance collection page from its repo.

    A page of its own rather than a button on the edit form: a form cannot
    sit inside the page editor's form, and a sync is not a page edit.
    Needs publish permission on the page, as the sync changes what it serves.
    """
    if not settings.FEATURE_FLAGS.get("GUIDANCE"):
        raise Http404
    page = get_object_or_404(GuidanceCollectionPage, pk=page_id)
    if not page.permissions_for_user(request.user).can_publish():
        return permission_denied(request)

    edit_url = reverse("wagtailadmin_pages:edit", args=[page.pk])
    if request.method == "POST":
        try:
            result = sync_guidance_page(page, force=bool(request.POST.get("force")))
        except GuidanceSyncError as exc:
            messages.error(request, f"Sync failed for '{page.title}': {exc}")
        else:
            messages.success(request, f"'{page.title}': {result.summary()}")
        return redirect(edit_url)

    snapshot = page.snapshot
    return TemplateResponse(
        request,
        "wagtailadmin/guidance/sync.html",
        {
            "page": page,
            "snapshot": snapshot,
            "document_count": snapshot.documents.count() if snapshot else 0,
            "edit_url": edit_url,
        },
    )


@hooks.register("register_admin_urls")
def register_guidance_admin_urls():
    return [
        path("guidance/<int:page_id>/sync/", guidance_sync_view, name="govuk_guidance_sync"),
    ]


@hooks.register("after_publish_page")
def sync_guidance_after_publish(request, page):
    """Sync on the first publish, and whenever the source settings change.

    Content changes in the repo are picked up by "Sync now" or the
    ``sync_guidance`` command, not by publishing.
    """
    page = page.specific
    if not isinstance(page, GuidanceCollectionPage):
        return
    if not settings.FEATURE_FLAGS.get("GUIDANCE") or not page.needs_sync():
        return
    try:
        result = sync_guidance_page(page)
    except GuidanceSyncError as exc:
        messages.error(request, f"Published, but the guidance could not be synced: {exc}")
    else:
        messages.success(request, f"Guidance synced. {result.summary()}")
