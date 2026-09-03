"""Backfill indicative Civil Service job grades onto role levels.

The published CSV downloads carry role, level and skill content but not the
indicative job grades, so roles imported by ``import_capability_framework``
render without the "This role level is most often performed at..." sentence
that every level on the live site shows.

The grades are public content, so this command reads them back off the live
site until the Strapi export is available. The same pass restores the level
ordering, which the CSV loses because its rows are alphabetical rather than
ordered by seniority.
"""

import json
import re
import time
import urllib.request
from html import unescape
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.utils.text import slugify

from govuk.models import JOB_GRADE_CHOICES, GovukRole

LIVE_BASE_URL = "https://ddat-capability-framework.service.gov.uk"
# Every run inside the tag stops at a "<", which a tag cannot hold anyway.
# Spelt "[^>]*" and "[^"]*" the three of them nest, and a page of repeated
# "<h3 class=..." with no ">" in it took 157 seconds at 116KB to refuse.
LEVEL_HEADING = re.compile(
    r'<h3[^<>]*class="[^"<>]*role-level-header[^"<>]*"[^<>]*>(.*?)</h3>', re.S
)
GRADE_SENTENCE = re.compile(
    r"performed at\s+the\s+Civil Service job grade of:(.*?)(?:Skill\s+Description|Level:|$)",
    re.S,
)
GRADE_ITEM = re.compile(r"\b([A-Z][A-Za-z0-9]*(?:\s\d)?)\s*\(([^)]{3,60})\)")
GRADE_KEY_BY_LABEL = {label: value for value, label in JOB_GRADE_CHOICES}
GRADE_KEYS = {value for value, _ in JOB_GRADE_CHOICES}


def grade_keys(raw_grades) -> list[str]:
    """Normalise grades written as either stored keys or display labels."""
    keys = []
    for raw_grade in raw_grades or []:
        grade = str(raw_grade).strip()
        key = grade if grade in GRADE_KEYS else GRADE_KEY_BY_LABEL.get(grade)
        if key and key not in keys:
            keys.append(key)
    return keys


