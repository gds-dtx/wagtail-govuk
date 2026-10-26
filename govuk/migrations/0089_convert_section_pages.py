# Turns every SectionPage into a ContentPage, so that 0090 can delete the
# SectionPage model. A section page was rows of cards followed by free text; a
# content page body is a stream that holds both, so each page becomes a content
# page whose body is its rows as card blocks and then its free text as a Text
# block. The page keeps its id, its place in the tree and its URL.
#
# Wagtail cannot change a page's type, so this is done the way 0071 changed the
# framework's: the page's row moves from one child table to the other by raw
# SQL, and the content type is repointed. Everything else that records the
# page's type, or the shape of its content, is converted with it:
#
# - its page tags, which live in a through table per page type;
# - its revisions. Their content is what the admin edits and what a draft
#   publishes, so it is rebuilt into the content page's shape. A draft's tag
#   rows carry the primary keys of the old through table, and publishing it
#   saves rows by those keys, which could overwrite another page's tag; they
#   are remapped to the new rows;
# - the page log, search index, workflow state, editing sessions and uploads,
#   which name the page by type and id;
# - the reference index rows that point into the rows, the free text or the
#   tags, which are rewritten to the paths the same content has now.
#
# Two migrations rather than one because Postgres will not alter or drop a
# table in the same transaction that deleted rows referencing it (see 0071/0072).

import json
import uuid

from django.db import migrations

# Frozen: the namespace ReferenceIndex._get_content_path_hash hashes a content
# path under, as at Wagtail 8.0 (as in 0087).
CONTENT_PATH_HASH_NAMESPACE = uuid.UUID("bdc70d8b-e7a2-4c2a-bf43-2a3e3fcbbe86")

# Tables outside this app that name a page by content type and id, and the
# column that holds the type. A table this instance does not have is skipped.
CONTENT_TYPE_COLUMNS = [
    ("wagtailcore_pagelogentry", "content_type_id"),
    ("wagtailcore_workflowstate", "content_type_id"),
    ("wagtailsearch_indexentry", "content_type_id"),
    ("wagtailadmin_editingsession", "content_type_id"),
    ("wagtailadmin_formstate", "content_type_id"),
    ("wagtailcore_uploadedfile", "for_content_type_id"),
]


def _blocks(raw) -> list:
    """A stream's raw blocks from a column value or a revision's JSON string.

    SQLite hands the column back as text and Postgres as parsed JSON.
    """
    if raw in (None, ""):
        return []
    if isinstance(raw, str):
        return json.loads(raw)
    return list(raw)


def _is_blank(html) -> bool:
    return not (html or "").strip()


def _body(raw_rows, free_text) -> tuple[list, str | None]:
    """The content page body a section page's rows and free text make.

    Returns the stream and the id of the Text block the free text went into,
    or None where it was blank. Each row keeps its id and value and is retyped:
    the card row block was "row" in SectionPage.rows and is "cards" in a body.
    """
    stream = [
        {**block, "type": "cards"} if block.get("type") == "row" else block
        for block in _blocks(raw_rows)
    ]
    text_id = None
    if not _is_blank(free_text):
        text_id = str(uuid.uuid4())
        stream.append({"type": "text", "value": free_text, "id": text_id})
    return stream, text_id


def _content_type_id(apps, db_alias, model):
    ContentType = apps.get_model("contenttypes", "ContentType")
    content_type, _ = ContentType.objects.using(db_alias).get_or_create(
        app_label="govuk", model=model
    )
    return content_type.id


