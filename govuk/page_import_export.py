import json
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from uuid import UUID

from django.apps import apps
from django.conf import settings
from django.contrib.auth.models import Group
from django.core.exceptions import FieldDoesNotExist, ValidationError
from django.db import transaction
from django.db import models as django_models
from django.utils.dateparse import parse_date, parse_datetime, parse_time
from django.utils.text import slugify
from django.utils import timezone
from wagtail.fields import StreamField
from wagtail.models import Page, PageViewRestriction, Site
from wagtail.permission_policies import ModelPermissionPolicy
from wagtail.rich_text import RichText

from govuk.models import (
    JOB_GRADE_CHOICES,
    SCS_GRADE_CHOICES,
    GovukChangelogEntry,
    GovukRole,
    GovukSkill,
    GovukTag,
)

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
SKILL_POINT_FIELD_NAMES = (
    "awareness_points",
    "working_points",
    "practitioner_points",
    "expert_points",
    "leadership_points",
)
SKILL_LEVEL_CHOICES = {"awareness", "working", "practitioner", "expert"}
# A page's snippet chooser holds a primary key, which means nothing in another
# database. Carry these as slugs so the reference survives the move, the way
# role levels and Senior Civil Service skills already do.
PAGE_SNIPPET_SLUG_STREAM_FIELDS = {
    ("govuk.RolePage", "selected_roles"): ("role", GovukRole),
}
JOB_GRADE_KEYS = {value for value, _ in JOB_GRADE_CHOICES}
SCS_GRADE_KEYS = {value for value, _ in SCS_GRADE_CHOICES}


@dataclass
class PageImportResult:
    processed: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def build_page_export_payload(
    *,
    site: Site,
    pages: list[Page],
    skills: list[GovukSkill] | None = None,
    roles: list[GovukRole] | None = None,
) -> dict:
    selected_roots = _deduplicate_selected_roots(pages)
    payload = {
        "format": PAGE_EXPORT_FORMAT,
        "exported_at": timezone.now().isoformat(),
        "site": {
            "id": site.id,
            "hostname": site.hostname,
            "port": site.port,
            "site_name": site.site_name,
            "is_default_site": site.is_default_site,
        },
        "pages": [_serialise_page_tree(page) for page in selected_roots],
    }
    if settings.FEATURE_FLAGS.get("SKILLS"):
        payload["skills"] = [
            _serialise_skill(skill)
            for skill in sorted(
                (skills or []),
                key=lambda row: (
                    (row.title or "").strip().lower(),
                    (row.slug or "").strip().lower(),
                    row.pk or 0,
                ),
            )
        ]
        payload["roles"] = [
            _serialise_role(role)
            for role in sorted(
                (roles or []),
                key=lambda row: (
                    (row.title or "").strip().lower(),
                    (row.slug or "").strip().lower(),
                    row.pk or 0,
                ),
            )
        ]
        # Entries attached to no role or skill are framework-wide and belong
        # to the home page rather than to any one exported snippet.
        payload["changelog"] = _serialise_changelog(
            GovukChangelogEntry.objects.filter(role__isnull=True, skill__isnull=True)
        )
    return payload


def import_pages_from_payload(*, payload: dict, site: Site, user) -> PageImportResult:
    result = PageImportResult()
    if not isinstance(payload, dict):
        result.skipped += 1
        result.errors.append("Import payload must be a JSON object.")
        return result

    raw_tags = payload.get("tags", [])
    if raw_tags is None:
        raw_tags = []
    if not isinstance(raw_tags, list):
        result.skipped += 1
        result.errors.append("Payload 'tags' value must be an array when provided.")
        return result

    raw_pages = payload.get("pages", [])
    if raw_pages is None:
        raw_pages = []
    if not isinstance(raw_pages, list):
        result.skipped += 1
        result.errors.append("Payload must contain a 'pages' array.")
        return result

    raw_skills: list = []
    raw_roles: list = []
    if settings.FEATURE_FLAGS.get("SKILLS"):
        raw_skills = payload.get("skills", []) or []
        raw_roles = payload.get("roles", []) or []
        if not isinstance(raw_skills, list):
            result.skipped += 1
            result.errors.append("Payload 'skills' value must be an array when provided.")
            return result
        if not isinstance(raw_roles, list):
            result.skipped += 1
            result.errors.append("Payload 'roles' value must be an array when provided.")
            return result

    if not raw_pages and not raw_skills and not raw_roles and not raw_tags:
        result.skipped += 1
        if settings.FEATURE_FLAGS.get("SKILLS"):
            result.errors.append(
                "Payload must contain at least one entry in 'tags', 'pages', 'skills' or 'roles'."
            )
        else:
            result.errors.append("Payload must contain at least one entry in 'tags' or 'pages'.")
        return result

    _import_site_name(payload.get("site"), site=site)

    tag_lookup = _import_tags_from_payload(raw_tags=raw_tags, raw_pages=raw_pages)

    if settings.FEATURE_FLAGS.get("SKILLS"):
        _import_skills(raw_skills, user=user, result=result)
        _import_roles(raw_roles, user=user, result=result)
        _import_site_wide_changelog(
            payload.get("changelog"), user=user, result=result
        )

    site_root = _replace_placeholder_home_page(
        raw_pages,
        site=site,
        site_root=site.root_page.specific,
        user=user,
        result=result,
    )
    for node in raw_pages:
        _import_page_node(
            node=node,
            parent_page=site_root,
            site_root=site_root,
            user=user,
            result=result,
            tag_lookup=tag_lookup,
        )
    return result


