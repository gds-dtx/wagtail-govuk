import json
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from uuid import UUID

from django.apps import apps
from django.contrib.auth.models import Group
from django.core.exceptions import FieldDoesNotExist, ValidationError
from django.db import transaction
from django.db import models as django_models
from django.utils.dateparse import parse_date, parse_datetime, parse_time
from django.utils.text import slugify
from django.utils import timezone
from wagtail.fields import StreamField
from wagtail.models import Page, PageViewRestriction, Site

from govuk.models import GovukTag

PAGE_EXPORT_FORMAT = "govuk-page-import-export/v1"
BASE_PAGE_LOCAL_FIELD_NAMES = {field.name for field in Page._meta.local_fields}
CORE_PAGE_SETTING_FIELDS = (
    "title",
    "draft_title",
    "slug",
    "seo_title",
    "search_description",
    "show_in_menus",
    "go_live_at",
    "expire_at",
)


@dataclass
class PageImportResult:
    processed: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


def build_page_export_payload(*, site: Site, pages: list[Page]) -> dict:
    selected_roots = _deduplicate_selected_roots(pages)
    return {
        "format": PAGE_EXPORT_FORMAT,
        "exported_at": timezone.now().isoformat(),
        "site": {
            "id": site.id,
            "hostname": site.hostname,
            "port": site.port,
            "is_default_site": site.is_default_site,
        },
        "pages": [_serialise_page_tree(page) for page in selected_roots],
    }


def import_pages_from_payload(*, payload: dict, site: Site, user) -> PageImportResult:
    result = PageImportResult()
    if not isinstance(payload, dict):
        result.skipped += 1
        result.errors.append("Import payload must be a JSON object.")
        return result

    raw_pages = payload.get("pages")
    if not isinstance(raw_pages, list):
        result.skipped += 1
        result.errors.append("Payload must contain a 'pages' array.")
        return result

    site_root = site.root_page.specific
    for node in raw_pages:
        _import_page_node(
            node=node,
            parent_page=site_root,
            site_root=site_root,
            user=user,
            result=result,
        )
    return result


def dump_payload_as_json(payload: dict) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False)


def _deduplicate_selected_roots(pages: list[Page]) -> list[Page]:
    ordered_pages = sorted((page.specific for page in pages), key=lambda page: page.path)
    roots: list[Page] = []
    for page in ordered_pages:
        if any(page.path.startswith(existing.path) for existing in roots):
            continue
        roots.append(page)
    return roots


def _serialise_page_tree(page: Page) -> dict:
    specific_page = page.specific
    return {
        "model": specific_page._meta.label,
        "settings": _serialise_page_settings(specific_page),
        "fields": _serialise_page_fields(specific_page),
        "tags": _serialise_tags(specific_page),
        "privacy": _serialise_privacy(specific_page),
        "children": [
            _serialise_page_tree(child.specific)
            for child in specific_page.get_children().order_by("path")
        ],
    }


def _serialise_page_settings(page: Page) -> dict:
    values: dict[str, object] = {}
    for field_name in CORE_PAGE_SETTING_FIELDS:
        values[field_name] = _serialise_value(getattr(page, field_name))
    return values


def _serialise_page_fields(page: Page) -> dict:
    values: dict[str, object] = {}
    for field in page._meta.local_fields:
        if field.name in BASE_PAGE_LOCAL_FIELD_NAMES or field.name == "page_ptr":
            continue
        if field.auto_created:
            continue

        raw_value = field.value_from_object(page)
        if isinstance(field, StreamField):
            serialisable = field.get_prep_value(raw_value)
        else:
            serialisable = field.get_prep_value(raw_value)
        values[field.name] = _serialise_value(serialisable)
    return values


def _serialise_tags(page: Page) -> list[dict[str, str]]:
    if not hasattr(page, "tags"):
        return []

    tags_payload: list[dict[str, str]] = []
    for tag in page.tags.all().order_by("slug"):
        tag_slug = (getattr(tag, "slug", "") or "").strip()
        if not tag_slug:
            continue
        tag_name = (getattr(tag, "name", "") or "").strip() or tag_slug
        tags_payload.append(
            {
                "slug": tag_slug,
                "name": tag_name,
            }
        )
    return tags_payload


def _serialise_privacy(page: Page) -> list[dict]:
    restrictions_payload: list[dict] = []
    for restriction in page.view_restrictions.all():
        row: dict[str, object] = {"type": restriction.restriction_type}
        if restriction.restriction_type == PageViewRestriction.PASSWORD:
            row["password"] = restriction.password
        if restriction.restriction_type == PageViewRestriction.GROUPS:
            row["groups"] = list(
                restriction.groups.order_by("name").values_list("name", flat=True)
            )
        restrictions_payload.append(row)
    return restrictions_payload


