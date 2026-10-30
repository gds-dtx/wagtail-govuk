"""Let editors manage the content catalogue's vocabularies.

The types, scopes and professions are snippets, and snippet permissions are
separate from page permissions: without this an editor could create a
catalogue entry but not add the type it needs. As with the tags in 0065,
editors may add and change them; deleting one, which every entry using it
depends on, stays with moderators.

Reversing removes only what was granted here.
"""

from django.db import migrations

VOCABULARY_MODELS = ["cataloguetype", "cataloguescope", "catalogueprofession"]
GRANTS = {
    "Editors": ["add", "change"],
    "Moderators": ["add", "change", "delete"],
}


def _ensure_permissions_exist(apps, schema_editor):
    """Create the vocabularies' Permission rows now, not after every migration.

    Only these three models': creating the whole app's here would add rows
    under the new names of models renamed earlier, beside the rows 0065 made
    under their old names, and Django's rename of those after migrating would
    then refuse to run.
    """
    ContentType = apps.get_model("contenttypes", "ContentType")
    Permission = apps.get_model("auth", "Permission")
    for model_name in VOCABULARY_MODELS:
        model = apps.get_model("govuk", model_name)
        content_type, _ = ContentType.objects.get_or_create(app_label="govuk", model=model_name)
        for action in model._meta.default_permissions:
            Permission.objects.get_or_create(
                content_type=content_type,
                codename=f"{action}_{model_name}",
                defaults={"name": f"Can {action} {model._meta.verbose_name_raw}"},
            )


def _apply(apps, add):
    ContentType = apps.get_model("contenttypes", "ContentType")
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    for group_name, actions in GRANTS.items():
        group = Group.objects.filter(name=group_name).first()
        if group is None:
            continue
        permissions = Permission.objects.filter(
            content_type__in=ContentType.objects.filter(
                app_label="govuk", model__in=VOCABULARY_MODELS
            ),
            codename__in=[
                f"{action}_{model}" for action in actions for model in VOCABULARY_MODELS
            ],
        )
        if add:
            group.permissions.add(*permissions)
        else:
            group.permissions.remove(*permissions)


def grant(apps, schema_editor):
    _ensure_permissions_exist(apps, schema_editor)
    _apply(apps, add=True)


def revoke(apps, schema_editor):
    _apply(apps, add=False)


class Migration(migrations.Migration):
    dependencies = [
        ("govuk", "0093_content_catalogue"),
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [migrations.RunPython(grant, revoke)]