def dump_payload_as_json(payload: dict) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False)


def user_may(user, model, action: str) -> bool:
    """Whether the user could do this to a snippet through its own admin.

    Snippets are governed by Django's model permissions, which is what the
    snippet views ask for. The pages in a file are already checked one by one
    against the page permissions, so a file was the way round the snippet
    permissions and only the snippet permissions: an account with nothing but
    access to the admin could rewrite every skill and role in the framework by
    uploading one, while the snippet menu it would have used stayed shut.
    """
    if user is None:
        return False
    return ModelPermissionPolicy(model).user_has_permission(user, action)


def _permission_error(model, action: str, subject: str) -> str:
    """Why an entry was left alone, and the permission that would allow it.

    Named in full because the person who ran the import is rarely the person
    who administers groups, and "you do not have permission" on its own leaves
    them nothing to ask for.
    """
    codename = f"{model._meta.app_label}.{action}_{model._meta.model_name}"
    return (
        f"Skipped {subject} because you do not have permission to {action} "
        f"{model._meta.verbose_name_plural} ({codename})."
    )


def _import_site_name(raw_site, *, site: Site):
    """Carry the site name over.

    The name is content: it is the product name in the header and the suffix
    on every page title. The hostname and port belong to the environment being
    imported into, so they are left alone.
    """
    if not isinstance(raw_site, dict):
        return

    site_name = str(raw_site.get("site_name") or "").strip()[:255]
    if site_name and site_name != site.site_name:
        site.site_name = site_name
        site.save(update_fields=["site_name"])


def _replace_placeholder_home_page(raw_pages: list, *, site: Site, site_root: Page, user, result: PageImportResult) -> Page:
    """Swap a new instance's placeholder home page for the one being imported.

    A fresh instance ships an empty home page of whatever type the starter site
    uses, and Wagtail cannot change a page's type in place. The page therefore
    has to be rebuilt and the site repointed at it. Without this the import has
    nowhere to put its own home page, so it nests the whole site a level down
    and every URL gains a /home/ prefix.

    Only an empty placeholder is replaced. A home page that already has children
    belongs to someone, and is left alone for _find_or_create_page to deal with.
    """
    if not raw_pages or not isinstance(raw_pages[0], dict):
        return site_root

    node = raw_pages[0]
    raw_settings = node.get("settings")
    if not isinstance(raw_settings, dict):
        raw_settings = {}

    slug = _normalised_slug(raw_settings.get("slug"))
    model_class = _resolve_model_class(str(node.get("model") or "").strip())
    if (
        model_class is None
        or not slug
        or slug != site_root.slug
        or site_root.specific_class is model_class
        # Page.objects, not site_root.get_children(): called on a specific page
        # that narrows to the same subclass, and would miss children of any
        # other type.
        or Page.objects.child_of(site_root).exists()
    ):
        return site_root

    tree_root = site_root.get_parent()
    if tree_root is None:
        return site_root

    placeholder_label = site_root._meta.label
    title = str(raw_settings.get("title") or "").strip() or slug.replace("-", " ").title()

    try:
        with transaction.atomic():
            # Build the replacement alongside the placeholder, which still owns
            # the slug, then take the slug over once the placeholder is gone.
            replacement = model_class(
                title=title,
                draft_title=title,
                slug=f"{slug}-import",
            )
            tree_root.add_child(instance=replacement)
            replacement.save_revision().publish()

            # Repoint the site before the delete: a page that is still a site
            # root cannot be removed, and the delete cascades to descendants.
            site.root_page = replacement
            site.save(update_fields=["root_page"])

            site_root.refresh_from_db()
            site_root.delete()

            replacement.refresh_from_db()
            replacement.slug = slug
            replacement.save()
            replacement.save_revision().publish()
    except (ValidationError, ValueError) as exc:
        result.errors.append(f"Could not replace the placeholder home page: {exc}")
        return site_root

    result.notes.append(
        f"Replaced the empty placeholder home page ({placeholder_label}) with "
        f"{model_class._meta.label} so the site imports at the top level."
    )
    return replacement.specific


