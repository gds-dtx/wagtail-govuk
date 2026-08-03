from django.db import migrations

# Frozen literals mirroring govuk.wagtail_hooks constants as of this migration:
#   GOVUK_START_BUTTON_LINKTYPE = "govuk-start-button"
#   GOVUK_BUTTON_LINKTYPE       = "govuk-button"
#   GOVUK_BUTTON_STYLE_ATTR     = "data-govuk-button-style"
#   GOVUK_BUTTON_STYLE_START    = "start"
# They are intentionally hardcoded, not imported: the legacy handlers (and the
# GOVUK_START_BUTTON_LINKTYPE constant) are removed in the same change that adds this
# migration, so importing them would break this historical migration. Wagtail always
# serialises rich-text link tags with double quotes, so the substring match is exact.
LEGACY = 'linktype="govuk-start-button"'
NEW = 'linktype="govuk-button" data-govuk-button-style="start"'

# Only unrestricted RichTextFields can contain button markup. Every other rich text
# field / StreamField block in this app uses a restricted `features` list that excludes
# the button, so it cannot hold legacy markup. GovukSkill/GovukRole are non-revisionable
# and restricted, so they are absent here and from the revision sweep below.
LEGACY_FIELDS = [
    ("govuk", "ContentPage", "body"),
    ("govuk", "RolePage", "body"),
    ("govuk", "SkillsAZPage", "body"),
    ("govuk", "TagListingsPage", "free_text"),
    ("govuk", "SectionPage", "free_text"),
]


def _replace_in_json(node, src, dst):
    """Recursively replace ``src`` with ``dst`` in every string within a JSON value.

    Returns ``(new_node, changed)``. Non-string leaves pass through untouched.
    """
    if isinstance(node, str):
        if src in node:
            return node.replace(src, dst), True
        return node, False
    if isinstance(node, dict):
        changed = False
        out = {}
        for key, value in node.items():
            new_value, value_changed = _replace_in_json(value, src, dst)
            out[key] = new_value
            changed = changed or value_changed
        return out, changed
    if isinstance(node, list):
        changed = False
        out = []
        for value in node:
            new_value, value_changed = _replace_in_json(value, src, dst)
            out.append(new_value)
            changed = changed or value_changed
        return out, changed
    return node, False


def _migrate_live_fields(apps, db_alias, src, dst):
    for app_label, model_name, field in LEGACY_FIELDS:
        model = apps.get_model(app_label, model_name)
        to_update = []
        queryset = model.objects.using(db_alias).exclude(**{f"{field}__isnull": True})
        for obj in queryset.iterator(chunk_size=500):
            value = getattr(obj, field) or ""
            if src in value:
                setattr(obj, field, value.replace(src, dst))
                to_update.append(obj)
        # bulk_update issues raw UPDATEs: no save(), no signals, no new revisions,
        # no search reindex.
        for start in range(0, len(to_update), 500):
            model.objects.using(db_alias).bulk_update(
                to_update[start : start + 500], [field]
            )


def _migrate_revisions(apps, db_alias, src, dst):
    Revision = apps.get_model("wagtailcore", "Revision")
    to_update = []
    for revision in Revision.objects.using(db_alias).iterator(chunk_size=200):
        new_content, changed = _replace_in_json(revision.content, src, dst)
        if changed:
            revision.content = new_content
            to_update.append(revision)
        if len(to_update) >= 200:
            Revision.objects.using(db_alias).bulk_update(to_update, ["content"])
            to_update = []
    if to_update:
        Revision.objects.using(db_alias).bulk_update(to_update, ["content"])


def _assert_no_legacy(apps, db_alias, src):
    """Fail loudly if any legacy markup survives, rolling back the migration.

    Guards against removing the compatibility handlers while orphaned legacy links
    remain — those would otherwise render as broken href-less anchors.
    """
    leftovers = []
    for app_label, model_name, field in LEGACY_FIELDS:
        model = apps.get_model(app_label, model_name)
        count = (
            model.objects.using(db_alias)
            .filter(**{f"{field}__contains": src})
            .count()
        )
        if count:
            leftovers.append(f"{model_name}.{field}: {count}")

    Revision = apps.get_model("wagtailcore", "Revision")
    revision_hits = 0
    for revision in Revision.objects.using(db_alias).iterator(chunk_size=200):
        _, changed = _replace_in_json(revision.content, src, "")
        if changed:
            revision_hits += 1
    if revision_hits:
        leftovers.append(f"Revision.content: {revision_hits} rows")

    if leftovers:
        raise RuntimeError(
            "Legacy govuk-start-button markup still present after migration: "
            + "; ".join(leftovers)
        )


def forwards(apps, schema_editor):
    db_alias = schema_editor.connection.alias
    _migrate_live_fields(apps, db_alias, LEGACY, NEW)
    _migrate_revisions(apps, db_alias, LEGACY, NEW)
    _assert_no_legacy(apps, db_alias, LEGACY)


def backwards(apps, schema_editor):
    db_alias = schema_editor.connection.alias
    _migrate_live_fields(apps, db_alias, NEW, LEGACY)
    _migrate_revisions(apps, db_alias, NEW, LEGACY)


class Migration(migrations.Migration):
    dependencies = [
        ("govuk", "0049_phasebannersettings_phase_text"),
        # Ensures the Revision model (renamed from PageRevision) resolves.
        ("wagtailcore", "0070_rename_pagerevision_revision"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
