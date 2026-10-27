# Moves each content-style page's rich text body into a Text block at the head
# of its block stream, so that 0088 can drop the rich text field and rename the
# stream to ``body``. Two migrations rather than one because Postgres will not
# alter a table in the same transaction that changed its rows (see 0071/0072).
#
# Three things carry the old shape and all three are converted here:
#
# - the live rows of the three page types that had both fields;
# - their revisions, whose content is what the admin edits and what a draft
#   publishes. Left alone, a page's latest draft would open with an empty body
#   and publishing it would blank the page;
# - the reference index rows that point into either field, so the admin's
#   "usage" listings still find a page linked from a body.
#
# The page tables are read and written with raw SQL, as 0071 did: the stream
# column's historical field would rebuild every block from definitions that
# have nothing to do with this move, and only the raw JSON is needed.
#
# FrameworkSkillsPage has a rich text ``body`` of its own that stays one, so
# revisions are picked by content type, never by the "body" key.

import json
import uuid

from django.db import migrations

# The concrete subclasses of BaseContentPage, as at this migration.
MODEL_NAMES = ["contentpage", "frameworkmainpage", "frameworkcontentpage"]

# Frozen: the namespace ReferenceIndex._get_content_path_hash hashes a content
# path under, as at Wagtail 8.0. Hardcoded so a later Wagtail cannot change what
# this migration writes.
CONTENT_PATH_HASH_NAMESPACE = uuid.UUID("bdc70d8b-e7a2-4c2a-bf43-2a3e3fcbbe86")

# The paths Wagtail indexes a rich text field and a stream under: a rich text
# field's references sit at "<field>." and a stream's at "<field>.<block>...".
OLD_BODY_PATH = "body."
OLD_STREAM_PREFIX = "body_blocks."
NEW_STREAM_PREFIX = "body."


def _blocks(raw) -> list:
    """A stream's raw blocks from a column value or a revision's JSON string.

    SQLite hands the column back as text and Postgres as parsed JSON.
    """
    if raw in (None, ""):
        return []
    if isinstance(raw, str):
        return json.loads(raw)
    return list(raw)


def _text_block(html: str) -> dict:
    return {"type": "text", "value": html, "id": str(uuid.uuid4())}


def _is_blank(html) -> bool:
    return not (html or "").strip()


def _is_stream(raw) -> bool:
    """Whether a revision's body is already a stream rather than rich text."""
    if not isinstance(raw, str):
        return isinstance(raw, list)
    try:
        return isinstance(json.loads(raw), list)
    except ValueError:
        return False


def _content_type_ids(apps, db_alias) -> list[int]:
    ContentType = apps.get_model("contenttypes", "ContentType")
    return list(
        ContentType.objects.using(db_alias)
        .filter(app_label="govuk", model__in=MODEL_NAMES)
        .values_list("id", flat=True)
    )


def _migrate_live_rows(apps, schema_editor) -> dict[int, tuple[str, list]]:
    """Fold each live row's body into its stream.

    Returns, for every page whose body held anything, the new Text block's id
    and the stream as written, for the reference index and the check to use.
    """
    conn = schema_editor.connection
    qn = conn.ops.quote_name
    converted: dict[int, tuple[str, list]] = {}

    for model_name in MODEL_NAMES:
        table = apps.get_model("govuk", model_name)._meta.db_table
        with conn.cursor() as cursor:
            cursor.execute(
                f"SELECT {qn('page_ptr_id')}, {qn('body')}, {qn('body_blocks')} "
                f"FROM {qn(table)}"
            )
            rows = cursor.fetchall()

        for page_id, body, raw_blocks in rows:
            if _is_blank(body):
                continue
            text_block = _text_block(body)
            stream = [text_block, *_blocks(raw_blocks)]
            with conn.cursor() as cursor:
                cursor.execute(
                    f"UPDATE {qn(table)} SET {qn('body_blocks')} = %s "
                    f"WHERE {qn('page_ptr_id')} = %s",
                    [json.dumps(stream), page_id],
                )
            converted[page_id] = (text_block["id"], stream)
    return converted


def _migrate_revisions(apps, db_alias, content_type_ids) -> None:
    Revision = apps.get_model("wagtailcore", "Revision")
    to_update = []
    revisions = Revision.objects.using(db_alias).filter(
        content_type_id__in=content_type_ids
    )
    for revision in revisions.iterator(chunk_size=200):
        content = revision.content
        if not isinstance(content, dict):
            continue
        if "body_blocks" not in content and _is_stream(content.get("body")):
            continue

        stream = _blocks(content.pop("body_blocks", None))
        body = content.get("body")
        if isinstance(body, str) and not _is_blank(body):
            stream.insert(0, _text_block(body))
        # A revision keeps a stream as its JSON text, the way it kept
        # body_blocks.
        content["body"] = json.dumps(stream)
        revision.content = content
        to_update.append(revision)

    for start in range(0, len(to_update), 200):
        Revision.objects.using(db_alias).bulk_update(
            to_update[start : start + 200], ["content"]
        )