def _import_tags_from_payload(*, raw_tags: list, raw_pages: list) -> dict[str, dict[str, int]]:
    tag_candidates = _collect_tag_candidates(raw_tags=raw_tags, raw_pages=raw_pages)
    ordered_candidates: dict[str, str] = {}
    for tag_slug, tag_name in tag_candidates:
        if tag_slug not in ordered_candidates:
            ordered_candidates[tag_slug] = tag_name

    if not ordered_candidates:
        return {"by_slug": {}, "by_name": {}}

    candidate_slugs = list(ordered_candidates.keys())
    existing_slugs = set(
        GovukTag.objects.filter(slug__in=candidate_slugs).values_list("slug", flat=True)
    )
    tags_to_create = [
        GovukTag(slug=tag_slug, name=tag_name or tag_slug)
        for tag_slug, tag_name in ordered_candidates.items()
        if tag_slug not in existing_slugs
    ]
    if tags_to_create:
        GovukTag.objects.bulk_create(tags_to_create, ignore_conflicts=True)

    lookup: dict[str, dict[str, int]] = {"by_slug": {}, "by_name": {}}
    for tag in GovukTag.objects.filter(slug__in=candidate_slugs).only("id", "slug", "name"):
        _update_tag_lookup(lookup, tag_id=tag.id, tag_slug=tag.slug, tag_name=tag.name)

    for tag_slug, tag_name in ordered_candidates.items():
        tag_id = lookup["by_slug"].get(tag_slug)
        if tag_id is not None:
            _update_tag_lookup(
                lookup,
                tag_id=tag_id,
                tag_slug=tag_slug,
                tag_name=tag_name,
            )

    return lookup


def _collect_tag_candidates(*, raw_tags: list, raw_pages: list) -> list[tuple[str, str]]:
    candidates = _extract_tag_candidates_from_tag_list(raw_tags)
    if not isinstance(raw_pages, list):
        return candidates

    for node in raw_pages:
        _collect_page_node_tag_candidates(node=node, candidates=candidates)
    return candidates


def _collect_page_node_tag_candidates(*, node, candidates: list[tuple[str, str]]):
    if not isinstance(node, dict):
        return

    candidates.extend(_extract_tag_candidates_from_tag_list(node.get("tags")))

    fields_data = node.get("fields")
    if isinstance(fields_data, dict):
        candidates.extend(_extract_tag_candidates_from_rows(fields_data.get("rows")))

    child_entries = node.get("children")
    if not isinstance(child_entries, list):
        return
    for child_node in child_entries:
        _collect_page_node_tag_candidates(node=child_node, candidates=candidates)


def _extract_tag_candidates_from_tag_list(raw_tags) -> list[tuple[str, str]]:
    if not isinstance(raw_tags, list):
        return []

    candidates: list[tuple[str, str]] = []
    for raw_tag in raw_tags:
        if isinstance(raw_tag, dict):
            raw_slug = raw_tag.get("slug") or raw_tag.get("key")
            raw_name = raw_tag.get("name") or raw_tag.get("label")
            if not raw_name and isinstance(raw_tag.get("value"), str):
                raw_name = raw_tag.get("value")
        elif isinstance(raw_tag, str):
            raw_slug = raw_tag
            raw_name = raw_tag
        else:
            continue

        candidate = _normalised_tag_candidate(raw_slug=raw_slug, raw_name=raw_name)
        if candidate is not None:
            candidates.append(candidate)
    return candidates


def _extract_tag_candidates_from_rows(raw_rows) -> list[tuple[str, str]]:
    if not isinstance(raw_rows, list):
        return []

    candidates: list[tuple[str, str]] = []
    for raw_row in raw_rows:
        if not isinstance(raw_row, dict):
            continue
        row_value = raw_row.get("value")
        if not isinstance(row_value, dict):
            continue
        raw_cards = row_value.get("cards")
        if not isinstance(raw_cards, list):
            continue

        for raw_card in raw_cards:
            if not isinstance(raw_card, dict):
                continue
            card_value = raw_card.get("value")
            if not isinstance(card_value, dict):
                continue
            card_tags = card_value.get("tags")
            if not isinstance(card_tags, list):
                continue

            for raw_card_tag in card_tags:
                raw_tag_value = raw_card_tag.get("value") if isinstance(raw_card_tag, dict) else raw_card_tag
                if isinstance(raw_tag_value, dict):
                    raw_slug = raw_tag_value.get("slug") or raw_tag_value.get("key")
                    raw_name = raw_tag_value.get("name") or raw_tag_value.get("label")
                    if not raw_name and isinstance(raw_tag_value.get("value"), str):
                        raw_name = raw_tag_value.get("value")
                elif isinstance(raw_tag_value, str):
                    raw_slug = raw_tag_value
                    raw_name = raw_tag_value
                else:
                    continue

                candidate = _normalised_tag_candidate(raw_slug=raw_slug, raw_name=raw_name)
                if candidate is not None:
                    candidates.append(candidate)
    return candidates


def _normalised_tag_candidate(*, raw_slug, raw_name) -> tuple[str, str] | None:
    tag_slug = _normalised_slug(raw_slug or raw_name)
    if not tag_slug:
        return None
    tag_name = (str(raw_name or raw_slug or tag_slug).strip() or tag_slug).strip()
    return tag_slug, tag_name


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


def _serialise_skill(skill: GovukSkill) -> dict:
    payload = {
        "slug": skill.slug,
        "title": skill.title,
        "body": _serialise_value(skill.body),
        "is_senior_civil_service": skill.is_senior_civil_service,
    }
    for field_name in SKILL_POINT_FIELD_NAMES:
        payload[field_name] = _serialise_stream_field(skill, field_name)
    payload["changelog"] = _serialise_changelog(skill.changelog_entries.all())
    return payload


