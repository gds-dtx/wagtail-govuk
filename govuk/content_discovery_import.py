from __future__ import annotations

import csv

from dataclasses import dataclass
from typing import TextIO

from django.db import transaction
from django.db.models import Max
from wagtail.models import Site

from govuk.models import ContentDiscoverySettings, ContentDiscoverySource, GovukTag

TRUTHY_VALUES = {"1", "true", "t", "yes", "y", "on"}
FALSY_VALUES = {"0", "false", "f", "no", "n", "off", ""}


class ContentDiscoverySourceImportError(ValueError):
    """Raised when a content discovery source CSV cannot be imported."""


@dataclass(slots=True)
class ContentDiscoverySourceImportResult:
    processed: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped_empty: int = 0


def import_content_discovery_sources_from_csv(
    csv_file: TextIO,
    *,
    delimiter: str = ",",
    default_site_id: int | None = None,
    allowed_site_ids: set[int] | None = None,
) -> ContentDiscoverySourceImportResult:
    if len(delimiter) != 1:
        raise ContentDiscoverySourceImportError("Delimiter must be a single character.")
    if default_site_id is not None and default_site_id <= 0:
        raise ContentDiscoverySourceImportError("--site-id must be a positive integer.")

    reader = csv.DictReader(csv_file, delimiter=delimiter)
    if not reader.fieldnames:
        raise ContentDiscoverySourceImportError("CSV file is missing a header row.")

    header_names = {
        (header or "").strip().lower()
        for header in reader.fieldnames
        if (header or "").strip()
    }
    if "url" not in header_names:
        raise ContentDiscoverySourceImportError(
            "CSV header must include a 'url' column."
        )
    if "site_id" not in header_names and default_site_id is None:
        raise ContentDiscoverySourceImportError(
            "Provide a 'site_id' column or pass --site-id for all rows."
        )

    has_name = "name" in header_names
    has_tls = "disable_tls_verification" in header_names
    has_signed_jwt = "send_signed_bearer_jwt" in header_names
    has_default_tags = "default_tags" in header_names
    settings_cache: dict[int, ContentDiscoverySettings] = {}
    result = ContentDiscoverySourceImportResult()

    with transaction.atomic():
        for row_index, raw_row in enumerate(reader, start=2):
            row = _normalize_row(raw_row)
            if not any(value for value in row.values()):
                result.skipped_empty += 1
                continue

            result.processed += 1
            site_id = _resolve_site_id(
                row=row,
                row_index=row_index,
                default_site_id=default_site_id,
                allowed_site_ids=allowed_site_ids,
            )
            source_settings = _get_settings_for_site(
                site_id=site_id,
                row_index=row_index,
                cache=settings_cache,
            )

            url = row.get("url", "")
            if not url:
                raise ContentDiscoverySourceImportError(
                    f"Row {row_index}: 'url' cannot be blank."
                )

            source = ContentDiscoverySource.objects.filter(
                settings=source_settings,
                url=url,
            ).first()

            name = row.get("name", "") if has_name else None
            disable_tls_verification = (
                _parse_bool(
                    row.get("disable_tls_verification", ""),
                    row_index=row_index,
                    field_name="disable_tls_verification",
                )
                if has_tls
                else None
            )
            send_signed_bearer_jwt = (
                _parse_bool(
                    row.get("send_signed_bearer_jwt", ""),
                    row_index=row_index,
                    field_name="send_signed_bearer_jwt",
                )
                if has_signed_jwt
                else None
            )
            default_tag_stream = (
                _parse_default_tag_stream(
                    row.get("default_tags", ""),
                    row_index=row_index,
                )
                if has_default_tags
                else None
            )

            if source is None:
                source = ContentDiscoverySource(
                    settings=source_settings,
                    sort_order=_next_sort_order(source_settings),
                    url=url,
                    name=name or "",
                    disable_tls_verification=(
                        disable_tls_verification
                        if disable_tls_verification is not None
                        else False
                    ),
                    send_signed_bearer_jwt=(
                        send_signed_bearer_jwt
                        if send_signed_bearer_jwt is not None
                        else False
                    ),
                    default_tags=default_tag_stream or [],
                )
                source.full_clean()
                source.save()
                result.created += 1
                continue

            fields_to_update: list[str] = []
            if has_name and source.name != name:
                source.name = name or ""
                fields_to_update.append("name")
            if (
                has_tls
                and disable_tls_verification is not None
                and source.disable_tls_verification != disable_tls_verification
            ):
                source.disable_tls_verification = disable_tls_verification
                fields_to_update.append("disable_tls_verification")
            if (
                has_signed_jwt
                and send_signed_bearer_jwt is not None
                and source.send_signed_bearer_jwt != send_signed_bearer_jwt
            ):
                source.send_signed_bearer_jwt = send_signed_bearer_jwt
                fields_to_update.append("send_signed_bearer_jwt")
            if (
                has_default_tags
                and default_tag_stream is not None
                and source.get_default_tag_ids()
                != _tag_ids_from_stream(default_tag_stream)
            ):
                source.default_tags = default_tag_stream
                fields_to_update.append("default_tags")

            if fields_to_update:
                source.full_clean(validate_unique=False)
                source.save(update_fields=fields_to_update)
                result.updated += 1
            else:
                result.unchanged += 1

    return result


