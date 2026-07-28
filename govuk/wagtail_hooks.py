import base64
import io
import json
from datetime import timedelta

from draftjs_exporter.dom import DOM
from django.conf import settings
from django import forms
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.http import (
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseNotAllowed,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse, reverse_lazy
from django.utils.html import escape
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils import timezone
from wagtail import hooks
from wagtail.admin import messages
from wagtail.admin.auth import permission_denied, require_admin_access
from wagtail.admin.menu import MenuItem
from wagtail.admin.rich_text.converters.contentstate_models import Entity
from wagtail.admin.rich_text.converters.html_to_contentstate import (
    AtomicBlockEntityElementHandler,
    BlockElementHandler,
    PageLinkElementHandler,
)
from wagtail.admin.rich_text.editors.draftail import features as draftail_features
from wagtail.models import Page, Site
from wagtail.rich_text import EmbedHandler
from wagtail.rich_text.pages import PageLinkHandler
from wagtail.snippets.models import register_snippet
from wagtail.snippets.views.snippets import IndexView as SnippetIndexView
from wagtail.snippets.views.snippets import SnippetViewSet
from wagtail.whitelist import check_url

from govuk.content_discovery import ContentDiscoveryError, sync_content_discovery_source
from govuk.content_discovery_import import (
    ContentDiscoverySourceImportError,
    import_content_discovery_sources_from_csv,
)
from govuk.models import (
    ContentDiscoverySettings,
    ContentDiscoverySource,
    EdDSAKeyPair,
    EdDSAKeySettings,
    ExternalContentItem,
    Feedback,
    GovukChangelogEntry,
    GovukRole,
    GovukSkill,
    GovukTag,
    JWTGenerationError,
)
from govuk.page_import_export import (
    PAGE_EXPORT_FORMAT,
    build_page_export_payload,
    dump_payload_as_json,
    import_pages_from_payload,
)

GOVUK_BUTTON_FEATURE = "govuk-button"
GOVUK_START_BUTTON_FEATURE = "govuk-start-button"
GOVUK_BUTTON_ENTITY_TYPE = "GOVUK_BUTTON_LINK"
GOVUK_START_BUTTON_ENTITY_TYPE = "GOVUK_START_BUTTON_LINK"
GOVUK_BUTTON_LINKTYPE = "govuk-button"
GOVUK_START_BUTTON_LINKTYPE = "govuk-start-button"
GOVUK_BUTTON_STYLE_ATTR = "data-govuk-button-style"
GOVUK_BUTTON_STYLE_DEFAULT = "default"
GOVUK_BUTTON_STYLE_START = "start"
RAW_HTML_FEATURE = "raw-html"
RAW_HTML_ENTITY_TYPE = "RAW_HTML"
RAW_HTML_EMBEDTYPE = "raw_html"
INSET_TEXT_FEATURE = "inset-text"
INSET_TEXT_BLOCK_TYPE = "inset-text"


def _encode_raw_html(raw_html: str | None) -> str:
    normalised_html = (raw_html or "").strip()
    if not normalised_html:
        return ""
    return base64.urlsafe_b64encode(normalised_html.encode("utf-8")).decode("ascii")


def _decode_raw_html(encoded_html: str | None) -> str:
    normalised_encoded_html = (encoded_html or "").strip()
    if not normalised_encoded_html:
        return ""

    # Base64 strings in embed attributes can be copied without padding.
    padding = "=" * (-len(normalised_encoded_html) % 4)
    try:
        return base64.urlsafe_b64decode(
            (normalised_encoded_html + padding).encode("ascii")
        ).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return ""


def _get_govuk_button_attributes(*, is_start: bool) -> dict[str, str]:
    classes = "govuk-button govuk-button--start" if is_start else "govuk-button"
    style = GOVUK_BUTTON_STYLE_START if is_start else GOVUK_BUTTON_STYLE_DEFAULT
    return {
        "class": classes,
        "role": "button",
        "draggable": "false",
        "data-module": "govuk-button",
        GOVUK_BUTTON_STYLE_ATTR: style,
    }


def _build_govuk_button_opening_tag(*, href: str | None, is_start: bool) -> str:
    attrs = _get_govuk_button_attributes(is_start=is_start)
    ordered_attrs: list[str] = []
    if href:
        ordered_attrs.append(f'href="{escape(href)}"')
    ordered_attrs.extend(
        [
            f'class="{escape(attrs["class"])}"',
            f'role="{escape(attrs["role"])}"',
            f'draggable="{escape(attrs["draggable"])}"',
            f'data-module="{escape(attrs["data-module"])}"',
            f'{GOVUK_BUTTON_STYLE_ATTR}="{escape(attrs[GOVUK_BUTTON_STYLE_ATTR])}"',
        ]
    )
    return "<a " + " ".join(ordered_attrs) + ">"


def _govuk_button_entity(props: dict, *, is_start: bool):
    id_ = props.get("id")
    link_props = {}
    link_props["linktype"] = (
        GOVUK_START_BUTTON_LINKTYPE if is_start else GOVUK_BUTTON_LINKTYPE
    )
    if id_ is not None:
        link_props["id"] = id_
    else:
        link_props["url"] = check_url(props.get("url") or "") or "#"
    return DOM.create_element("a", link_props, props["children"])


def govuk_button_entity(props: dict):
    return _govuk_button_entity(props, is_start=False)


def govuk_start_button_entity(props: dict):
    return _govuk_button_entity(props, is_start=True)


class GovukButtonLinkElementHandler(PageLinkElementHandler):
    def get_attribute_data(self, attrs):
        if "id" in attrs:
            return super().get_attribute_data(attrs)
        return {"url": attrs.get("url", "")}


class GovukButtonLinkHandler(PageLinkHandler):
    identifier = GOVUK_BUTTON_LINKTYPE
    is_start = False

    @classmethod
    def expand_db_attributes_many(cls, attrs_list: list[dict]) -> list[str]:
        return [
            _build_govuk_button_opening_tag(
                href=(
                    page.localized.url
                    if page
                    else (check_url(attrs.get("url") or "") or "#")
                ),
                is_start=cls.is_start,
            )
            for attrs, page in zip(attrs_list, cls.get_many(attrs_list))
        ]

    @classmethod
    def extract_references(cls, attrs):
        if attrs.get("id"):
            yield from super().extract_references(attrs)


class GovukStartButtonLinkHandler(GovukButtonLinkHandler):
    identifier = GOVUK_START_BUTTON_LINKTYPE
    is_start = True


class RawHtmlElementHandler(AtomicBlockEntityElementHandler):
    def create_entity(self, name, attrs, state, contentstate):
        return Entity(
            RAW_HTML_ENTITY_TYPE,
            "MUTABLE",
            {"html": _decode_raw_html(attrs.get("html", ""))},
        )


class RawHtmlEmbedHandler(EmbedHandler):
    identifier = RAW_HTML_EMBEDTYPE

    @classmethod
    def expand_db_attributes_many(cls, attrs_list: list[dict]) -> list[str]:
        return [_decode_raw_html(attrs.get("html", "")) for attrs in attrs_list]


def raw_html_entity(props: dict):
    return DOM.create_element(
        "embed",
        {
            "embedtype": RAW_HTML_EMBEDTYPE,
            "html": _encode_raw_html(props.get("html", "")),
        },
    )


@hooks.register("register_rich_text_features")
def register_govuk_button_rich_text_features(features):
    features.register_link_type(GovukButtonLinkHandler)
    features.register_link_type(GovukStartButtonLinkHandler)
    features.register_embed_type(RawHtmlEmbedHandler)

    for feature_name in (
        GOVUK_BUTTON_FEATURE,
        GOVUK_START_BUTTON_FEATURE,
        RAW_HTML_FEATURE,
        INSET_TEXT_FEATURE,
    ):
        if feature_name not in features.default_features:
            features.default_features.append(feature_name)

    link_chooser_urls = {
        "pageChooser": reverse_lazy("wagtailadmin_choose_page"),
        "externalLinkChooser": reverse_lazy("wagtailadmin_choose_page_external_link"),
        "emailLinkChooser": reverse_lazy("wagtailadmin_choose_page_email_link"),
        "phoneLinkChooser": reverse_lazy("wagtailadmin_choose_page_phone_link"),
        "anchorLinkChooser": reverse_lazy("wagtailadmin_choose_page_anchor_link"),
    }
    common_editor_plugin_args = {
        "attributes": ["url", "id", "parentId"],
        "allowlist": {
            "href": "^(http:|https:|mailto:|tel:|#|undefined$)",
        },
        "chooserUrls": link_chooser_urls,
    }

    features.register_editor_plugin(
        "draftail",
        GOVUK_BUTTON_FEATURE,
        draftail_features.EntityFeature(
            {
                "type": GOVUK_BUTTON_ENTITY_TYPE,
                "label": "Btn",
                "description": "Button link",
                **common_editor_plugin_args,
            },
            js=[
                "wagtailadmin/js/page-chooser-modal.js",
                "govuk/js/draftail-govuk-button.js",
            ],
        ),
    )
    features.register_converter_rule(
        "contentstate",
        GOVUK_BUTTON_FEATURE,
        {
            "from_database_format": {
                f'a[linktype="{GOVUK_BUTTON_LINKTYPE}"]': GovukButtonLinkElementHandler(
                    GOVUK_BUTTON_ENTITY_TYPE
                ),
            },
            "to_database_format": {
                "entity_decorators": {GOVUK_BUTTON_ENTITY_TYPE: govuk_button_entity}
            },
        },
    )

    features.register_editor_plugin(
        "draftail",
        GOVUK_START_BUTTON_FEATURE,
        draftail_features.EntityFeature(
            {
                "type": GOVUK_START_BUTTON_ENTITY_TYPE,
                "description": "Start button link",
                "icon": "login",
                **common_editor_plugin_args,
            },
            js=[
                "wagtailadmin/js/page-chooser-modal.js",
                "govuk/js/draftail-govuk-button.js",
            ],
        ),
    )
    features.register_converter_rule(
        "contentstate",
        GOVUK_START_BUTTON_FEATURE,
        {
            "from_database_format": {
                f'a[linktype="{GOVUK_START_BUTTON_LINKTYPE}"]': GovukButtonLinkElementHandler(
                    GOVUK_START_BUTTON_ENTITY_TYPE
                ),
            },
            "to_database_format": {
                "entity_decorators": {
                    GOVUK_START_BUTTON_ENTITY_TYPE: govuk_start_button_entity
                }
            },
        },
    )

    features.register_editor_plugin(
        "draftail",
        RAW_HTML_FEATURE,
        draftail_features.EntityFeature(
            {
                "type": RAW_HTML_ENTITY_TYPE,
                "description": "Raw HTML block",
                "icon": "code",
            },
            js=["govuk/js/draftail-raw-html.js"],
        ),
    )
    features.register_converter_rule(
        "contentstate",
        RAW_HTML_FEATURE,
        {
            "from_database_format": {
                f'embed[embedtype="{RAW_HTML_EMBEDTYPE}"]': RawHtmlElementHandler(),
            },
            "to_database_format": {
                "entity_decorators": {RAW_HTML_ENTITY_TYPE: raw_html_entity}
            },
        },
    )

    features.register_editor_plugin(
        "draftail",
        INSET_TEXT_FEATURE,
        draftail_features.BlockFeature(
            {
                "type": INSET_TEXT_BLOCK_TYPE,
                "description": "Inset text",
                "icon": "openquote",
            }
        ),
    )
    features.register_converter_rule(
        "contentstate",
        INSET_TEXT_FEATURE,
        {
            "from_database_format": {
                'div[class="govuk-inset-text"]': BlockElementHandler(
                    INSET_TEXT_BLOCK_TYPE
                ),
            },
            "to_database_format": {
                "block_map": {
                    INSET_TEXT_BLOCK_TYPE: {
                        "element": "div",
                        "props": {"class": "govuk-inset-text"},
                    }
                }
            },
        },
    )


class GovukTagForm(forms.ModelForm):
    class Meta:
        model = GovukTag
        fields = ["slug", "name"]
        labels = {
            "slug": "Key",
            "name": "Value",
        }
        help_texts = {
            "slug": "Lowercase tag key, for example housing-benefit.",
            "name": "Human-readable label, for example Housing benefit.",
        }

    def clean_slug(self) -> str:
        slug = self.cleaned_data["slug"]
        return slug.strip().lower()


class GovukTagViewSet(SnippetViewSet):
    model = GovukTag
    form_class = GovukTagForm
    icon = "tag"
    add_to_admin_menu = True
    menu_label = "Tags"
    menu_name = "govuk-tags"
    menu_order = 200
    list_display = ["key", "value"]
    search_fields = ["slug", "name"]


class ExternalContentItemViewSet(SnippetViewSet):
    model = ExternalContentItem
    icon = "link"
    add_to_admin_menu = True
    menu_label = "External content"
    menu_name = "external-content"
    menu_order = 210
    list_display = [
        "title",
        "url",
        "source",
        "hidden",
        "updated_at",
        "last_seen_at",
    ]
    list_filter = ["hidden", "source"]
    search_fields = ["title", "url"]


class GovukSkillViewSet(SnippetViewSet):
    model = GovukSkill
    icon = "pick"
    add_to_admin_menu = True
    menu_label = "Skills"
    menu_name = "govuk-skills"
    menu_order = 215
    list_display = ["title", "slug"]
    search_fields = ["title", "slug", "body"]


class GovukRoleViewSet(SnippetViewSet):
    model = GovukRole
    icon = "user"
    add_to_admin_menu = True
    menu_label = "Roles"
    menu_name = "govuk-roles"
    menu_order = 216
    list_display = ["title", "family", "slug"]
    list_filter = ["family"]
    search_fields = ["title", "slug", "body", "family"]


class GovukChangelogEntryViewSet(SnippetViewSet):
    model = GovukChangelogEntry
    icon = "history"
    add_to_admin_menu = True
    menu_label = "Changelog"
    menu_name = "govuk-changelog"
    menu_order = 217
    list_display = ["date", "role", "skill", "change_type", "live"]
    list_filter = ["live", "date"]
    search_fields = ["note", "change_type"]
    ordering = ["-date"]


class FeedbackIndexView(SnippetIndexView):
    def _get_title_column(self, *args, **kwargs):
        column = super()._get_title_column(*args, **kwargs)
        column._get_url_func = lambda instance: self.get_inspect_url(
            instance
        ) or self.get_edit_url(instance)
        return column


class FeedbackViewSet(SnippetViewSet):
    model = Feedback
    index_view_class = FeedbackIndexView
    icon = "doc-full"
    add_to_admin_menu = True
    menu_label = "Feedback"
    menu_name = "feedback"
    menu_order = 220
    ordering = ["-created_at", "-id"]
    list_display = [
        "name",
        "feedback_type_label",
        "comments_preview",
        "created_at",
    ]
    inspect_view_enabled = True
    inspect_view_fields = [
        "name",
        "email",
        "feedback_type",
        "comments",
        "referrer",
        "browser",
        "is_mobile",
        "created_at",
        "user",
    ]
    search_fields = ["name", "email", "comments", "referrer", "browser"]


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
    if raw_site_id.isdigit():
        selected_site = next(
            (site for site in sites if site.pk == int(raw_site_id)),
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
        raw_value = (raw_id or "").strip()
        if raw_value.isdigit():
            selected_ids.append(int(raw_value))
    return selected_ids


def _page_rows_for_site(site: Site) -> list[dict]:
    root_page = site.root_page.specific
    pages = (
        Page.objects.descendant_of(root_page, inclusive=False)
        .specific()
        .order_by("path")
    )
    return [
        {
            "id": page.pk,
            "title": page.title,
            "slug": page.slug,
            "depth": max(page.depth - root_page.depth - 1, 0),
            "model_label": page._meta.label,
            "is_private": page.view_restrictions.exists(),
        }
        for page in pages
    ]


def _skill_rows() -> list[dict]:
    return list(
        GovukSkill.objects.order_by("title", "slug").values(
            "id",
            "title",
            "slug",
        )
    )


def _role_rows() -> list[dict]:
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
            "page_rows": _page_rows_for_site(selected_site),
            "skills_feature_enabled": skills_feature_enabled,
            "skill_rows": _skill_rows() if skills_feature_enabled else [],
            "role_rows": _role_rows() if skills_feature_enabled else [],
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
        if skills_feature_enabled:
            messages.error(
                request, "Select at least one page, skill or role to export."
            )
        else:
            messages.error(request, "Select at least one page to export.")
        return redirect(redirect_url)

    selected_pages = list(
        Page.objects.descendant_of(selected_site.root_page, inclusive=False)
        .filter(pk__in=selected_page_ids)
        .specific()
        .order_by("path")
    )
    selected_skills = (
        list(
            GovukSkill.objects.filter(pk__in=selected_skill_ids).order_by(
                "title", "slug"
            )
        )
        if skills_feature_enabled
        else []
    )
    selected_roles = (
        list(
            GovukRole.objects.filter(pk__in=selected_role_ids).order_by("title", "slug")
        )
        if skills_feature_enabled
        else []
    )

    if not selected_pages and not selected_skills and not selected_roles:
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
    )
    file_contents = dump_payload_as_json(payload)
    timestamp = timezone.now().strftime("%Y%m%d-%H%M%S")
    file_name = f"pages-export-site-{selected_site.pk}-{timestamp}.json"

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
    messages.success(
        request,
        (
            "Import complete. "
            f"Processed {result.processed}, created {result.created}, "
            f"updated {result.updated}, skipped {result.skipped}."
        ),
    )
    if result.errors:
        preview = "; ".join(result.errors[:3])
        if len(result.errors) > 3:
            preview = f"{preview}; and {len(result.errors) - 3} more."
        messages.warning(request, f"Some items were skipped: {preview}")

    return redirect(redirect_url)


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
    ]


def _register_snippet_if_needed(viewset):
    try:
        register_snippet(viewset)
    except ImproperlyConfigured as exc:
        if "already registered as a snippet" not in str(exc):
            raise


_register_snippet_if_needed(GovukTagViewSet)
_register_snippet_if_needed(ExternalContentItemViewSet)
if settings.FEATURE_FLAGS.get("SKILLS"):
    _register_snippet_if_needed(GovukSkillViewSet)
    _register_snippet_if_needed(GovukRoleViewSet)
    _register_snippet_if_needed(GovukChangelogEntryViewSet)
if settings.FEATURE_FLAGS.get("FEEDBACK"):
    _register_snippet_if_needed(FeedbackViewSet)