def _serialise_role(role: GovukRole) -> dict:
    return {
        "slug": role.slug,
        "title": role.title,
        "family": role.family,
        "body": _serialise_value(role.body),
        "levels": _serialise_role_levels(role),
        "is_senior_civil_service": role.is_senior_civil_service,
        "scs_grades": _serialise_choice_stream(role.scs_grades, "grade"),
        "scs_skills": _serialise_scs_skills(role),
        "changelog": _serialise_changelog(role.changelog_entries.all()),
    }


def _serialise_choice_stream(stream_value, block_type: str) -> list[str]:
    """Choice keys from a StreamField of single-choice blocks."""
    return [
        str(block.value).strip()
        for block in stream_value
        if block.block_type == block_type and str(block.value or "").strip()
    ]


def _serialise_scs_skills(role: GovukRole) -> list[str]:
    """Slugs of the skills a Senior Civil Service role requires."""
    return [
        block.value.slug
        for block in role.scs_skills
        if block.block_type == "skill" and block.value is not None
    ]


def _serialise_changelog(entries) -> list[dict]:
    """Changelog entries belonging to one role, skill, or the framework."""
    return [
        {
            "date": _serialise_value(entry.date),
            "change_type": entry.change_type,
            "note": _serialise_value(entry.note),
            "live": entry.live,
        }
        for entry in entries.order_by("-date", "pk")
    ]


def _serialise_stream_field(instance, field_name: str):
    model_field = instance._meta.get_field(field_name)
    raw_value = model_field.value_from_object(instance)
    return _serialise_value(model_field.get_prep_value(raw_value))


def _serialise_role_levels(role: GovukRole) -> list[dict]:
    levels_payload: list[dict] = []
    for level_block in role.levels:
        if level_block.block_type != "level":
            continue

        level_value = level_block.value
        skill_requirements_payload: list[dict[str, str]] = []
        for raw_skill_requirement in level_value.get("skills") or []:
            skill_slug = _skill_slug_from_value(raw_skill_requirement.get("skill"))
            required_level = _normalised_skill_level(raw_skill_requirement.get("level"))
            if not skill_slug or not required_level:
                continue
            skill_requirements_payload.append(
                {
                    "skill_slug": skill_slug,
                    "level": required_level,
                }
            )

        levels_payload.append(
            {
                "title": str(level_value.get("title") or "").strip(),
                "description": _serialise_value(level_value.get("description")),
                "grades": [
                    str(getattr(raw_grade, "value", raw_grade) or "").strip()
                    for raw_grade in (level_value.get("grades") or [])
                    if str(getattr(raw_grade, "value", raw_grade) or "").strip()
                ],
                "skills": skill_requirements_payload,
            }
        )
    return levels_payload


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
        serialisable = field.get_prep_value(raw_value)

        snippet_stream = PAGE_SNIPPET_SLUG_STREAM_FIELDS.get(
            (page._meta.label, field.name)
        )
        if snippet_stream is not None:
            block_type, snippet_model = snippet_stream
            serialisable = _serialise_snippet_chooser_stream(
                serialisable,
                block_type=block_type,
                snippet_model=snippet_model,
            )

        values[field.name] = _serialise_value(serialisable)
    return values


def _serialise_snippet_chooser_stream(raw_blocks, *, block_type: str, snippet_model) -> list:
    """Swap the primary keys in a page's chooser stream for slugs."""
    if not isinstance(raw_blocks, list):
        return raw_blocks

    chosen_keys = [
        block.get("value")
        for block in raw_blocks
        if isinstance(block, dict) and block.get("type") == block_type
    ]
    slugs_by_key = dict(
        snippet_model.objects.filter(
            pk__in=[key for key in chosen_keys if isinstance(key, int)]
        ).values_list("pk", "slug")
    )

    blocks_payload: list = []
    for block in raw_blocks:
        if not isinstance(block, dict) or block.get("type") != block_type:
            blocks_payload.append(block)
            continue

        slug = slugs_by_key.get(block.get("value"))
        if not slug:
            continue
        blocks_payload.append({**block, "value": slug})
    return blocks_payload


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
    if isinstance(value, RichText):
        return value.source
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


def _import_skills(raw_skills: list, *, user, result: PageImportResult):
    for node in raw_skills:
        _import_skill_node(node=node, user=user, result=result)


def _import_skill_node(*, node, user, result: PageImportResult):
    result.processed += 1
    if not isinstance(node, dict):
        result.skipped += 1
        result.errors.append("Skipped skill entry because it is not an object.")
        return

    slug = _normalised_slug(node.get("slug") or node.get("title"))
    if not slug:
        result.skipped += 1
        result.errors.append("Skipped skill entry because the slug is missing.")
        return

    existing_skill = GovukSkill.objects.filter(slug=slug).first()
    if existing_skill is None:
        action = "create"
        skill = GovukSkill(slug=slug)
    else:
        action = "update"
        skill = existing_skill

    required = "add" if action == "create" else "change"
    if not user_may(user, GovukSkill, required):
        result.skipped += 1
        result.errors.append(_permission_error(GovukSkill, required, f"skill '{slug}'"))
        return

    default_title = slug.replace("-", " ").strip().title() or slug
    skill.slug = slug
    skill.title = str(node.get("title") or skill.title or default_title).strip() or default_title
    skill.body = str(node.get("body") or "")
    skill.is_senior_civil_service = _coerce_bool(node.get("is_senior_civil_service"))
    for field_name in SKILL_POINT_FIELD_NAMES:
        setattr(skill, field_name, _deserialise_skill_points(node.get(field_name)))

    try:
        skill.full_clean()
        skill.save()
    except (ValidationError, ValueError) as exc:
        result.skipped += 1
        result.errors.append(f"Skipped skill '{slug}': {exc}")
        return

    _import_changelog(
        node.get("changelog"), subject=skill, field_name="skill", user=user, result=result
    )

    if action == "create":
        result.created += 1
    else:
        result.updated += 1