def _serialise_value(value):
    if value is None:
        return None
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: _serialise_value(inner_value) for key, inner_value in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialise_value(inner_value) for inner_value in value]
    return value


def _import_page_node(*, node, parent_page: Page, site_root: Page, user, result: PageImportResult):
    result.processed += 1
    if not isinstance(node, dict):
        result.skipped += 1
        result.errors.append("Skipped page entry because it is not an object.")
        return

    model_label = str(node.get("model") or "").strip()
    model_class = _resolve_model_class(model_label)
    if model_class is None:
        result.skipped += 1
        result.errors.append(f"Skipped page with unknown model '{model_label}'.")
        return

    raw_settings = node.get("settings")
    if not isinstance(raw_settings, dict):
        raw_settings = {}

    slug = _normalised_slug(raw_settings.get("slug"))
    if not slug:
        result.skipped += 1
        result.errors.append(
            f"Skipped page for model '{model_label}' because the slug is missing."
        )
        return

    try:
        with transaction.atomic():
            page, action = _find_or_create_page(
                slug=slug,
                model_class=model_class,
                parent_page=parent_page,
                site_root=site_root,
                user=user,
            )
            _apply_page_settings(page, raw_settings)
            _apply_page_fields(page, node.get("fields"))
            page.full_clean()
            page.save()
            _apply_tags(page, node.get("tags"))
            page.save()
            _apply_privacy(page, node.get("privacy"), user=user)

            if action == "create":
                result.created += 1
            else:
                result.updated += 1

            child_entries = node.get("children")
            if isinstance(child_entries, list):
                imported_parent = page.specific
                for child_node in child_entries:
                    _import_page_node(
                        node=child_node,
                        parent_page=imported_parent,
                        site_root=site_root,
                        user=user,
                        result=result,
                    )
    except (ValidationError, ValueError, PermissionError) as exc:
        result.skipped += 1
        result.errors.append(f"Skipped '{slug}': {exc}")


def _find_or_create_page(*, slug: str, model_class, parent_page: Page, site_root: Page, user):
    existing_page = _find_page_by_slug(
        slug=slug,
        site_root=site_root,
        preferred_parent=parent_page,
    )
    if existing_page is None:
        if not parent_page.permissions_for_user(user).can_add_subpage():
            raise PermissionError(
                f"You do not have permission to create pages under '{parent_page.title}'."
            )
        new_title = slug.replace("-", " ").strip().title() or slug
        new_page = model_class(title=new_title, slug=slug, draft_title=new_title)
        parent_page.add_child(instance=new_page)
        return new_page.specific, "create"

    specific_existing = existing_page.specific
    if specific_existing.specific_class is not model_class:
        raise ValidationError(
            f"Existing page '{slug}' has model '{specific_existing._meta.label}', "
            f"but import entry expects '{model_class._meta.label}'."
        )
    if not specific_existing.permissions_for_user(user).can_edit():
        raise PermissionError(f"You do not have permission to edit '{slug}'.")

    current_parent = specific_existing.get_parent()
    if current_parent.pk != parent_page.pk:
        if not specific_existing.permissions_for_user(user).can_move():
            raise PermissionError(f"You do not have permission to move '{slug}'.")
        if parent_page.is_descendant_of(specific_existing):
            raise ValidationError(
                f"Cannot move '{slug}' under '{parent_page.slug}' because that would create a loop."
            )
        specific_existing.move(parent_page, pos="last-child")
    return specific_existing.specific, "update"


def _find_page_by_slug(*, slug: str, site_root: Page, preferred_parent: Page | None) -> Page | None:
    queryset = Page.objects.descendant_of(site_root, inclusive=False).filter(slug=slug)

    if preferred_parent is not None:
        sibling_match = queryset.child_of(preferred_parent).order_by("path").first()
        if sibling_match is not None:
            return sibling_match

    return queryset.order_by("path").first()


def _apply_page_settings(page: Page, settings_data: dict):
    for field_name in CORE_PAGE_SETTING_FIELDS:
        if field_name not in settings_data:
            continue
        field_value = settings_data.get(field_name)
        if field_name == "slug":
            normalised_slug = _normalised_slug(field_value)
            if normalised_slug:
                page.slug = normalised_slug
            continue
        if field_name in {"title", "draft_title", "seo_title", "search_description"}:
            if field_value is None:
                field_value = ""
            setattr(page, field_name, str(field_value))
            continue
        if field_name == "show_in_menus":
            page.show_in_menus = _coerce_bool(field_value)
            continue
        if field_name in {"go_live_at", "expire_at"}:
            parsed_datetime = _parse_datetime_or_none(field_value)
            setattr(page, field_name, parsed_datetime)
            continue

    page.draft_title = page.draft_title or page.title