def _normalize_row(raw_row: dict[str, str | None]) -> dict[str, str]:
    return {
        (key or "").strip().lower(): (value or "").strip()
        for key, value in raw_row.items()
        if key is not None
    }


def _resolve_site_id(
    *,
    row: dict[str, str],
    row_index: int,
    default_site_id: int | None,
    allowed_site_ids: set[int] | None,
) -> int:
    raw_site_id = row.get("site_id", "")
    if raw_site_id:
        if not raw_site_id.isdigit():
            raise ContentDiscoverySourceImportError(
                f"Row {row_index}: site_id must be a positive integer."
            )
        site_id = int(raw_site_id)
        if site_id <= 0:
            raise ContentDiscoverySourceImportError(
                f"Row {row_index}: site_id must be a positive integer."
            )
    elif default_site_id is not None:
        site_id = default_site_id
    else:
        raise ContentDiscoverySourceImportError(
            f"Row {row_index}: missing site_id and no --site-id fallback provided."
        )

    if allowed_site_ids is not None and site_id not in allowed_site_ids:
        raise ContentDiscoverySourceImportError(
            f"Row {row_index}: site_id {site_id} is not allowed for this import."
        )
    return site_id


def _parse_bool(raw_value: str, *, row_index: int, field_name: str) -> bool:
    normalised = raw_value.strip().lower()
    if normalised in TRUTHY_VALUES:
        return True
    if normalised in FALSY_VALUES:
        return False
    raise ContentDiscoverySourceImportError(
        f"Row {row_index}: '{field_name}' must be one of "
        f"{sorted(TRUTHY_VALUES | FALSY_VALUES)}."
    )


def _tag_ids_from_stream(stream_data: list[dict[str, int]]) -> list[int]:
    return [int(block["value"]) for block in stream_data]


def _parse_default_tag_stream(
    raw_value: str,
    *,
    row_index: int,
) -> list[dict[str, int]]:
    tag_keys = [key.strip().lower() for key in raw_value.split("|") if key.strip()]
    if not tag_keys:
        return []

    seen: set[str] = set()
    ordered_keys: list[str] = []
    for key in tag_keys:
        if key in seen:
            continue
        seen.add(key)
        ordered_keys.append(key)

    tags_by_key = {
        tag.slug: tag for tag in GovukTag.objects.filter(slug__in=ordered_keys)
    }
    missing = [key for key in ordered_keys if key not in tags_by_key]
    if missing:
        raise ContentDiscoverySourceImportError(
            f"Row {row_index}: unknown tag key(s): {', '.join(missing)}."
        )

    return [{"type": "tag", "value": tags_by_key[key].pk} for key in ordered_keys]


def _get_settings_for_site(
    *,
    site_id: int,
    row_index: int,
    cache: dict[int, ContentDiscoverySettings],
) -> ContentDiscoverySettings:
    if site_id in cache:
        return cache[site_id]

    site = Site.objects.filter(pk=site_id).first()
    if site is None:
        raise ContentDiscoverySourceImportError(
            f"Row {row_index}: site_id {site_id} does not exist."
        )

    cache[site_id] = ContentDiscoverySettings.for_site(site)
    return cache[site_id]


def _next_sort_order(settings: ContentDiscoverySettings) -> int:
    current_max = settings.sources.aggregate(max_sort_order=Max("sort_order"))[
        "max_sort_order"
    ]
    return (current_max if current_max is not None else -1) + 1