def _deserialise_skill_points(raw_points) -> list[dict[str, str]]:
    if not isinstance(raw_points, list):
        return []

    points_payload: list[dict[str, str]] = []
    for raw_point in raw_points:
        if isinstance(raw_point, dict):
            point_value = raw_point.get("value")
            point_type = str(raw_point.get("type") or "point").strip()
            if point_type != "point":
                continue
        else:
            point_value = raw_point

        point_text = str(point_value or "").strip()
        if not point_text:
            continue
        points_payload.append(
            {
                "type": "point",
                "value": point_text,
            }
        )
    return points_payload


def _import_roles(raw_roles: list, *, user, result: PageImportResult):
    for node in raw_roles:
        _import_role_node(node=node, user=user, result=result)


def _import_role_node(*, node, user, result: PageImportResult):
    result.processed += 1
    if not isinstance(node, dict):
        result.skipped += 1
        result.errors.append("Skipped role entry because it is not an object.")
        return

    slug = _normalised_slug(node.get("slug") or node.get("title"))
    if not slug:
        result.skipped += 1
        result.errors.append("Skipped role entry because the slug is missing.")
        return

    existing_role = GovukRole.objects.filter(slug=slug).first()
    if existing_role is None:
        action = "create"
        role = GovukRole(slug=slug)
    else:
        action = "update"
        role = existing_role

    required = "add" if action == "create" else "change"
    if not user_may(user, GovukRole, required):
        result.skipped += 1
        result.errors.append(_permission_error(GovukRole, required, f"role '{slug}'"))
        return

    default_title = slug.replace("-", " ").strip().title() or slug
    role.slug = slug
    role.title = str(node.get("title") or role.title or default_title).strip() or default_title
    role.family = str(node.get("family") or "").strip()
    role.body = str(node.get("body") or "")
    role.levels = _deserialise_role_levels(
        node.get("levels"),
        role_slug=slug,
        result=result,
    )
    role.is_senior_civil_service = _coerce_bool(node.get("is_senior_civil_service"))
    role.scs_grades = _deserialise_choice_stream(
        node.get("scs_grades"),
        block_type="grade",
        valid_keys=SCS_GRADE_KEYS,
    )
    role.scs_skills = _deserialise_scs_skills(
        node.get("scs_skills"),
        role_slug=slug,
        result=result,
    )

    try:
        role.full_clean()
        role.save()
    except (ValidationError, ValueError) as exc:
        result.skipped += 1
        result.errors.append(f"Skipped role '{slug}': {exc}")
        return

    _import_changelog(
        node.get("changelog"), subject=role, field_name="role", user=user, result=result
    )

    if action == "create":
        result.created += 1
    else:
        result.updated += 1


def _deserialise_choice_stream(raw_values, *, block_type: str, valid_keys: set) -> list[dict]:
    """Stream blocks for choice keys, dropping anything not in the vocabulary."""
    if not isinstance(raw_values, list):
        return []

    blocks_payload: list[dict] = []
    seen: set[str] = set()
    for raw_value in raw_values:
        key = str(
            raw_value.get("value") if isinstance(raw_value, dict) else raw_value or ""
        ).strip()
        if key in valid_keys and key not in seen:
            seen.add(key)
            blocks_payload.append({"type": block_type, "value": key})
    return blocks_payload


def _deserialise_scs_skills(raw_skills, *, role_slug: str, result: PageImportResult) -> list[dict]:
    if not isinstance(raw_skills, list):
        return []

    blocks_payload: list[dict] = []
    for raw_skill in raw_skills:
        raw_value = raw_skill.get("value") if isinstance(raw_skill, dict) else raw_skill
        skill_slug = _normalised_slug(raw_value)
        skill = (
            GovukSkill.objects.filter(slug=skill_slug).only("pk").first()
            if skill_slug
            else None
        )
        if skill is None:
            result.errors.append(
                f"Role '{role_slug}' skipped Senior Civil Service skill "
                f"for missing skill '{raw_value}'."
            )
            continue
        blocks_payload.append({"type": "skill", "value": skill.pk})
    return blocks_payload


def _import_changelog(
    raw_entries, *, subject, field_name: str, user, result: PageImportResult
):
    """Replace the changelog entries attached to one role or skill."""
    _replace_changelog(
        raw_entries,
        owner={field_name: subject},
        label=str(subject),
        user=user,
        result=result,
    )


