"""The content catalogue's import and export, in the admin.

Linked from a content main page's edit view (``CatalogueToolsPanel``). Both
need permission to publish the main page: an import publishes pages beneath
it, and an export includes entries not yet published. Both 404 on a site
without the catalogue (``FEATURE_FLAGS["GUIDANCE"]``).
"""

from django import forms
from django.http import Http404, HttpResponse, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, render
from django.urls import path, reverse
from wagtail import hooks
from wagtail.admin.auth import permission_denied

from govuk.catalogue.bundle import GuidanceSyncError
from govuk.catalogue.export_catalogue import export_catalogue
from govuk.catalogue.import_catalogue import (
    MAX_ZIP_BYTES,
    files_from_github,
    files_from_zip,
    import_catalogue,
)
from govuk.models import ContentMainPage, catalogue_enabled


class CatalogueImportForm(forms.Form):
    zip_file = forms.FileField(
        required=False,
        label="A zipped catalogue",
        help_text="A zip of the catalogue's folder, such as GitHub's Download ZIP.",
    )
    repository = forms.CharField(
        required=False,
        label="Or a GitHub repository",
        help_text="As owner/name, such as alphagov/tech-catalogue. It must be public.",
    )
    branch = forms.CharField(required=False, help_text="Optional. The default is main.")
    update = forms.BooleanField(
        required=False,
        label="Rewrite pages the catalogue already has",
        help_text="Replaces what editors have changed on them since they were imported.",
    )
    draft = forms.BooleanField(
        required=False,
        label="Save as drafts",
        help_text="To review and publish here, rather than publish straight away.",
    )

    def clean(self):
        data = super().clean()
        if bool(data.get("zip_file")) == bool((data.get("repository") or "").strip()):
            raise forms.ValidationError("Choose a zip file or enter a repository, not both.")
        upload = data.get("zip_file")
        if upload and upload.size > MAX_ZIP_BYTES:
            raise forms.ValidationError(f"The zip file must be under {MAX_ZIP_BYTES // 1_000_000} MB.")
        return data


def _main_page_for(request, page_id):
    """The main page, if the catalogue is on and the user may publish it."""
    if not catalogue_enabled():
        raise Http404
    page = get_object_or_404(ContentMainPage, pk=page_id)
    if not page.permissions_for_user(request.user).can_publish():
        return page, permission_denied(request)
    return page, None


def catalogue_import_view(request, page_id):
    page, denied = _main_page_for(request, page_id)
    if denied:
        return denied
    if request.method not in ("GET", "POST"):
        return HttpResponseNotAllowed(["GET", "POST"])

    result, error = None, ""
    form = (
        CatalogueImportForm(request.POST, request.FILES)
        if request.method == "POST"
        else CatalogueImportForm()
    )
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            if data["zip_file"]:
                files, repository = files_from_zip(data["zip_file"].read()), None
            else:
                files, repository = files_from_github(
                    data["repository"].strip(), branch=data["branch"].strip()
                )
            result = import_catalogue(
                page,
                files,
                repository=repository,
                publish=not data["draft"],
                update_existing=data["update"],
                user=request.user,
            )
        except GuidanceSyncError as exc:
            error = str(exc)

    return render(
        request,
        "wagtailadmin/catalogue/import.html",
        {
            "page": page,
            "form": form,
            "result": result,
            "error": error,
            "edit_url": reverse("wagtailadmin_pages:edit", args=[page.pk]),
            "explore_url": reverse("wagtailadmin_explore", args=[page.pk]),
            "header_title": f"Import a catalogue into {page.title}",
            "page_title": "Import a catalogue",
            "header_icon": "upload",
        },
    )


def catalogue_export_view(request, page_id):
    page, denied = _main_page_for(request, page_id)
    if denied:
        return denied
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    result = export_catalogue(page)
    response = HttpResponse(result.zip_bytes(), content_type="application/zip")
    response["Content-Disposition"] = f'attachment; filename="{page.slug}-catalogue.zip"'
    return response


@hooks.register("register_admin_urls")
def register_catalogue_admin_urls():
    return [
        path(
            "catalogue/<int:page_id>/import/",
            catalogue_import_view,
            name="govuk_catalogue_import",
        ),
        path(
            "catalogue/<int:page_id>/export/",
            catalogue_export_view,
            name="govuk_catalogue_export",
        ),
    ]