def _apply_page_fields(page: Page, fields_data):
    if not isinstance(fields_data, dict):
        return

    for field_name, raw_value in fields_data.items():
        try:
            model_field = page._meta.get_field(field_name)
        except FieldDoesNotExist:
            continue

        if not isinstance(model_field, django_models.Field):
            continue
        if model_field.name in BASE_PAGE_LOCAL_FIELD_NAMES or model_field.auto_created:
            continue
        if model_field.many_to_many or model_field.one_to_many:
            continue

        python_value = _deserialise_model_field_value(model_field, raw_value)
        setattr(page, field_name, python_value)


def _apply_tags(page: Page, tags_data):
    if not hasattr(page, "tags"):
        return

    if not isinstance(tags_data, list):
        return

    tag_ids: list[int] = []
    for raw_tag in tags_data:
        if isinstance(raw_tag, dict):
            raw_slug = raw_tag.get("slug")
            raw_name = raw_tag.get("name")
        else:
            raw_slug = raw_tag
            raw_name = raw_tag

        tag_slug = _normalised_slug(raw_slug)
        if not tag_slug:
            continue
        tag_name = (str(raw_name or "").strip() or tag_slug).strip()

        tag_obj, _ = GovukTag.objects.get_or_create(
            slug=tag_slug,
            defaults={"name": tag_name},
        )
        if not tag_obj.name and tag_name:
            tag_obj.name = tag_name
            tag_obj.save(update_fields=["name"])
        tag_ids.append(tag_obj.id)

    tag_objects = list(GovukTag.objects.filter(id__in=tag_ids))
    page.tags.set(tag_objects)


def _apply_privacy(page: Page, privacy_data, *, user):
    page.view_restrictions.all().delete()
    if not isinstance(privacy_data, list):
        return

    for raw_restriction in privacy_data:
        if not isinstance(raw_restriction, dict):
            continue
        restriction_type = (raw_restriction.get("type") or "").strip()
        if restriction_type not in {
            PageViewRestriction.PASSWORD,
            PageViewRestriction.LOGIN,
            PageViewRestriction.GROUPS,
        }:
            continue

        restriction = PageViewRestriction(
            page=page,
            restriction_type=restriction_type,
            password="",
        )
        if restriction_type == PageViewRestriction.PASSWORD:
            restriction.password = str(raw_restriction.get("password") or "")
        restriction.save(user=user)

        if restriction_type == PageViewRestriction.GROUPS:
            groups_to_apply: list[Group] = []
            for raw_group_name in raw_restriction.get("groups", []):
                group_name = (str(raw_group_name or "").strip())[:150]
                if not group_name:
                    continue
                group, _ = Group.objects.get_or_create(name=group_name)
                groups_to_apply.append(group)
            restriction.groups.set(groups_to_apply)


def _deserialise_model_field_value(field: django_models.Field, raw_value):
    if raw_value is None:
        if field.empty_strings_allowed:
            return ""
        return None

    if isinstance(field, StreamField):
        return field.to_python(raw_value)
    if isinstance(field, django_models.DateTimeField):
        parsed_datetime = _parse_datetime_or_none(raw_value)
        return parsed_datetime
    if isinstance(field, django_models.DateField):
        if isinstance(raw_value, date):
            return raw_value
        parsed_date = parse_date(str(raw_value))
        if parsed_date is None:
            raise ValidationError(f"Invalid date value '{raw_value}' for '{field.name}'.")
        return parsed_date
    if isinstance(field, django_models.TimeField):
        if isinstance(raw_value, time):
            return raw_value
        parsed_time = parse_time(str(raw_value))
        if parsed_time is None:
            raise ValidationError(f"Invalid time value '{raw_value}' for '{field.name}'.")
        return parsed_time

    return field.to_python(raw_value)


def _resolve_model_class(model_label: str):
    if not model_label:
        return None
    try:
        model_class = apps.get_model(model_label)
    except (LookupError, ValueError):
        return None
    if not isinstance(model_class, type) or not issubclass(model_class, Page):
        return None
    return model_class


def _normalised_slug(value) -> str:
    slug = slugify(str(value or "").strip())
    return slug[:255]


def _coerce_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    value_str = str(value or "").strip().lower()
    return value_str in {"1", "true", "yes", "y", "on"}


def _parse_datetime_or_none(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    parsed_datetime = parse_datetime(str(value))
    if parsed_datetime is None:
        raise ValidationError(f"Invalid datetime value '{value}'.")
    return parsed_datetime