def _import_site_wide_changelog(raw_entries, *, user, result: PageImportResult):
    """Replace the framework-wide entries, which belong to no role or skill."""
    _replace_changelog(
        raw_entries,
        owner={"role": None, "skill": None},
        label="the framework",
        user=user,
        result=result,
    )


def _replace_changelog(
    raw_entries, *, owner: dict, label: str, user, result: PageImportResult
):
    """Swap a set of changelog entries for the imported ones.

    Entries carry no stable identifier of their own, so the imported set
    replaces whatever is there rather than trying to match entry by entry.

    A changelog is its own snippet rather than a panel on the role, so the
    permission to rewrite one is its own too: a file naming entries is checked
    the way the changelog menu is. Reported and skipped rather than refused,
    so the role or skill it came with still arrives.
    """
    if not isinstance(raw_entries, list):
        return

    # A replace is a delete and then an add, so both are asked for: holding one
    # without the other would empty the changelog and then fail to refill it.
    for required in ("delete", "add"):
        if not user_may(user, GovukChangelogEntry, required):
            result.errors.append(
                _permission_error(
                    GovukChangelogEntry, required, f"the changelog for {label}"
                )
            )
            return

    filters = {}
    for name, value in owner.items():
        if value is None:
            filters[f"{name}__isnull"] = True
        else:
            filters[name] = value
    GovukChangelogEntry.objects.filter(**filters).delete()

    for raw_entry in raw_entries:
        if not isinstance(raw_entry, dict):
            continue

        entry_date = parse_date(str(raw_entry.get("date") or "").strip()[:10])
        note = str(raw_entry.get("note") or "").strip()
        if entry_date is None or not note:
            result.errors.append(
                f"Skipped a changelog entry for {label} with no date or note."
            )
            continue

        entry = GovukChangelogEntry(
            date=entry_date,
            change_type=str(raw_entry.get("change_type") or "").strip(),
            note=note,
            live=_coerce_bool(raw_entry.get("live", True)),
            **owner,
        )
        try:
            entry.full_clean()
            entry.save()
        except (ValidationError, ValueError) as exc:
            result.errors.append(f"Skipped a changelog entry for {label}: {exc}")


def _deserialise_role_levels(raw_levels, *, role_slug: str, result: PageImportResult) -> list[dict]:
    if not isinstance(raw_levels, list):
        return []

    levels_payload: list[dict] = []
    for raw_level in raw_levels:
        if not isinstance(raw_level, dict):
            continue

        raw_skill_requirements = raw_level.get("skills")
        if not isinstance(raw_skill_requirements, list):
            raw_skill_requirements = []

        skill_requirements_payload: list[dict[str, object]] = []
        for raw_skill_requirement in raw_skill_requirements:
            if not isinstance(raw_skill_requirement, dict):
                continue

            required_level = _normalised_skill_level(raw_skill_requirement.get("level"))
            if not required_level:
                continue

            raw_skill_value = raw_skill_requirement.get("skill")
            skill_slug = _normalised_slug(
                raw_skill_requirement.get("skill_slug")
                or ("" if str(raw_skill_value or "").isdigit() else raw_skill_value)
            )
            skill_id = raw_skill_requirement.get("skill_id") or raw_skill_value
            skill = None
            if skill_slug:
                skill = GovukSkill.objects.filter(slug=skill_slug).only("pk").first()
            if skill is None and str(skill_id or "").isdigit():
                skill = GovukSkill.objects.filter(pk=int(skill_id)).only("pk", "slug").first()
                if skill is not None and not skill_slug:
                    skill_slug = skill.slug

            if skill is None:
                result.errors.append(
                    (
                        f"Role '{role_slug}' skipped skill requirement "
                        f"for missing skill '{skill_slug or skill_id}'."
                    )
                )
                continue

            skill_requirements_payload.append(
                {
                    "skill": skill.pk,
                    "level": required_level,
                }
            )

        grade_keys = [
            key
            for key in (
                str(raw_grade or "").strip() for raw_grade in (raw_level.get("grades") or [])
            )
            if key in JOB_GRADE_KEYS
        ]

        levels_payload.append(
            {
                "type": "level",
                "value": {
                    "title": str(raw_level.get("title") or "").strip(),
                    "description": raw_level.get("description") or "",
                    "grades": grade_keys,
                    "skills": skill_requirements_payload,
                },
            }
        )
    return levels_payload


def _import_page_node(
    *,
    node,
    parent_page: Page,
    site_root: Page,
    user,
    result: PageImportResult,
    tag_lookup: dict[str, dict[str, int]] | None = None,
):
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
            _apply_page_fields(
                page,
                node.get("fields"),
                tag_lookup=tag_lookup,
                result=result,
            )
            page.full_clean()
            page.save()
            _apply_tags(page, node.get("tags"), tag_lookup=tag_lookup)
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
                        tag_lookup=tag_lookup,
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
    if (
        existing_page is None
        and slug == site_root.slug
        and site_root.specific_class is model_class
    ):
        # A payload that starts at the home page is the whole site. Update the
        # home page in place; the search above cannot find it because it looks
        # below the root, so without this every import nests another copy of
        # the site one level deeper.
        existing_page = site_root

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
    if specific_existing.pk != site_root.pk and current_parent.pk != parent_page.pk:
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


