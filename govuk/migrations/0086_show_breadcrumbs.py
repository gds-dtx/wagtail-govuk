# Adds the per-page show_breadcrumbs switch to the four non-framework page types.
#
# The field defaults to True, so existing pages keep their breadcrumbs ... except
# combined service-navigation/hero pages, which showed none before, so the data
# step turns the switch off for them. Frame workpage types keep their own fixed
# breadcrumb rules and gain no field.

from django.db import migrations, models

# Page models that gain the field (all carry hero_style, so "combined" is readable).
MODELS = ["contentpage", "newsindexpage", "sectionpage", "taglistingspage"]


def _field():
    return models.BooleanField(
        default=True,
        help_text="Show the breadcrumb trail at the top of the page.",
        verbose_name="Show breadcrumbs",
    )


def forwards(apps, schema_editor):
    db_alias = schema_editor.connection.alias
    # Combined pages showed no breadcrumbs before, so start them switched off.
    for model_name in MODELS:
        model = apps.get_model("govuk", model_name)
        model.objects.using(db_alias).filter(hero_style="combined").update(
            show_breadcrumbs=False
        )

    # Drafts store field values in Revision.content JSON; seed the new key so a
    # combined page's unpublished draft previews without breadcrumbs too.
    Revision = apps.get_model("wagtailcore", "Revision")
    to_update = []
    for revision in Revision.objects.using(db_alias).iterator(chunk_size=200):
        content = revision.content
        if not isinstance(content, dict) or "hero_style" not in content:
            continue
        content["show_breadcrumbs"] = content.get("hero_style") != "combined"
        revision.content = content
        to_update.append(revision)
        if len(to_update) >= 200:
            Revision.objects.using(db_alias).bulk_update(to_update, ["content"])
            to_update = []
    if to_update:
        Revision.objects.using(db_alias).bulk_update(to_update, ["content"])


def backwards(apps, schema_editor):
    # The columns go with RemoveField's reversal; just drop the draft key.
    db_alias = schema_editor.connection.alias
    Revision = apps.get_model("wagtailcore", "Revision")
    to_update = []
    for revision in Revision.objects.using(db_alias).iterator(chunk_size=200):
        content = revision.content
        if isinstance(content, dict) and "show_breadcrumbs" in content:
            content.pop("show_breadcrumbs", None)
            revision.content = content
            to_update.append(revision)
        if len(to_update) >= 200:
            Revision.objects.using(db_alias).bulk_update(to_update, ["content"])
            to_update = []
    if to_update:
        Revision.objects.using(db_alias).bulk_update(to_update, ["content"])


class Migration(migrations.Migration):

    dependencies = [
        ("govuk", "0085_hero_style"),
        ("wagtailcore", "0070_rename_pagerevision_revision"),
    ]

    operations = [
        migrations.AddField(
            model_name="contentpage", name="show_breadcrumbs", field=_field()
        ),
        migrations.AddField(
            model_name="newsindexpage", name="show_breadcrumbs", field=_field()
        ),
        migrations.AddField(
            model_name="sectionpage", name="show_breadcrumbs", field=_field()
        ),
        migrations.AddField(
            model_name="taglistingspage", name="show_breadcrumbs", field=_field()
        ),
        migrations.RunPython(forwards, backwards),
    ]