def _convert_pages(apps, schema_editor, contentpage_ct) -> dict[int, dict]:
    """Move every section page's row across, with its tags.

    Returns, for each converted page, its new body, the free text block's id
    and the map from its old tag rows' ids to the new ones.
    """
    conn = schema_editor.connection
    qn = conn.ops.quote_name
    db_alias = conn.alias
    SectionPage = apps.get_model("govuk", "SectionPage")
    ContentPage = apps.get_model("govuk", "ContentPage")
    SectionPageTag = apps.get_model("govuk", "SectionPageTag")
    ContentPageTag = apps.get_model("govuk", "ContentPageTag")

    source_table = SectionPage._meta.db_table
    target_table = ContentPage._meta.db_table
    source_columns = {field.column for field in SectionPage._meta.local_fields}
    target_columns = [field.column for field in ContentPage._meta.local_fields]
    # Every content page column has to come from somewhere: the body is built,
    # and the rest are columns the two types share. A column a section page
    # does not have would be left to the database to fill, so stop instead.
    copied = [column for column in target_columns if column != "body"]
    missing = [column for column in copied if column not in source_columns]
    if missing:
        raise RuntimeError(
            f"Migration 0089 has no section page value for content page columns {missing}."
        )

    with conn.cursor() as cursor:
        cursor.execute(
            f"SELECT {', '.join(qn(c) for c in copied)}, {qn('rows')}, {qn('free_text')} "
            f"FROM {qn(source_table)}"
        )
        rows = cursor.fetchall()

    converted: dict[int, dict] = {}
    page_ptr_index = copied.index("page_ptr_id")
    for row in rows:
        values = list(row[: len(copied)])
        raw_rows, free_text = row[len(copied) :]
        page_id = values[page_ptr_index]
        body, text_id = _body(raw_rows, free_text)

        with conn.cursor() as cursor:
            cursor.execute(
                f"INSERT INTO {qn(target_table)} "
                f"({', '.join(qn(c) for c in copied)}, {qn('body')}) "
                f"VALUES ({', '.join(['%s'] * (len(copied) + 1))})",
                [*values, json.dumps(body)],
            )

        # The content page's row now exists for the new tag rows to point at.
        tag_ids = {}
        for old in SectionPageTag.objects.using(db_alias).filter(
            content_object_id=page_id
        ).order_by("pk"):
            new = ContentPageTag.objects.using(db_alias).create(
                content_object_id=page_id, tag_id=old.tag_id
            )
            tag_ids[old.pk] = new.pk
        SectionPageTag.objects.using(db_alias).filter(content_object_id=page_id).delete()

        with conn.cursor() as cursor:
            cursor.execute(
                f"DELETE FROM {qn(source_table)} WHERE {qn('page_ptr_id')} = %s",
                [page_id],
            )
            cursor.execute(
                f"UPDATE {qn('wagtailcore_page')} SET {qn('content_type_id')} = %s "
                f"WHERE {qn('id')} = %s",
                [contentpage_ct, page_id],
            )
        converted[page_id] = {"body": body, "text_id": text_id, "tag_ids": tag_ids}
    return converted


def _convert_revisions(apps, db_alias, sectionpage_ct, contentpage_ct, converted):
    Revision = apps.get_model("wagtailcore", "Revision")
    to_update = []
    for revision in Revision.objects.using(db_alias).filter(
        content_type_id=sectionpage_ct
    ).iterator(chunk_size=200):
        content = revision.content
        if isinstance(content, dict):
            body, _ = _body(content.pop("rows", None), content.pop("free_text", None))
            content["body"] = json.dumps(body)
            content["content_type"] = contentpage_ct
            tag_ids = converted.get(int(revision.object_id), {}).get("tag_ids", {})
            for item in content.get("tagged_items") or []:
                if isinstance(item, dict) and item.get("pk") is not None:
                    # None where the row has gone since the draft was saved:
                    # publishing then adds the tag as a new row.
                    item["pk"] = tag_ids.get(item["pk"])
            revision.content = content
        revision.content_type_id = contentpage_ct
        to_update.append(revision)

    for start in range(0, len(to_update), 200):
        Revision.objects.using(db_alias).bulk_update(
            to_update[start : start + 200], ["content", "content_type_id"]
        )


def _repoint_content_types(schema_editor, sectionpage_ct, contentpage_ct):
    conn = schema_editor.connection
    qn = conn.ops.quote_name
    tables = set(conn.introspection.table_names())
    with conn.cursor() as cursor:
        for table, column in CONTENT_TYPE_COLUMNS:
            if table in tables:
                cursor.execute(
                    f"UPDATE {qn(table)} SET {qn(column)} = %s WHERE {qn(column)} = %s",
                    [contentpage_ct, sectionpage_ct],
                )


def _new_reference_paths(row, entry):
    """A reference index row's paths for the same content on a content page.

    None where the content it pointed at is not there any more.
    """
    model_path, content_path = row.model_path, row.content_path
    if model_path == "free_text.":
        if entry["text_id"] is None:
            return None
        return "body.text", f"body.{entry['text_id']}"
    if model_path.startswith("rows.row"):
        # The row keeps its id, so only the field and the block type change.
        return (
            "body.cards" + model_path[len("rows.row") :],
            "body." + content_path[len("rows.") :],
        )
    if model_path.startswith("tagged_items."):
        _, old_pk, rest = content_path.split(".", 2)
        new_pk = entry["tag_ids"].get(int(old_pk))
        if new_pk is None:
            return None
        return model_path, f"tagged_items.{new_pk}.{rest}"
    return model_path, content_path


