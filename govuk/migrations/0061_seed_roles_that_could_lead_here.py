"""Seed the progression mapping the framework already publishes.

The field added in 0060 arrived empty, so on every environment a Senior Civil
Service role would show nothing leading into it until someone typed the mapping
back in by hand. These are the four senior roles the live service lists, in the
order it lists them.

A role that already names its own is left alone, so this can run on a database
an editor has worked on, and re-running it changes nothing.
"""

import json

from django.db import migrations

# Senior role slug -> the roles that could lead to it, in the published order.
PROGRESSION_BY_SENIOR_ROLE = {
    "chief-data-officer": [
        "data-architect",
        "data-analyst",
        "data-engineer",
        "data-governance-manager",
        "data-scientist",
        "programme-delivery-manager",
    ],
    "chief-digital-and-information-officer": [
        "chief-data-officer",
        "chief-technology-officer",
        "chief-information-security-officer",
    ],
    "chief-information-security-officer": [
        "it-service-manager",
        "technical-architect",
        "security-architect",
    ],
    "chief-technology-officer": [
        "frontend-developer",
        "it-service-manager",
        "software-developer",
        "technical-architect",
        "development-operations-devops-engineer",
    ],
}


def seed(apps, schema_editor):
    GovukRole = apps.get_model("govuk", "GovukRole")
    role_ids = dict(GovukRole.objects.values_list("slug", "pk"))

    for senior_slug, source_slugs in PROGRESSION_BY_SENIOR_ROLE.items():
        senior_id = role_ids.get(senior_slug)
        if senior_id is None:
            continue

        senior = GovukRole.objects.get(pk=senior_id)
        if len(senior.roles_that_could_lead_here):
            continue

        # Written as the raw JSON the field stores, with ids derived from the
        # two slugs so that running this twice cannot produce two different
        # databases. A role missing from this site is passed over rather than
        # left as a broken reference.
        blocks = [
            {"type": "role", "value": role_ids[slug], "id": f"{senior_slug}--{slug}"}
            for slug in source_slugs
            if slug in role_ids
        ]
        if not blocks:
            continue

        senior.roles_that_could_lead_here = json.dumps(blocks)
        senior.save(update_fields=["roles_that_could_lead_here"])


class Migration(migrations.Migration):

    dependencies = [
        ("govuk", "0060_govukrole_roles_that_could_lead_here"),
    ]

    # Reversing steps back to 0060, which drops the column outright, so there
    # is nothing for a backwards step to undo.
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
