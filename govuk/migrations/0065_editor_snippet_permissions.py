"""Let editors edit the framework.

Roles, skills, changelog entries, tags and external links are snippets, not
pages, and snippet permissions are separate from page permissions. Wagtail's
own initial data gives the Editors and Moderators groups `access_admin` plus
images and documents, and nothing else -- so an account in Editors could sign
in, see the page tree, and could not open a single role or skill. Dev has
hidden this because five of its eight accounts are superusers.

Add is granted alongside change deliberately: an editor who can amend a role
but cannot create one has to ask a superuser every time the framework gains a
role, which is the whole job. Delete stays with Moderators -- a deleted snippet
takes its references out of every page that used it.

Reversing removes only what was granted here.
"""

from django.apps import apps as installed_apps
from django.contrib.auth.management import create_permissions
from django.db import migrations

# (app_label, model_name) for every snippet the capability framework registers.
SNIPPET_MODELS = [
    ("govuk", "govukrole"),
    ("govuk", "govukskill"),
    ("govuk", "govukchangelogentry"),
    ("govuk", "govuktag"),
    ("govuk", "externalcontentitem"),
]

EDITOR_ACTIONS = ["add", "change"]
MODERATOR_ACTIONS = ["add", "change", "delete"]

# Site-wide labels -- the breadcrumb wording, the family group titles. Changing
# one rewords every page at once, so it sits with the moderators.
MODERATOR_ONLY = [("govuk", "capabilityframeworkwordingsettings", ["change"])]

# Feedback is submitted by the public. Both groups can read it; neither should
# be writing words into somebody else's response.
READ_ONLY = [("govuk", "feedback", ["view"])]


def _ensure_permissions_exist(apps, schema_editor):
    """Create this app's Permission rows early.

    Django creates them from a post_migrate signal, which fires after every
    migration has run -- so on a fresh database the rows this migration wants
    to grant do not exist yet and the grant would silently do nothing. Asking
    for them by hand is the documented way out.
    """
    app_config = installed_apps.get_app_config("govuk")
    models_module = app_config.models_module
    app_config.models_module = True
    try:
        create_permissions(
            app_config, apps=apps, using=schema_editor.connection.alias, verbosity=0
        )
    finally:
        app_config.models_module = models_module


def _permissions_for(apps, grants):
    """Resolve (app_label, model, actions) triples to Permission rows."""
    ContentType = apps.get_model("contenttypes", "ContentType")
    Permission = apps.get_model("auth", "Permission")

    found = []
    for app_label, model_name, actions in grants:
        content_type = ContentType.objects.filter(
            app_label=app_label, model=model_name
        ).first()
        if content_type is None:
            # The model was removed by a later migration in someone's branch;
            # there is nothing to grant and nothing to complain about.
            continue
        for action in actions:
            permission = Permission.objects.filter(
                content_type=content_type, codename=f"{action}_{model_name}"
            ).first()
            if permission is not None:
                found.append(permission)
    return found


def _grants(actions):
    return [(app, model, actions) for app, model in SNIPPET_MODELS]


def _apply(apps, add):
    Group = apps.get_model("auth", "Group")

    for group_name, grants in (
        ("Editors", _grants(EDITOR_ACTIONS) + READ_ONLY),
        ("Moderators", _grants(MODERATOR_ACTIONS) + MODERATOR_ONLY + READ_ONLY),
    ):
        group = Group.objects.filter(name=group_name).first()
        if group is None:
            continue
        permissions = _permissions_for(apps, grants)
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
        ("govuk", "0064_customisesettings_error_contact_about_and_more"),
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
        ("wagtailcore", "0002_initial_data"),
    ]

    operations = [migrations.RunPython(grant, revoke)]