def _apply_page_fields(
    page: Page,
    fields_data,
    *,
    tag_lookup: dict[str, dict[str, int]] | None = None,
    result: PageImportResult | None = None,
):
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

        if (
            isinstance(model_field, StreamField)
            and page._meta.label == "govuk.SectionPage"
            and field_name == "rows"
        ):
            raw_value = _normalise_section_row_card_tags(raw_value, tag_lookup=tag_lookup)

        snippet_stream = PAGE_SNIPPET_SLUG_STREAM_FIELDS.get(
            (page._meta.label, field_name)
        )
        if snippet_stream is not None:
            block_type, snippet_model = snippet_stream
            raw_value = _deserialise_snippet_chooser_stream(
                raw_value,
                block_type=block_type,
                snippet_model=snippet_model,
                page_slug=page.slug,
                result=result,
            )

        python_value = _deserialise_model_field_value(model_field, raw_value)
        setattr(page, field_name, python_value)


def _deserialise_snippet_chooser_stream(
    raw_blocks,
    *,
    block_type: str,
    snippet_model,
    page_slug: str,
    result: PageImportResult | None = None,
) -> list:
    """Resolve the slugs in a page's chooser stream to local primary keys.

    A reference that matches no slug is dropped and reported. It is not worth
    falling back to a primary key carried in an older payload: keys are not
    the same between databases, so that silently points the page at whichever
    snippet happens to hold the number.
    """
    if not isinstance(raw_blocks, list):
        return raw_blocks

    chosen_slugs = [
        _normalised_slug(block.get("value"))
        for block in raw_blocks
        if isinstance(block, dict) and block.get("type") == block_type
    ]
    keys_by_slug = dict(
        snippet_model.objects.filter(
            slug__in=[slug for slug in chosen_slugs if slug]
        ).values_list("slug", "pk")
    )

    blocks_payload: list = []
    for block in raw_blocks:
        if not isinstance(block, dict) or block.get("type") != block_type:
            blocks_payload.append(block)
            continue

        raw_reference = block.get("value")
        chosen_key = keys_by_slug.get(_normalised_slug(raw_reference))
        if chosen_key is None:
            if result is not None:
                result.errors.append(
                    f"Page '{page_slug}' dropped {block_type} '{raw_reference}' "
                    f"because no {snippet_model._meta.verbose_name} has that slug."
                )
            continue
        blocks_payload.append({**block, "value": chosen_key})
    return blocks_payload


def _apply_tags(page: Page, tags_data, *, tag_lookup: dict[str, dict[str, int]] | None = None):
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

        tag_id = _resolve_tag_id_from_slug_and_name(
            raw_slug=raw_slug,
            raw_name=raw_name,
            tag_lookup=tag_lookup,
            create_if_missing=True,
        )
        if tag_id is not None:
            tag_ids.append(tag_id)

    tag_objects = list(GovukTag.objects.filter(id__in=tag_ids))
    page.tags.set(tag_objects)


def _normalise_section_row_card_tags(raw_rows, *, tag_lookup: dict[str, dict[str, int]] | None):
    if not isinstance(raw_rows, list):
        return raw_rows

    normalised_rows: list = []
    for raw_row in raw_rows:
        if not isinstance(raw_row, dict):
            normalised_rows.append(raw_row)
            continue

        row_value = raw_row.get("value")
        if raw_row.get("type") != "row" or not isinstance(row_value, dict):
            normalised_rows.append(raw_row)
            continue

        raw_cards = row_value.get("cards")
        if not isinstance(raw_cards, list):
            normalised_rows.append(raw_row)
            continue

        normalised_cards: list = []
        for raw_card in raw_cards:
            if not isinstance(raw_card, dict):
                normalised_cards.append(raw_card)
                continue

            card_value = raw_card.get("value")
            if raw_card.get("type") != "item" or not isinstance(card_value, dict):
                normalised_cards.append(raw_card)
                continue

            raw_card_tags = card_value.get("tags")
            if not isinstance(raw_card_tags, list):
                normalised_cards.append(raw_card)
                continue

            normalised_card_tags: list[dict[str, object]] = []
            for raw_card_tag in raw_card_tags:
                normalised_card_tag = _normalise_section_card_tag_block(
                    raw_card_tag,
                    tag_lookup=tag_lookup,
                )
                if normalised_card_tag is not None:
                    normalised_card_tags.append(normalised_card_tag)

            updated_card_value = dict(card_value)
            updated_card_value["tags"] = normalised_card_tags

            updated_card = dict(raw_card)
            updated_card["value"] = updated_card_value
            normalised_cards.append(updated_card)

        updated_row_value = dict(row_value)
        updated_row_value["cards"] = normalised_cards

        updated_row = dict(raw_row)
        updated_row["value"] = updated_row_value
        normalised_rows.append(updated_row)

    return normalised_rows