class Command(BaseCommand):
    help = "Backfill role level job grades and level ordering from the live site"

    def add_arguments(self, parser):
        parser.add_argument(
            "--json",
            dest="json_path",
            help="Apply a previously saved grades file instead of fetching.",
        )
        parser.add_argument(
            "--save",
            dest="save_path",
            help="Write the fetched grades to this path for later re-use.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without writing anything.",
        )

    def handle(self, *args, **options):
        if options["json_path"]:
            path = Path(options["json_path"])
            if not path.exists():
                raise CommandError(f"Missing {path}")
            grades_by_slug = json.loads(path.read_text())
        else:
            grades_by_slug = self.fetch_from_live()
            if options["save_path"]:
                Path(options["save_path"]).write_text(
                    json.dumps(grades_by_slug, indent=1)
                )
                self.stdout.write(f"Saved fetched grades to {options['save_path']}")

        self.apply(grades_by_slug, dry_run=options["dry_run"])

    def fetch_from_live(self) -> dict:
        slugs = list(GovukRole.objects.values_list("slug", flat=True))
        if not slugs:
            raise CommandError("No roles found. Run import_capability_framework first.")

        fetched: dict[str, dict] = {}
        for position, slug in enumerate(slugs, 1):
            url = f"{LIVE_BASE_URL}/role/{slug}"
            try:
                request = urllib.request.Request(
                    url, headers={"User-Agent": "wagtail-govuk-grade-import"}
                )
                with urllib.request.urlopen(request, timeout=30) as response:
                    body = response.read().decode("utf-8", "replace")
            except OSError as exc:
                self.stderr.write(self.style.WARNING(f"  Could not fetch {url}: {exc}"))
                continue

            fetched[slug] = self.parse_role_page(body)
            self.stdout.write(
                f"[{position:2}/{len(slugs)}] fetched {slug}", ending="\r"
            )
            # The live site is a production service; do not hammer it.
            time.sleep(0.2)

        self.stdout.write("")
        return fetched

    @classmethod
    def parse_role_page(cls, body: str) -> dict:
        headings = [
            (match.start(), match.end(), cls._heading_title(match.group(1)))
            for match in LEVEL_HEADING.finditer(body)
        ]

        levels = []
        for index, (_, heading_end, title) in enumerate(headings):
            section_end = (
                headings[index + 1][0] if index + 1 < len(headings) else len(body)
            )
            levels.append(
                {
                    "title": title,
                    "grades": cls._grades_in(body[heading_end:section_end]),
                }
            )

        # Senior Civil Service roles have no levels; their grades sit on the
        # role itself, above where the first level heading would be.
        role_body = body[: headings[0][0]] if headings else body
        return {"role_grades": cls._grades_in(role_body), "levels": levels}

    @staticmethod
    def _heading_title(raw_heading: str) -> str:
        title = unescape(re.sub(r"<[^<>]+>", "", raw_heading)).strip()
        return re.sub(r"^\s*\d+\.\s*", "", title).strip()

    @staticmethod
    def _grades_in(fragment: str) -> list[str]:
        text = re.sub(r"\s+", " ", unescape(re.sub(r"<[^<>]+>", " ", fragment)))
        sentence = GRADE_SENTENCE.search(text)
        if not sentence:
            return []

        keys: list[str] = []
        for abbreviation, expansion in GRADE_ITEM.findall(sentence.group(1)[:220]):
            label = f"{abbreviation.strip()} ({expansion.strip()})"
            key = GRADE_KEY_BY_LABEL.get(label)
            if key and key not in keys:
                keys.append(key)
        return keys

    def apply(self, grades_by_slug: dict, *, dry_run: bool):
        graded = reordered = skipped = 0
        unmatched: list[str] = []

        for slug, entry in grades_by_slug.items():
            role = GovukRole.objects.filter(slug=slug).first()
            if role is None:
                unmatched.append(slug)
                skipped += 1
                continue

            changed = False
            role_grades = grade_keys(entry.get("role_grades"))
            if role_grades:
                role.scs_grades = json.dumps(
                    [{"type": "grade", "value": key} for key in role_grades]
                )
                changed = True

            live_levels = entry.get("levels") or []
            if live_levels and role.levels.raw_data:
                new_levels, did_reorder = self._rebuild_levels(role, live_levels)
                if new_levels is not None:
                    role.levels = json.dumps(new_levels)
                    changed = True
                    reordered += int(did_reorder)

            if not changed:
                continue

            graded += 1
            if not dry_run:
                role.save()

        prefix = "Would update" if dry_run else "Updated"
        self.stdout.write(f"{prefix} {graded} roles ({reordered} reordered)")
        if unmatched:
            self.stdout.write(
                f"  no matching role for: {', '.join(sorted(unmatched)[:10])}"
            )

    @staticmethod
    def _rebuild_levels(role: GovukRole, live_levels: list[dict]):
        """Return the role's levels in live order with grades attached."""
        stored = list(role.levels.raw_data)
        by_title: dict[str, dict] = {}
        for block in stored:
            title = ((block.get("value") or {}).get("title") or "").strip()
            by_title.setdefault(slugify(title), block)

        ordered: list[dict] = []
        used: set[int] = set()
        for live_level in live_levels:
            block = by_title.get(slugify(live_level["title"]))
            if block is None or id(block) in used:
                continue
            used.add(id(block))
            block = dict(block)
            block["value"] = {
                **(block.get("value") or {}),
                "grades": grade_keys(live_level.get("grades")),
            }
            ordered.append(block)

        # Keep anything the live page did not mention rather than dropping it.
        remainder = [block for block in stored if id(block) not in used]
        if not ordered:
            return None, False

        did_reorder = [block.get("id") for block in ordered + remainder] != [
            block.get("id") for block in stored
        ]
        return ordered + remainder, did_reorder
