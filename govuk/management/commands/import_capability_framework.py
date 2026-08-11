import csv
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.utils.html import escape
from django.utils.text import slugify
from wagtail.models import Page, Site

from govuk.capability_framework import (
    NOT_IN_USE,
    changelog_note_to_html,
    parse_iso_date,
    parse_points,
    split_leadership_examples,
    text_to_rich_html,
)
from govuk.models import (
    ContentPage,
    GovukChangelogEntry,
    GovukRole,
    GovukSkill,
    RolePage,
    SkillsAZPage,
)

SITE_NAME = "Government Digital and Data Profession Capability Framework"
SITE_INTRO = (
    "Learn about the digital, data and technology roles in government, "
    "and the skills you need to do them."
)

SKILL_LEVELS = ("awareness", "working", "practitioner", "expert")
SITE_WIDE_CHANGELOG_PAGES = {"homepage", "home page", "home"}


def points_stream(points: list[str]) -> str:
    return json.dumps([{"type": "point", "value": p} for p in points])


class Command(BaseCommand):
    help = "Import Capability Framework content from the public CSV exports"

    def add_arguments(self, parser):
        parser.add_argument("data_dir", help="Directory containing roles.csv and skills.csv")

    def handle(self, *args, **options):
        data_dir = Path(options["data_dir"])
        roles_csv = data_dir / "roles.csv"
        skills_csv = data_dir / "skills.csv"
        for path in (roles_csv, skills_csv):
            if not path.exists():
                raise CommandError(f"Missing {path}")

        skills_by_slug = self.import_skills(skills_csv)
        roles = self.import_roles(roles_csv, skills_by_slug)
        self.create_pages(roles)

        changelog_csv = data_dir / "changelog.csv"
        if changelog_csv.exists():
            self.import_changelog(changelog_csv)
        else:
            self.stdout.write("No changelog.csv found, skipping changelog import")

    def import_changelog(self, changelog_csv: Path):
        roles_by_slug = {role.slug: role for role in GovukRole.objects.all()}
        skills_by_slug = {skill.slug: skill for skill in GovukSkill.objects.all()}

        created = skipped = 0
        unmatched: set[str] = set()
        with open(changelog_csv) as f:
            for row in csv.DictReader(f):
                entry_date = parse_iso_date(row.get("Timestamp", ""))
                note_html = changelog_note_to_html(row.get("Change note", ""))
                if not entry_date or not note_html:
                    skipped += 1
                    continue

                page_name = (row.get("Page") or "").strip()
                role = skill = None
                if page_name.lower() not in SITE_WIDE_CHANGELOG_PAGES:
                    page_slug = slugify(page_name)[:120]
                    role = roles_by_slug.get(page_slug)
                    if role is None:
                        skill = skills_by_slug.get(page_slug)
                    if role is None and skill is None:
                        unmatched.add(page_name)
                        skipped += 1
                        continue

                # Match on the natural key so re-runs do not duplicate entries.
                _, was_created = GovukChangelogEntry.objects.get_or_create(
                    date=entry_date,
                    role=role,
                    skill=skill,
                    note=note_html,
                    defaults={"live": True},
                )
                created += int(was_created)

        self.stdout.write(f"Changelog: {created} created, {skipped} skipped")
        if unmatched:
            self.stdout.write(
                "  unmatched pages: " + ", ".join(sorted(unmatched)[:10])
            )

    def import_skills(self, skills_csv: Path) -> dict[str, GovukSkill]:
        created = updated = 0
        skills_by_slug: dict[str, GovukSkill] = {}
        with open(skills_csv) as f:
            for row in csv.DictReader(f):
                title = row["Skill Name"].strip()
                if not title:
                    continue
                slug = slugify(title)[:120]
                skill = GovukSkill.objects.filter(slug=slug).first()
                if skill is None:
                    skill = GovukSkill(slug=slug)
                    created += 1
                else:
                    updated += 1
                skill.title = title
                description, leadership = split_leadership_examples(
                    row["Skill Description"]
                )
                skill.body = text_to_rich_html(description)
                skill.leadership_points = points_stream(leadership)
                # Skills with leadership examples and no proficiency levels
                # belong to Senior Civil Service roles.
                skill.is_senior_civil_service = bool(leadership) or not any(
                    row[level].strip()
                    for level in ("Awareness", "Working", "Practitioner", "Expert")
                )
                skill.awareness_points = points_stream(parse_points(row["Awareness"]))
                skill.working_points = points_stream(parse_points(row["Working"]))
                skill.practitioner_points = points_stream(parse_points(row["Practitioner"]))
                skill.expert_points = points_stream(parse_points(row["Expert"]))
                skill.save()
                skills_by_slug[slug] = skill
        self.stdout.write(f"Skills: {created} created, {updated} updated")
        return skills_by_slug

    def import_roles(self, roles_csv: Path, skills_by_slug: dict) -> list[dict]:
        # Group the flat (role, level, skill) rows back into a hierarchy.
        roles: dict[str, dict] = {}
        with open(roles_csv) as f:
            for row in csv.DictReader(f):
                role_title = row["Role"].strip()
                if not role_title:
                    continue
                role = roles.setdefault(
                    role_title,
                    {
                        "title": role_title,
                        "family": row["Role Family"].strip(),
                        "description": row["Role Description"],
                        "is_scs": row["Role Type"].strip() == "Senior Civil Service",
                        "levels": {},
                        "scs_skills": [],
                    },
                )
                if role["is_scs"]:
                    # SCS rows carry flat skills; level columns are NOT IN USE.
                    role["scs_skills"].append(
                        {
                            "name": row["Skill Name"].strip(),
                            "description": row["Skill Description"],
                        }
                    )
                    continue
                level_title = row["Role Level"].strip()
                level = role["levels"].setdefault(
                    level_title,
                    {"description": row["Role Level Description"], "skills": []},
                )
                skill_slug = slugify(row["Skill Name"].strip())[:120]
                skill = skills_by_slug.get(skill_slug)
                skill_level = row["Skill Level"].strip().lower()
                if skill and skill_level in SKILL_LEVELS:
                    entry = {"skill": skill.pk, "level": skill_level}
                    # the export contains some byte-identical duplicate rows
                    if entry not in level["skills"]:
                        level["skills"].append(entry)

        created = updated = 0
        results = []
        for role_data in roles.values():
            slug = slugify(role_data["title"])[:120]
            role = GovukRole.objects.filter(slug=slug).first()
            if role is None:
                role = GovukRole(slug=slug)
                created += 1
            else:
                updated += 1
            role.title = role_data["title"]
            role.family = role_data["family"]

            body_html = text_to_rich_html(role_data["description"])
            if role_data["is_scs"]:
                role.is_senior_civil_service = True
                role.levels = json.dumps([])
                scs_skill_ids = []
                for scs_skill in role_data["scs_skills"]:
                    skill = skills_by_slug.get(slugify(scs_skill["name"])[:120])
                    if skill and skill.pk not in scs_skill_ids:
                        scs_skill_ids.append(skill.pk)
                role.scs_skills = json.dumps(
                    [{"type": "skill", "value": pk} for pk in scs_skill_ids]
                )
                # NOTE: indicative SCS grades (SCS 1/2/3) are not in the CSV
                # exports and need a Strapi-side export to populate.
            else:
                role.levels = json.dumps(
                    [
                        {
                            "type": "level",
                            "value": {
                                "title": level_title,
                                "description": text_to_rich_html(level["description"]),
                                "skills": level["skills"],
                            },
                        }
                        for level_title, level in role_data["levels"].items()
                    ]
                )
            role.body = body_html
            role.save()
            results.append({"role": role, "data": role_data})
        self.stdout.write(f"Roles: {created} created, {updated} updated")
        return results

    def create_pages(self, roles: list[dict]):
        home = self.ensure_home_page()
        pages_created = pages_updated = 0

        for entry in roles:
            role = entry["role"]
            page = RolePage.objects.filter(slug=role.slug).first()
            if page is None:
                page = RolePage(
                    title=role.title,
                    slug=role.slug,
                    selected_roles=json.dumps([{"type": "role", "value": role.pk}]),
                )
                home.add_child(instance=page)
                pages_created += 1
            else:
                page.title = role.title
                page.selected_roles = json.dumps([{"type": "role", "value": role.pk}])
                pages_updated += 1
            page.save_revision().publish()

        if not SkillsAZPage.objects.filter(slug="skills").exists():
            skills_page = SkillsAZPage(title="Skills A to Z", slug="skills")
            home.add_child(instance=skills_page)
            skills_page.save_revision().publish()
            self.stdout.write("Created Skills A to Z page")

        self.stdout.write(f"Role pages: {pages_created} created, {pages_updated} updated")
        self.write_home_page_content(home)

    def ensure_home_page(self) -> Page:
        """Return the site's home page, replacing Wagtail's default if present."""
        site = Site.objects.filter(is_default_site=True).first()
        if site is None:
            return Page.objects.get(depth=2).specific

        if site.site_name != SITE_NAME:
            site.site_name = SITE_NAME
            site.save(update_fields=["site_name"])

        home = site.root_page.specific
        if isinstance(home, ContentPage):
            return home

        # The starter site ships a placeholder home page that cannot hold the
        # framework's introduction, so swap it for a content page once. The
        # placeholder already owns the "home" slug, so claim it after removal.
        root = home.get_parent()
        new_home = ContentPage(
            title=SITE_NAME, slug="framework-home", show_last_updated_date=True
        )
        root.add_child(instance=new_home)
        new_home.save_revision().publish()

        for child in list(home.get_children()):
            child.move(new_home, pos="last-child")

        site.root_page = new_home
        site.save(update_fields=["root_page"])
        home.refresh_from_db()
        home.delete()

        new_home.refresh_from_db()
        new_home.slug = "home"
        new_home.save()
        new_home.save_revision().publish()
        self.stdout.write("Replaced the placeholder home page")
        return ContentPage.objects.get(pk=new_home.pk)

    def write_home_page_content(self, home: Page):
        """Set the framework home page up.

        The role lists and update history are built from the imported content
        when the page is rendered. The welcome prose is deliberately not written
        here: it is page content that the content team owns in the CMS, and it
        travels between instances through the admin page import, not this
        command.
        """
        if not isinstance(home, ContentPage):
            return

        home.hero_intro = f"<p>{escape(SITE_INTRO)}</p>"
        home.body = ""
        home.show_role_navigation = True
        home.show_framework_updates = True
        home.save()
        home.save_revision().publish()
        self.stdout.write("Home page: role navigation and updates switched on")
