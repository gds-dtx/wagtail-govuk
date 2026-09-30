# Replaces the two hero-styling booleans with a single hero_style choice field.
#
# Operations run in order: hero_style is added to the four page types that
# expose it, a RunPython then copies each row's (and each draft revision's) old
# booleans into hero_style, and only then are the booleans removed - so the
# historical model carries both during the data step. Follows the RunPython
# pattern in 0084_convert_legacy_govuk_start_button.py (live rows via
# bulk_update, plus a wagtailcore.Revision.content JSON sweep so drafts convert).
#
# The framework page types (frameworkmainpage/frameworkcontentpage/
# frameworkskillspage) carried the booleans but render their hero in-column and
# never exposed styling, so they simply drop the booleans and gain no hero_style.

from django.db import migrations, models

# Page models that gain hero_style AND had the booleans, so their values map across.
MODELS_TO_MAP = ["contentpage", "sectionpage", "taglistingspage"]

# Frozen literals - the field/choice names as at this migration.
STYLED_ATTR = "enable_hero_styling"
COMBINED_ATTR = "enable_combined_service_navigation_and_hero_styling"


def _hero_style_field():
    return models.CharField(
        max_length=20,
        choices=[
            ("hidden", "Not visible"),
            ("none", "No styling"),
            ("large_text", "Large text"),
            ("styled", "Hero styling"),
            ("combined", "Combined service navigation and hero styling"),
        ],
        default="none",
        help_text="How the hero (page title and intro) is shown at the top of the page.",
        verbose_name="Hero style",
    )


def _style_from_booleans(styled, combined) -> str:
    if combined:
        return "combined"
    if styled:
        return "styled"
    return "none"


def _booleans_from_style(style):
    return style == "styled", style == "combined"


def _bulk_update(manager, objs, fields, chunk=500):
    for start in range(0, len(objs), chunk):
        manager.bulk_update(objs[start : start + chunk], fields)


def _migrate_live_fields(apps, db_alias, to_style):
    for model_name in MODELS_TO_MAP:
        model = apps.get_model("govuk", model_name)
        to_update = []
        for obj in model.objects.using(db_alias).iterator(chunk_size=500):
            if to_style:
                obj.hero_style = _style_from_booleans(
                    getattr(obj, STYLED_ATTR), getattr(obj, COMBINED_ATTR)
                )
            else:
                styled, combined = _booleans_from_style(obj.hero_style)
                setattr(obj, STYLED_ATTR, styled)
                setattr(obj, COMBINED_ATTR, combined)
            to_update.append(obj)
        fields = ["hero_style"] if to_style else [STYLED_ATTR, COMBINED_ATTR]
        _bulk_update(model.objects.using(db_alias), to_update, fields)


def _migrate_revisions(apps, db_alias, to_style):
    # Draft field values live in Revision.content JSON keyed by field name. Every
    # revision that still carries the old boolean keys is converted (framework
    # pages included ... setting a hero_style key there is harmless, as those
    # models have no such field and ignore it on load, while the stale booleans
    # are cleared).
    Revision = apps.get_model("wagtailcore", "Revision")
    to_update = []
    for revision in Revision.objects.using(db_alias).iterator(chunk_size=200):
        content = revision.content
        if not isinstance(content, dict):
            continue
        changed = False
        if to_style:
            if STYLED_ATTR in content or COMBINED_ATTR in content:
                content["hero_style"] = _style_from_booleans(
                    content.get(STYLED_ATTR), content.get(COMBINED_ATTR)
                )
                content.pop(STYLED_ATTR, None)
                content.pop(COMBINED_ATTR, None)
                changed = True
        else:
            if "hero_style" in content:
                styled, combined = _booleans_from_style(content.get("hero_style"))
                content[STYLED_ATTR] = styled
                content[COMBINED_ATTR] = combined
                content.pop("hero_style", None)
                changed = True
        if changed:
            revision.content = content
            to_update.append(revision)
    _bulk_update(Revision.objects.using(db_alias), to_update, ["content"], chunk=200)


def forwards(apps, schema_editor):
    db_alias = schema_editor.connection.alias
    _migrate_live_fields(apps, db_alias, to_style=True)
    _migrate_revisions(apps, db_alias, to_style=True)


def backwards(apps, schema_editor):
    db_alias = schema_editor.connection.alias
    _migrate_live_fields(apps, db_alias, to_style=False)
    _migrate_revisions(apps, db_alias, to_style=False)


class Migration(migrations.Migration):

    dependencies = [
        ("govuk", "0084_convert_legacy_govuk_start_button"),
        # Revision (renamed from PageRevision) must resolve for the draft sweep.
        ("wagtailcore", "0070_rename_pagerevision_revision"),
    ]

    operations = [
        # 1. Add hero_style to the four page types that expose it.
        migrations.AddField(
            model_name="contentpage", name="hero_style", field=_hero_style_field()
        ),
        migrations.AddField(
            model_name="newsindexpage", name="hero_style", field=_hero_style_field()
        ),
        migrations.AddField(
            model_name="sectionpage", name="hero_style", field=_hero_style_field()
        ),
        migrations.AddField(
            model_name="taglistingspage", name="hero_style", field=_hero_style_field()
        ),
        # 2. Copy the old booleans (live rows + draft revisions) into hero_style.
        migrations.RunPython(forwards, backwards),
        # 3. Remove the now-unused booleans from every model that carried them.
        migrations.RemoveField(model_name="contentpage", name=COMBINED_ATTR),
        migrations.RemoveField(model_name="contentpage", name=STYLED_ATTR),
        migrations.RemoveField(model_name="frameworkcontentpage", name=COMBINED_ATTR),
        migrations.RemoveField(model_name="frameworkcontentpage", name=STYLED_ATTR),
        migrations.RemoveField(model_name="frameworkmainpage", name=COMBINED_ATTR),
        migrations.RemoveField(model_name="frameworkmainpage", name=STYLED_ATTR),
        migrations.RemoveField(model_name="frameworkskillspage", name=COMBINED_ATTR),
        migrations.RemoveField(model_name="frameworkskillspage", name=STYLED_ATTR),
        migrations.RemoveField(model_name="sectionpage", name=COMBINED_ATTR),
        migrations.RemoveField(model_name="sectionpage", name=STYLED_ATTR),
        migrations.RemoveField(model_name="taglistingspage", name=COMBINED_ATTR),
        migrations.RemoveField(model_name="taglistingspage", name=STYLED_ATTR),
    ]