def _convert_reference_index(apps, db_alias, sectionpage_ct, contentpage_ct, converted):
    try:
        ReferenceIndex = apps.get_model("wagtailcore", "ReferenceIndex")
    except LookupError:
        return

    to_update = []
    stale_ids = []
    for row in ReferenceIndex.objects.using(db_alias).filter(
        content_type_id=sectionpage_ct
    ):
        entry = converted.get(int(row.object_id))
        paths = _new_reference_paths(row, entry) if entry else None
        if paths is None:
            stale_ids.append(row.pk)
            continue
        row.model_path, row.content_path = paths
        row.content_path_hash = uuid.uuid5(CONTENT_PATH_HASH_NAMESPACE, row.content_path)
        row.content_type_id = contentpage_ct
        to_update.append(row)

    if stale_ids:
        ReferenceIndex.objects.using(db_alias).filter(pk__in=stale_ids).delete()
    ReferenceIndex.objects.using(db_alias).bulk_update(
        to_update,
        ["model_path", "content_path", "content_path_hash", "content_type_id"],
        batch_size=200,
    )


def _assert_converted(apps, schema_editor, sectionpage_ct, converted, tag_count):
    """Fail loudly, rolling the migration back, if anything is left behind."""
    conn = schema_editor.connection
    qn = conn.ops.quote_name
    db_alias = conn.alias
    problems = []

    ContentPage = apps.get_model("govuk", "ContentPage")
    with conn.cursor() as cursor:
        for table in (
            apps.get_model("govuk", "SectionPage")._meta.db_table,
            apps.get_model("govuk", "SectionPageTag")._meta.db_table,
        ):
            cursor.execute(f"SELECT COUNT(*) FROM {qn(table)}")
            if cursor.fetchone()[0]:
                problems.append(f"rows left in {table}")
        cursor.execute(
            f"SELECT {qn('page_ptr_id')}, {qn('body')} FROM {qn(ContentPage._meta.db_table)}"
        )
        bodies = {page_id: _blocks(body) for page_id, body in cursor.fetchall()}
        cursor.execute(
            f"SELECT COUNT(*) FROM {qn('wagtailcore_page')} WHERE {qn('content_type_id')} = %s",
            [sectionpage_ct],
        )
        if cursor.fetchone()[0]:
            problems.append("pages still typed as section pages")

    for page_id, entry in converted.items():
        if bodies.get(page_id) != entry["body"]:
            problems.append(f"page {page_id}: body not as built")

    moved = apps.get_model("govuk", "ContentPageTag").objects.using(db_alias).filter(
        content_object_id__in=list(converted)
    )
    if moved.count() != tag_count:
        problems.append(f"{tag_count} page tags before, {moved.count()} after")

    Revision = apps.get_model("wagtailcore", "Revision")
    if Revision.objects.using(db_alias).filter(content_type_id=sectionpage_ct).exists():
        problems.append("revisions still typed as section pages")

    try:
        ReferenceIndex = apps.get_model("wagtailcore", "ReferenceIndex")
    except LookupError:
        ReferenceIndex = None
    if ReferenceIndex is not None and ReferenceIndex.objects.using(db_alias).filter(
        content_type_id=sectionpage_ct
    ).exists():
        problems.append("reference index rows still typed as section pages")

    if problems:
        raise RuntimeError(
            "Migration 0089 left section page content behind: " + "; ".join(problems[:20])
        )


def forwards(apps, schema_editor):
    db_alias = schema_editor.connection.alias
    sectionpage_ct = _content_type_id(apps, db_alias, "sectionpage")
    contentpage_ct = _content_type_id(apps, db_alias, "contentpage")
    tag_count = apps.get_model("govuk", "SectionPageTag").objects.using(db_alias).count()

    converted = _convert_pages(apps, schema_editor, contentpage_ct)
    _convert_revisions(apps, db_alias, sectionpage_ct, contentpage_ct, converted)
    _repoint_content_types(schema_editor, sectionpage_ct, contentpage_ct)
    _convert_reference_index(apps, db_alias, sectionpage_ct, contentpage_ct, converted)
    _assert_converted(apps, schema_editor, sectionpage_ct, converted, tag_count)


class Migration(migrations.Migration):
    dependencies = [
        ("govuk", "0088_body_streamfield"),
        ("wagtailcore", "0070_rename_pagerevision_revision"),
    ]

    # No way back: a content page made from a section page cannot be told apart
    # from one an editor made, and 0090 deletes the section page tables.
    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