def _normalise_section_card_tag_block(raw_card_tag, *, tag_lookup: dict[str, dict[str, int]] | None):
    tag_block_id = None
    tag_block_type = "item"
    raw_tag_value = raw_card_tag

    if isinstance(raw_card_tag, dict):
        tag_block_id = raw_card_tag.get("id")
        tag_block_type = str(raw_card_tag.get("type") or "item").strip() or "item"
        raw_tag_value = raw_card_tag.get("value")

    tag_id = _resolve_tag_id(raw_tag_value, tag_lookup=tag_lookup)
    if tag_id is None:
        return None

    payload: dict[str, object] = {
        "type": tag_block_type,
        "value": tag_id,
    }
    if tag_block_id not in {None, ""}:
        payload["id"] = str(tag_block_id)
    return payload


def _resolve_tag_id(raw_tag_value, *, tag_lookup: dict[str, dict[str, int]] | None) -> int | None:
    if isinstance(raw_tag_value, GovukTag):
        return raw_tag_value.id

    if isinstance(raw_tag_value, int):
        if GovukTag.objects.filter(pk=raw_tag_value).exists():
            return raw_tag_value
        return None

    if isinstance(raw_tag_value, str):
        raw_text = raw_tag_value.strip()
        if not raw_text:
            return None
        if raw_text.isdigit():
            numeric_id = int(raw_text)
            if GovukTag.objects.filter(pk=numeric_id).exists():
                return numeric_id
        return _resolve_tag_id_from_slug_and_name(
            raw_slug=raw_text,
            raw_name=raw_text,
            tag_lookup=tag_lookup,
            create_if_missing=True,
        )

    if isinstance(raw_tag_value, dict):
        raw_tag_id = raw_tag_value.get("id") or raw_tag_value.get("pk") or raw_tag_value.get(
            "value_id"
        )
        if str(raw_tag_id or "").isdigit():
            numeric_id = int(raw_tag_id)
            if GovukTag.objects.filter(pk=numeric_id).exists():
                return numeric_id

        raw_slug = raw_tag_value.get("slug") or raw_tag_value.get("key")
        raw_name = raw_tag_value.get("name") or raw_tag_value.get("label")
        if not raw_name and isinstance(raw_tag_value.get("value"), str):
            raw_name = raw_tag_value.get("value")

        return _resolve_tag_id_from_slug_and_name(
            raw_slug=raw_slug,
            raw_name=raw_name,
            tag_lookup=tag_lookup,
            create_if_missing=True,
        )

    return None


def _resolve_tag_id_from_slug_and_name(
    *,
    raw_slug,
    raw_name,
    tag_lookup: dict[str, dict[str, int]] | None,
    create_if_missing: bool,
) -> int | None:
    tag_slug = _normalised_slug(raw_slug or raw_name)
    if not tag_slug:
        return None

    tag_name = (str(raw_name or raw_slug or tag_slug).strip() or tag_slug).strip()
    tag_name_key = tag_name.lower()

    by_slug = tag_lookup.setdefault("by_slug", {}) if isinstance(tag_lookup, dict) else {}
    by_name = tag_lookup.setdefault("by_name", {}) if isinstance(tag_lookup, dict) else {}

    existing_id = by_slug.get(tag_slug)
    if existing_id is not None:
        return existing_id
    if tag_name_key:
        existing_id = by_name.get(tag_name_key)
        if existing_id is not None:
            return existing_id

    tag_obj = GovukTag.objects.filter(slug=tag_slug).only("id", "slug", "name").first()
    if tag_obj is None and tag_name_key:
        tag_obj = (
            GovukTag.objects.filter(name__iexact=tag_name)
            .order_by("id")
            .only("id", "slug", "name")
            .first()
        )
    if tag_obj is None and create_if_missing:
        tag_obj, _ = GovukTag.objects.get_or_create(
            slug=tag_slug,
            defaults={"name": tag_name},
        )

    if tag_obj is None:
        return None
    if not tag_obj.name and tag_name:
        tag_obj.name = tag_name
        tag_obj.save(update_fields=["name"])

    _update_tag_lookup(
        tag_lookup,
        tag_id=tag_obj.id,
        tag_slug=tag_obj.slug,
        tag_name=tag_obj.name or tag_name,
    )
    return tag_obj.id


def _update_tag_lookup(tag_lookup, *, tag_id: int, tag_slug: str, tag_name: str):
    if not isinstance(tag_lookup, dict):
        return

    by_slug = tag_lookup.setdefault("by_slug", {})
    by_name = tag_lookup.setdefault("by_name", {})
    if tag_slug:
        by_slug.setdefault(tag_slug, tag_id)
    name_key = str(tag_name or "").strip().lower()
    if name_key:
        by_name.setdefault(name_key, tag_id)


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


def _normalised_skill_level(value) -> str:
    level = str(value or "").strip().lower()
    if level in SKILL_LEVEL_CHOICES:
        return level
    return ""


def _skill_slug_from_value(raw_skill_value) -> str:
    if isinstance(raw_skill_value, GovukSkill):
        return _normalised_slug(raw_skill_value.slug)

    if isinstance(raw_skill_value, dict):
        raw_skill_value = raw_skill_value.get("slug") or raw_skill_value.get("skill_slug")

    if str(raw_skill_value or "").isdigit():
        matching_skill = GovukSkill.objects.filter(pk=int(raw_skill_value)).only("slug").first()
        if matching_skill is not None:
            return _normalised_slug(matching_skill.slug)

    return _normalised_slug(raw_skill_value)


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
