import io
import json
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.http import (
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseNotAllowed,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from wagtail import hooks
from wagtail.admin import messages
from wagtail.admin.auth import permission_denied, require_admin_access
from wagtail.admin.menu import MenuItem
from wagtail.models import Page, Site
from wagtail.permission_policies import ModelPermissionPolicy

from govuk.content_discovery import ContentDiscoveryError, sync_content_discovery_source
from govuk.content_discovery.csv_import import (
    ContentDiscoverySourceImportError,
    import_content_discovery_sources_from_csv,
)
from govuk.models import (
    ContentDiscoverySettings,
    ContentDiscoverySource,
    EdDSAKeyPair,
    EdDSAKeySettings,
    ExternalContentItem,
    GovukRole,
    GovukSkill,
    JWTGenerationError,
)
from govuk.page_import_export import (
    PAGE_EXPORT_FORMAT,
    build_page_export_payload,
    dump_payload_as_json,
    import_pages_from_payload,
)
from govuk.reports import PageUsefulnessReportView
from govuk.utils import row_id_from_text


def _content_discovery_edit_url(site_id: int) -> str:
    return reverse(
        "wagtailsettings:edit",
        args=(
            ContentDiscoverySettings._meta.app_label,
            "contentdiscoverysettings",
            site_id,
        ),
    )


def _eddsa_keys_edit_url(site_id: int) -> str:
    return reverse(
        "wagtailsettings:edit",
        args=(
            EdDSAKeySettings._meta.app_label,
            "eddsakeysettings",
            site_id,
        ),
    )


def _safe_next_url(request, *, fallback_url: str) -> str:
    next_url = (request.POST.get("next") or "").strip()
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url
    return fallback_url


def _user_can_change_content_discovery_setting(request, *, site) -> bool:
    permission_policy = ContentDiscoverySettings.get_permission_policy()
    return permission_policy.user_has_permission_for_instance(
        request.user,
        "change",
        site,
    )


def _user_can_change_eddsa_key_setting(request, *, site) -> bool:
    permission_policy = EdDSAKeySettings.get_permission_policy()
    return permission_policy.user_has_permission_for_instance(
        request.user,
        "change",
        site,
    )


def _all_admin_sites() -> list[Site]:
    return list(
        Site.objects.select_related("root_page").order_by(
            "-is_default_site", "hostname", "port"
        )
    )


def _selected_site_for_request(request) -> Site | None:
    sites = _all_admin_sites()
    if not sites:
        return None

    raw_site_id = (
        request.GET.get("site_id")
        or request.GET.get("site")
        or request.POST.get("site_id")
        or ""
    ).strip()
    requested_site_id = row_id_from_text(raw_site_id)
    if requested_site_id is not None:
        selected_site = next(
            (site for site in sites if site.pk == requested_site_id),
            None,
        )
        if selected_site is not None:
            return selected_site

    default_site = next((site for site in sites if site.is_default_site), None)
    return default_site or sites[0]


def _import_export_admin_url(site_id: int) -> str:
    return f"{reverse('govuk_pages_import_export')}?site_id={site_id}"


def _normalised_selected_ids(raw_ids: list[str]) -> list[int]:
    selected_ids: list[int] = []
    for raw_id in raw_ids:
        selected_id = row_id_from_text(raw_id)
        if selected_id is not None:
            selected_ids.append(selected_id)
    return selected_ids


def _page_rows_for_site(site: Site, user) -> list[dict]:
    """The pages this user may export, which are the ones they may edit.

    Offering the rest and dropping them at the point of export would read as
    the export having failed. They are not offered.
    """
    root_page = site.root_page.specific
    # Inclusive: the home page holds the content the front page shows, and an
    # export of everything that left it out had to be finished by hand in the
    # new instance. The import side has always known what to do with one.
    pages = [
        page
        for page in Page.objects.descendant_of(root_page, inclusive=True)
        .specific()
        .order_by("path")
        if page.permissions_for_user(user).can_edit()
    ]
    return [
        {
            "id": page.pk,
            "title": page.title,
            "slug": page.slug,
            "depth": page.depth - root_page.depth,
            "model_label": page._meta.label,
            "is_private": page.view_restrictions.exists(),
        }
        for page in pages
    ]


def _may_manage_snippet(user, model) -> bool:
    """Whether the snippet's own menu would open for this user.

    Export offers what that menu would show them, and no more.
    """
    return ModelPermissionPolicy(model).user_has_any_permission(
        user, ["add", "change", "delete"]
    )


def _skill_rows(user) -> list[dict]:
    if not _may_manage_snippet(user, GovukSkill):
        return []
    return list(
        GovukSkill.objects.order_by("title", "slug").values(
            "id",
            "title",
            "slug",
        )
    )


def _role_rows(user) -> list[dict]:
    if not _may_manage_snippet(user, GovukRole):
        return []
    return list(
        GovukRole.objects.order_by("title", "slug").values(
            "id",
            "title",
            "slug",
        )
    )


@hooks.register("register_admin_menu_item")
def register_pages_import_export_menu_item():
    return MenuItem(
        "Import / Export",
        reverse("govuk_pages_import_export"),
        icon_name="download",
        order=700,
    )


@require_admin_access
def pages_import_export_index_view(request):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    selected_site = _selected_site_for_request(request)
    if selected_site is None:
        messages.error(request, "No sites are configured yet.")
        return redirect(reverse("wagtailadmin_home"))

    skills_feature_enabled = settings.FEATURE_FLAGS.get("SKILLS")

    return render(
        request,
        "govuk/admin/pages_import_export.html",
        {
            "header_title": "Import / Export",
            "page_title": "Import / Export",
            "page_subtitle": f"Site: {selected_site.hostname}",
            "header_icon": "download",
            "sites": _all_admin_sites(),
            "selected_site": selected_site,
            "page_rows": _page_rows_for_site(selected_site, request.user),
            "skills_feature_enabled": skills_feature_enabled,
            "skill_rows": _skill_rows(request.user) if skills_feature_enabled else [],
            "role_rows": _role_rows(request.user) if skills_feature_enabled else [],
        },
    )


@require_admin_access
def pages_export_view(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    selected_site = _selected_site_for_request(request)
    if selected_site is None:
        messages.error(request, "No sites are configured yet.")
        return redirect(reverse("wagtailadmin_home"))

    redirect_url = _import_export_admin_url(selected_site.pk)
    skills_feature_enabled = settings.FEATURE_FLAGS.get("SKILLS")
    selected_page_ids = _normalised_selected_ids(request.POST.getlist("page_ids"))
    selected_skill_ids = (
        _normalised_selected_ids(request.POST.getlist("skill_ids"))
        if skills_feature_enabled
        else []
    )
    selected_role_ids = (
        _normalised_selected_ids(request.POST.getlist("role_ids"))
        if skills_feature_enabled
        else []
    )

    if not selected_page_ids and not selected_skill_ids and not selected_role_ids:
        # Nothing ticked still has something to carry: the framework wording is
        # a site setting, so it hangs off no page and cannot be ticked. Without
        # this, applying a wording change elsewhere would mean exporting a page
        # nobody wanted to move and overwriting it on the way in, or trimming
        # the file by hand.
        if not skills_feature_enabled:
            messages.error(request, "Select at least one page to export.")
            return redirect(redirect_url)

    # A page id can be typed into the form as easily as it can be ticked, so
    # the same permission the listing was filtered by is asked again here.
    selected_pages = [
        page
        for page in Page.objects.descendant_of(selected_site.root_page, inclusive=True)
        .filter(pk__in=selected_page_ids)
        .specific()
        .order_by("path")
        if page.permissions_for_user(request.user).can_edit()
    ]
    selected_skills = (
        list(
            GovukSkill.objects.filter(pk__in=selected_skill_ids).order_by(
                "title", "slug"
            )
        )
        if skills_feature_enabled and _may_manage_snippet(request.user, GovukSkill)
        else []
    )
    selected_roles = (
        list(
            GovukRole.objects.filter(pk__in=selected_role_ids).order_by("title", "slug")
        )
        if skills_feature_enabled and _may_manage_snippet(request.user, GovukRole)
        else []
    )

    asked_for_content = bool(
        selected_page_ids or selected_skill_ids or selected_role_ids
    )
    if (
        asked_for_content
        and not selected_pages
        and not selected_skills
        and not selected_roles
    ):
        # Only where something was ticked and none of it could be found. An
        # export of the wording alone asks for nothing else and is not a
        # failure to report.
        if skills_feature_enabled:
            messages.error(
                request,
                "No matching pages, skills or roles were found for export.",
            )
        else:
            messages.error(request, "No matching pages were found for export.")
        return redirect(redirect_url)

    payload = build_page_export_payload(
        site=selected_site,
        pages=selected_pages,
        skills=selected_skills,
        roles=selected_roles,
        user=request.user,
    )
    file_contents = dump_payload_as_json(payload)
    timestamp = timezone.now().strftime("%Y%m%d-%H%M%S")
    # Named for what is in it, so that a wording export is still recognisable
    # in a downloads folder a fortnight later.
    subject = "pages" if asked_for_content else "wording"
    file_name = f"{subject}-export-site-{selected_site.pk}-{timestamp}.json"

    response = HttpResponse(file_contents, content_type="application/json")
    response["Content-Disposition"] = f'attachment; filename="{file_name}"'
    return response


@require_admin_access
def pages_import_view(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    selected_site = _selected_site_for_request(request)
    if selected_site is None:
        messages.error(request, "No sites are configured yet.")
        return redirect(reverse("wagtailadmin_home"))

    redirect_url = _import_export_admin_url(selected_site.pk)
    uploaded_file = request.FILES.get("json_file")
    if uploaded_file is None:
        messages.error(request, "Choose a JSON export file to import.")
        return redirect(redirect_url)

    try:
        payload_text = uploaded_file.read().decode("utf-8-sig")
    except UnicodeDecodeError:
        messages.error(request, "Import file must be UTF-8 encoded JSON.")
        return redirect(redirect_url)

    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError:
        messages.error(request, "Import file must contain valid JSON.")
        return redirect(redirect_url)

    if not isinstance(payload, dict):
        messages.error(request, "Import payload must be a JSON object.")
        return redirect(redirect_url)

    payload_format = (payload.get("format") or "").strip()
    if payload_format and payload_format != PAGE_EXPORT_FORMAT:
        messages.error(
            request,
            (
                "Unsupported import format. "
                f"Expected '{PAGE_EXPORT_FORMAT}', got '{payload_format}'."
            ),
        )
        return redirect(redirect_url)

    result = import_pages_from_payload(
        payload=payload,
        site=selected_site,
        user=request.user,
    )
    if not result.processed and result.errors:
        # Nothing in the file was even a page, a skill or a role to look at.
        # "Import complete" over a file that turned out not to be an export
        # reads as success at the very moment someone is checking whether the
        # content moved, with the reason it did not only in the line beneath.
        # Every reason, not the first: a file can be refused for several at
        # once, and the ones not shown would be met one at a time, an upload
        # apiece.
        messages.error(
            request, f"Nothing was imported. {_errors_preview(result.errors)}"
        )
        return redirect(redirect_url)

    drafted_note = (
        f" {result.drafted} left as drafts for a publisher to review."
        if result.drafted
        else ""
    )
    messages.success(
        request,
        (
            "Import complete. "
            f"Processed {result.processed}, created {result.created}, "
            f"updated {result.updated}, skipped {result.skipped}.{drafted_note}"
        ),
    )
    if result.notes:
        # Things the import did beyond adding and updating what was in the
        # file: replacing a placeholder home page, seeding the live service's
        # redirects. Both change the site in ways the counts above do not
        # show, and both were previously recorded and then never shown.
        messages.info(request, " ".join(result.notes))
    if result.errors:
        messages.warning(
            request, f"Some items were skipped: {_errors_preview(result.errors)}"
        )

    return redirect(redirect_url)


def _errors_preview(errors: list[str]) -> str:
    preview = "; ".join(errors[:3])
    if len(errors) > 3:
        preview = f"{preview}; and {len(errors) - 3} more."
    return preview


@require_admin_access
def sync_content_discovery_source_view(request, source_id: int):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    source = get_object_or_404(
        ContentDiscoverySource.objects.select_related("settings__site"),
        pk=source_id,
    )
    if not _user_can_change_content_discovery_setting(
        request, site=source.settings.site
    ):
        return permission_denied(request)

    fallback_url = _content_discovery_edit_url(source.settings.site_id)
    redirect_url = _safe_next_url(request, fallback_url=fallback_url)

    try:
        result = sync_content_discovery_source(source)
    except ContentDiscoveryError as exc:
        messages.error(request, f"Sync failed for '{source}': {exc}")
    else:
        messages.success(
            request,
            (
                f"Synced '{source}'. "
                f"Processed {result.total_entries}, created {result.created}, "
                f"updated {result.updated}, skipped {result.skipped}."
            ),
        )
    return redirect(redirect_url)


@require_admin_access
def sync_content_discovery_site_view(request, site_id: int):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    discovery_settings = get_object_or_404(ContentDiscoverySettings, site_id=site_id)
    if not _user_can_change_content_discovery_setting(
        request, site=discovery_settings.site
    ):
        return permission_denied(request)

    fallback_url = _content_discovery_edit_url(site_id)
    redirect_url = _safe_next_url(request, fallback_url=fallback_url)

    sources = list(discovery_settings.sources.all())
    if not sources:
        messages.warning(
            request, "No content discovery sources are configured for this site."
        )
        return redirect(redirect_url)

    totals = {"entries": 0, "created": 0, "updated": 0, "skipped": 0}
    failed_sources: list[str] = []
    for source in sources:
        try:
            result = sync_content_discovery_source(source)
        except ContentDiscoveryError as exc:
            failed_sources.append(f"{source}: {exc}")
            continue

        totals["entries"] += result.total_entries
        totals["created"] += result.created
        totals["updated"] += result.updated
        totals["skipped"] += result.skipped

    if failed_sources:
        messages.error(
            request,
            "Some sources failed to sync: " + "; ".join(failed_sources),
        )
    if totals["entries"] or not failed_sources:
        messages.success(
            request,
            (
                f"Synced {len(sources) - len(failed_sources)} of {len(sources)} sources. "
                f"Processed {totals['entries']} entries, created {totals['created']}, "
                f"updated {totals['updated']}, skipped {totals['skipped']}."
            ),
        )
    return redirect(redirect_url)


@require_admin_access
def clear_content_discovery_site_view(request, site_id: int):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    if not settings.DEBUG:
        return permission_denied(request)

    discovery_settings = get_object_or_404(ContentDiscoverySettings, site_id=site_id)
    if not _user_can_change_content_discovery_setting(
        request, site=discovery_settings.site
    ):
        return permission_denied(request)

    fallback_url = _content_discovery_edit_url(site_id)
    redirect_url = _safe_next_url(request, fallback_url=fallback_url)

    queryset = ExternalContentItem.objects.filter(
        source__settings__site_id=site_id
    ).distinct()
    item_count = queryset.count()
    queryset.delete()

    messages.warning(
        request,
        f"Cleared {item_count} external content item{'s' if item_count != 1 else ''} for this site.",
    )
    return redirect(redirect_url)


@require_admin_access
def import_content_discovery_site_view(request, site_id: int):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    discovery_settings = get_object_or_404(ContentDiscoverySettings, site_id=site_id)
    if not _user_can_change_content_discovery_setting(
        request, site=discovery_settings.site
    ):
        return permission_denied(request)

    fallback_url = _content_discovery_edit_url(site_id)
    redirect_url = _safe_next_url(request, fallback_url=fallback_url)

    csv_file = request.FILES.get("csv_file")
    if csv_file is None:
        messages.error(request, "Choose a CSV file to import.")
        return redirect(redirect_url)

    delimiter = (request.POST.get("delimiter") or ",").strip()
    if len(delimiter) != 1:
        messages.error(request, "Delimiter must be a single character.")
        return redirect(redirect_url)

    try:
        csv_content = csv_file.read().decode("utf-8-sig")
    except UnicodeDecodeError:
        messages.error(request, "CSV file must be UTF-8 encoded.")
        return redirect(redirect_url)

    try:
        result = import_content_discovery_sources_from_csv(
            io.StringIO(csv_content),
            default_site_id=site_id,
            allowed_site_ids={site_id},
            delimiter=delimiter,
        )
    except ContentDiscoverySourceImportError as exc:
        messages.error(request, f"Import failed: {exc}")
        return redirect(redirect_url)

    messages.success(
        request,
        (
            "Imported content discovery sources. "
            f"Processed {result.processed}, created {result.created}, "
            f"updated {result.updated}, unchanged {result.unchanged}, "
            f"skipped empty {result.skipped_empty}."
        ),
    )
    return redirect(redirect_url)


@require_admin_access
def generate_eddsa_key_pair_view(request, site_id: int):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    key_settings = get_object_or_404(EdDSAKeySettings, site_id=site_id)
    if not _user_can_change_eddsa_key_setting(request, site=key_settings.site):
        return permission_denied(request)

    fallback_url = _eddsa_keys_edit_url(site_id)
    redirect_url = _safe_next_url(request, fallback_url=fallback_url)

    requested_algorithm = (
        request.POST.get("algorithm") or EdDSAKeyPair.Algorithm.EDDSA
    ).strip()
    try:
        generated_key_pair = EdDSAKeyPair.generate_for_settings(
            settings_obj=key_settings,
            algorithm=requested_algorithm,
        )
    except ValidationError as exc:
        return HttpResponseBadRequest("; ".join(exc.messages))
    messages.success(
        request,
        f"Generated {generated_key_pair.algorithm} key pair '{generated_key_pair.key_id}'.",
    )
    return redirect(redirect_url)


@require_admin_access
def generate_eddsa_jwt_view(request, site_id: int):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    key_settings = get_object_or_404(EdDSAKeySettings, site_id=site_id)
    if not _user_can_change_eddsa_key_setting(request, site=key_settings.site):
        return permission_denied(request)

    htu = (request.POST.get("htu") or "").strip() or None
    htm = (request.POST.get("htm") or "").strip() or None
    raw_lifetime_seconds = (request.POST.get("lifetime_seconds") or "300").strip()
    try:
        lifetime_seconds = int(raw_lifetime_seconds)
    except (TypeError, ValueError):
        return HttpResponseBadRequest(
            "lifetime_seconds must be an integer between 1 and 86400."
        )

    if lifetime_seconds < 1 or lifetime_seconds > 86400:
        return HttpResponseBadRequest("lifetime_seconds must be between 1 and 86400.")

    try:
        access_token = key_settings.generate_jwt(
            htu=htu,
            htm=htm,
            lifetime=timedelta(seconds=lifetime_seconds),
        )
    except (JWTGenerationError, ImproperlyConfigured) as exc:
        return _eddsa_jwt_generation_error_response(exc)

    primary_key_pair = key_settings.get_primary_key_pair()
    return JsonResponse(
        {
            "token_type": "Bearer",
            "access_token": access_token,
            "expires_in": lifetime_seconds,
            "issuer": getattr(settings, "WAGTAILADMIN_BASE_URL", ""),
            "kid": getattr(primary_key_pair, "key_id", ""),
            "alg": getattr(primary_key_pair, "algorithm", ""),
            "htm": (htm or "").strip().upper() or None,
            "htu": htu,
        },
        json_dumps_params={"indent": 2, "sort_keys": True},
    )


def _eddsa_jwt_generation_error_response(exc: Exception) -> HttpResponseBadRequest:
    if settings.DEBUG:
        return HttpResponseBadRequest(str(exc))
    return HttpResponseBadRequest("Unable to generate JWT.")


@require_admin_access
def set_primary_eddsa_key_pair_view(request, key_pair_id: int):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    key_pair = get_object_or_404(
        EdDSAKeyPair.objects.select_related("settings__site"),
        pk=key_pair_id,
    )
    if not _user_can_change_eddsa_key_setting(request, site=key_pair.settings.site):
        return permission_denied(request)

    fallback_url = _eddsa_keys_edit_url(key_pair.settings.site_id)
    redirect_url = _safe_next_url(request, fallback_url=fallback_url)

    key_pair.mark_as_primary()
    messages.success(
        request,
        f"Set '{key_pair.key_id}' as the primary signing key pair ({key_pair.algorithm}).",
    )
    return redirect(redirect_url)


@require_admin_access
def delete_eddsa_key_pair_view(request, key_pair_id: int):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    key_pair = get_object_or_404(
        EdDSAKeyPair.objects.select_related("settings__site"),
        pk=key_pair_id,
    )
    if not _user_can_change_eddsa_key_setting(request, site=key_pair.settings.site):
        return permission_denied(request)

    fallback_url = _eddsa_keys_edit_url(key_pair.settings.site_id)
    redirect_url = _safe_next_url(request, fallback_url=fallback_url)

    deleted_key_id = key_pair.key_id
    deleted_algorithm = key_pair.algorithm
    key_pair.delete()
    messages.success(
        request,
        f"Deleted {deleted_algorithm} key pair '{deleted_key_id}'.",
    )
    return redirect(redirect_url)


@hooks.register("register_admin_urls")
def register_content_discovery_admin_urls():
    return [
        path(
            "pages/import-export/",
            pages_import_export_index_view,
            name="govuk_pages_import_export",
        ),
        path(
            "pages/import-export/export/",
            pages_export_view,
            name="govuk_pages_export",
        ),
        path(
            "pages/import-export/import/",
            pages_import_view,
            name="govuk_pages_import",
        ),
        path(
            "content-discovery/sync/source/<int:source_id>/",
            sync_content_discovery_source_view,
            name="govuk_content_discovery_sync_source",
        ),
        path(
            "content-discovery/sync/site/<int:site_id>/",
            sync_content_discovery_site_view,
            name="govuk_content_discovery_sync_site",
        ),
        path(
            "content-discovery/clear/site/<int:site_id>/",
            clear_content_discovery_site_view,
            name="govuk_content_discovery_clear_site",
        ),
        path(
            "content-discovery/import/site/<int:site_id>/",
            import_content_discovery_site_view,
            name="govuk_content_discovery_import_site",
        ),
        path(
            "eddsa-keys/generate/site/<int:site_id>/",
            generate_eddsa_key_pair_view,
            name="govuk_eddsa_generate_site_key",
        ),
        path(
            "eddsa-keys/generate-jwt/site/<int:site_id>/",
            generate_eddsa_jwt_view,
            name="govuk_eddsa_generate_site_jwt",
        ),
        path(
            "eddsa-keys/set-primary/<int:key_pair_id>/",
            set_primary_eddsa_key_pair_view,
            name="govuk_eddsa_set_primary_key",
        ),
        path(
            "eddsa-keys/delete/<int:key_pair_id>/",
            delete_eddsa_key_pair_view,
            name="govuk_eddsa_delete_key",
        ),
        path(
            "reports/page-usefulness/",
            PageUsefulnessReportView.as_view(),
            name="govuk_page_usefulness_report",
        ),
        path(
            "reports/page-usefulness/results/",
            PageUsefulnessReportView.as_view(results_only=True),
            name="govuk_page_usefulness_report_results",
        ),
    ]


@hooks.register("register_reports_menu_item")
def register_page_usefulness_report_menu_item():
    # order < 700 (the first built-in report) puts it at the top of Reports.
    return MenuItem(
        "Page usefulness",
        reverse("govuk_page_usefulness_report"),
        name="page-usefulness",
        icon_name="help",
        order=100,
    )

