from django.db import migrations

# The content types that gain a review/approval workflow. Roles and skills are
# now DraftState/Workflow snippets (migration 0080), but the review step only
# runs once a workflow is bound to their content types, so this binds the
# default "Moderators approval" workflow that Wagtail seeds. Admins can change
# or remove it afterwards in Settings -> Workflows.
WORKFLOW_MODELS = ["govukrole", "govukskill"]


def assign_default_workflow(apps, schema_editor):
    Workflow = apps.get_model("wagtailcore", "Workflow")
    WorkflowContentType = apps.get_model("wagtailcore", "WorkflowContentType")
    ContentType = apps.get_model("contenttypes", "ContentType")

    workflow = Workflow.objects.filter(active=True).order_by("pk").first()
    if workflow is None:
        # No workflow to assign (e.g. an install with workflows disabled).
        # Draft/publish still works; leave review unbound rather than crash.
        return

    for model in WORKFLOW_MODELS:
        content_type = ContentType.objects.filter(
            app_label="govuk", model=model
        ).first()
        if content_type is None:
            continue
        WorkflowContentType.objects.get_or_create(
            content_type=content_type,
            defaults={"workflow": workflow},
        )


def unassign_default_workflow(apps, schema_editor):
    WorkflowContentType = apps.get_model("wagtailcore", "WorkflowContentType")
    ContentType = apps.get_model("contenttypes", "ContentType")

    content_type_ids = ContentType.objects.filter(
        app_label="govuk", model__in=WORKFLOW_MODELS
    ).values_list("id", flat=True)
    WorkflowContentType.objects.filter(
        content_type_id__in=list(content_type_ids)
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("govuk", "0080_govukrole_expire_at_govukrole_expired_and_more"),
        ("wagtailcore", "0098_apitoken"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [
        migrations.RunPython(assign_default_workflow, unassign_default_workflow),
    ]
