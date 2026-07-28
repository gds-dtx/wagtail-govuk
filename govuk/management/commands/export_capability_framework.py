"""Export Capability Framework content as the published CSV downloads.

Produces the three CSVs the framework publishes for reuse by departments
(roles, skills and change notes), generated from the content held in
Wagtail so the download stays in step with what is published.

Also useful as a migration check: exporting and diffing against the source
files shows exactly what survived an import.

Usage:
    python manage.py export_capability_framework /path/to/output-dir
"""

import csv
from pathlib import Path

from django.core.management.base import BaseCommand

from govuk.capability_framework import (
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
SITE_WIDE_PAGE_NAME = "Homepage"


class Command(BaseCommand):
    help = "Export Capability Framework content to the published CSV formats"

    def add_arguments(self, parser):
        parser.add_argument("output_dir", help="Directory to write the CSV files to")

    def handle(self, *args, **options):
        output_dir = Path(options["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)

        self.export_roles(output_dir / "roles.csv")
        self.export_skills(output_dir / "skills.csv")
        self.export_changelog(output_dir / "changelog.csv")

    def export_roles(self, path: Path):
        rows = 0
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=ROLE_COLUMNS)
            writer.writeheader()
            for role in GovukRole.objects.order_by("title"):
                role_description = rich_html_to_text(role.body)
                levels = role.get_levels_with_skills()
                if not levels:
                    # Roles with no levels (currently Senior Civil Service
                    # roles) still belong in the export.
                    writer.writerow(
                        {
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
                                "Role": role.title,
                                "Role Description": role_description,
                                "Role Level": level["title"],
                                "Role Level Description": rich_html_to_text(
                                    level["description"]
                                ),
                                "Skill Name": skill.title,
                                "Skill Description": rich_html_to_text(skill.body),
                                "Skill Level": skill_row["required_level_label"],
                                "Skill Level Description": points_to_text(
                                    skill_row["points"]
                                ),
                            }
                        )
                        rows += 1
        self.stdout.write(f"Roles: {rows} rows -> {path}")

    def export_skills(self, path: Path):
        roles_by_skill = GovukRole.roles_by_skill_id()
        rows = 0
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=SKILL_COLUMNS)
            writer.writeheader()
            for skill in GovukSkill.objects.order_by("title"):
                row = {
                    "Skill Name": skill.title,
                    "Skill Description": rich_html_to_text(skill.body),
                    "Roles that require Skill": ", ".join(
                        role.title for role in roles_by_skill.get(skill.pk, [])
                    ),
                }
                for level_key, level_label in SKILL_LEVEL_CHOICES:
                    row[level_label] = points_to_text(skill.points_for_level(level_key))
                writer.writerow(row)
                rows += 1
        self.stdout.write(f"Skills: {rows} rows -> {path}")

    def export_changelog(self, path: Path):
        rows = 0
        entries = GovukChangelogEntry.objects.filter(live=True).select_related(
            "role", "skill"
        )
        with open(path, "w", newline="") as f:
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
        self.stdout.write(f"Changelog: {rows} rows -> {path}")
