"""The framework's three published CSVs, written to any file-like object.

The same rows serve two callers: the management command writes them to disk
for migration checks, and the download views stream them to a reader. Built
from the published content at the moment of asking, so the download cannot
drift from the site the way a file regenerated on a schedule can.
"""

import csv

from govuk.capability_framework import (
    LEADERSHIP_HEADING,
    changelog_html_to_note,
    points_to_text,
    rich_html_to_text,
)
from govuk.models import (
    SKILL_LEVEL_CHOICES,
    GovukChangelogEntry,
    GovukRole,
    GovukSkill,
)

ROLE_COLUMNS = [
    "Role Family",
    "Role",
    "Role Description",
    "Role Level",
    "Role Level Description",
    "Skill Name",
    "Skill Description",
    "Skill Level",
    "Skill Level Description",
    "Role Type",
]
SKILL_COLUMNS = [
    "Skill Name",
    "Skill Description",
    "Awareness",
    "Working",
    "Practitioner",
    "Expert",
    "Roles that require Skill",
]
CHANGELOG_COLUMNS = ["Timestamp", "Page", "Change note"]
NOT_IN_USE = "NOT IN USE"
SCS_ROLE_TYPE = "Senior Civil Service"
SITE_WIDE_PAGE_NAME = "Homepage"


def _scs_skill_description(skill_row: dict) -> str:
    """Rebuild the published prose for a Senior Civil Service skill."""
    description = rich_html_to_text(skill_row["skill"].body)
    leadership = skill_row["leadership_points"]
    if not leadership:
        return description
    bullets = "\n".join(f"- {point}" for point in leadership)
    return f"{description}\n{LEADERSHIP_HEADING}\n{bullets}"


def write_roles_csv(f) -> int:
    rows = 0
    writer = csv.DictWriter(f, fieldnames=ROLE_COLUMNS)
    writer.writeheader()
    for role in GovukRole.objects.order_by("title"):
        role_description = rich_html_to_text(role.body)
        levels = role.get_levels_with_skills()
        if role.is_senior_civil_service:
            # Senior Civil Service roles have flat skills and no levels; the
            # published export marks the level columns as not in use.
            for skill_row in role.get_scs_skills():
                writer.writerow(
                    {
                        "Role Family": role.family,
                        "Role": role.title,
                        "Role Description": role_description,
                        "Role Level": NOT_IN_USE,
                        "Role Level Description": NOT_IN_USE,
                        "Skill Name": skill_row["skill"].title,
                        "Skill Description": _scs_skill_description(skill_row),
                        "Skill Level": NOT_IN_USE,
                        "Skill Level Description": NOT_IN_USE,
                        "Role Type": SCS_ROLE_TYPE,
                    }
                )
                rows += 1
            continue
        if not levels:
            writer.writerow(
                {
                    "Role Family": role.family,
                    "Role": role.title,
                    "Role Description": role_description,
                }
            )
            rows += 1
            continue
        for level in levels:
            for skill_row in level["skills"]:
                skill = skill_row["skill"]
                writer.writerow(
                    {
                        "Role Family": role.family,
                        "Role": role.title,
                        "Role Description": role_description,
                        "Role Level": level["title"],
                        "Role Level Description": rich_html_to_text(
                            level["description"]
                        ),
                        "Skill Name": skill.title,
                        "Skill Description": rich_html_to_text(skill.body),
                        "Skill Level": skill_row["required_level_label"],
                        "Skill Level Description": points_to_text(skill_row["points"]),
                    }
                )
                rows += 1
    return rows


def write_skills_csv(f) -> int:
    roles_by_skill = GovukRole.roles_by_skill_id()
    rows = 0
    writer = csv.DictWriter(f, fieldnames=SKILL_COLUMNS)
    writer.writeheader()
    for skill in GovukSkill.objects.order_by("title"):
        leadership = skill.get_leadership_points()
        description = rich_html_to_text(skill.body)
        if leadership:
            bullets = "\n".join(f"- {point}" for point in leadership)
            description = f"{description}\n{LEADERSHIP_HEADING}\n{bullets}"
        row = {
            "Skill Name": skill.title,
            "Skill Description": description,
            "Roles that require Skill": ", ".join(
                role.title for role in roles_by_skill.get(skill.pk, [])
            ),
        }
        for level_key, level_label in SKILL_LEVEL_CHOICES:
            row[level_label] = points_to_text(skill.points_for_level(level_key))
        writer.writerow(row)
        rows += 1
    return rows


def write_changelog_csv(f) -> int:
    rows = 0
    entries = GovukChangelogEntry.objects.filter(live=True).select_related(
        "role", "skill"
    )
    writer = csv.DictWriter(f, fieldnames=CHANGELOG_COLUMNS)
    writer.writeheader()
    for entry in entries:
        subject = entry.role or entry.skill
        writer.writerow(
            {
                "Timestamp": entry.date.isoformat() if entry.date else "",
                "Page": subject.title if subject else SITE_WIDE_PAGE_NAME,
                "Change note": changelog_html_to_note(entry.note),
            }
        )
        rows += 1
    return rows