def _migrate_reference_index(apps, db_alias, content_type_ids, converted) -> None:
    try:
        ReferenceIndex = apps.get_model("wagtailcore", "ReferenceIndex")
    except LookupError:
        return

    rows = ReferenceIndex.objects.using(db_alias).filter(
        content_type_id__in=content_type_ids
    )
    to_update = []
    stale_ids = []
    for row in rows.filter(model_path=OLD_BODY_PATH):
        # The rich text body becomes the Text block it was moved into. A row
        # for a page whose body is now blank was already stale.
        entry = converted.get(int(row.object_id))
        if entry is None:
            stale_ids.append(row.pk)
            continue
        row.model_path = f"{NEW_STREAM_PREFIX}text"
        row.content_path = f"{NEW_STREAM_PREFIX}{entry[0]}"
        to_update.append(row)
    for row in rows.filter(model_path__startswith=OLD_STREAM_PREFIX):
        row.model_path = NEW_STREAM_PREFIX + row.model_path[len(OLD_STREAM_PREFIX) :]
        row.content_path = (
            NEW_STREAM_PREFIX + row.content_path[len(OLD_STREAM_PREFIX) :]
        )
        to_update.append(row)

    for row in to_update:
        row.content_path_hash = uuid.uuid5(
            CONTENT_PATH_HASH_NAMESPACE, row.content_path
        )
    if stale_ids:
        ReferenceIndex.objects.using(db_alias).filter(pk__in=stale_ids).delete()
    ReferenceIndex.objects.using(db_alias).bulk_update(
        to_update, ["model_path", "content_path", "content_path_hash"], batch_size=200
    )


def _assert_converted(apps, schema_editor, content_type_ids, converted) -> None:
    """Fail loudly, rolling the migration back, if anything kept the old shape."""
    conn = schema_editor.connection
    qn = conn.ops.quote_name
    db_alias = conn.alias
    problems = []

    for model_name in MODEL_NAMES:
        table = apps.get_model("govuk", model_name)._meta.db_table
        with conn.cursor() as cursor:
            cursor.execute(
                f"SELECT {qn('page_ptr_id')}, {qn('body')}, {qn('body_blocks')} "
                f"FROM {qn(table)}"
            )
            rows = cursor.fetchall()
        for page_id, body, raw_blocks in rows:
            if page_id not in converted:
                continue
            stream = _blocks(raw_blocks)
            if stream != converted[page_id][1] or stream[0]["value"] != body:
                problems.append(f"{model_name} {page_id}: body not at head of stream")

    Revision = apps.get_model("wagtailcore", "Revision")
    for revision in Revision.objects.using(db_alias).filter(
        content_type_id__in=content_type_ids
    ).iterator(chunk_size=200):
        content = revision.content
        if isinstance(content, dict) and (
            "body_blocks" in content or not _is_stream(content.get("body"))
        ):
            problems.append(f"revision {revision.pk}: body is not a stream")

    try:
        ReferenceIndex = apps.get_model("wagtailcore", "ReferenceIndex")
    except LookupError:
        ReferenceIndex = None
    if ReferenceIndex is not None:
        leftover = ReferenceIndex.objects.using(db_alias).filter(
            content_type_id__in=content_type_ids
        )
        count = (
            leftover.filter(model_path=OLD_BODY_PATH).count()
            + leftover.filter(model_path__startswith=OLD_STREAM_PREFIX).count()
        )
        if count:
            problems.append(f"{count} reference index rows still use the old paths")

    if problems:
        raise RuntimeError(
            "Migration 0087 left content in the old body shape: "
            + "; ".join(problems[:20])
        )


def forwards(apps, schema_editor):
    db_alias = schema_editor.connection.alias
    content_type_ids = _content_type_ids(apps, db_alias)
    converted = _migrate_live_rows(apps, schema_editor)
    _migrate_revisions(apps, db_alias, content_type_ids)
    _migrate_reference_index(apps, db_alias, content_type_ids, converted)
    _assert_converted(apps, schema_editor, content_type_ids, converted)


class Migration(migrations.Migration):
    dependencies = [
        ("govuk", "0086_body_blocks_cards_and_banners"),
        ("wagtailcore", "0070_rename_pagerevision_revision"),
    ]

    # No way back: once the body is a block it cannot be told apart from a
    # Text block an editor added. Unapplying 0088 brings back an empty rich
    # text field and leaves every block, the moved body included, in place.
    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
