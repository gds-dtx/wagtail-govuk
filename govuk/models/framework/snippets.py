
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.text import slugify
from wagtail import blocks
from wagtail.admin.panels import FieldPanel
from wagtail.fields import RichTextField, StreamField
from wagtail.snippets.blocks import SnippetChooserBlock

from govuk.capability_framework import is_level_not_defined
from govuk.utils import row_id_from_text

from ..constants import (
    JOB_GRADE_CHOICES,
    JOB_GRADE_LABELS,
    JOB_GRADE_ORDER,
    RELATED_ROLES_COUNT,
    SCS_GRADE_CHOICES,
    SKILL_LEVEL_CHOICES,
    SKILL_LEVEL_LABEL_TO_VALUE,
    SKILL_LEVEL_LABELS,
    SKILL_LEVEL_ORDINALS,
    SKILL_LEVEL_VALUES,
    SKILLS_AND_ROLES_BODY_RICH_TEXT_FEATURES,
)
from ..helpers import _next_unique_slug


def _normalised_skill_level(value: str | None) -> str:
    raw_level = (value or "").strip().lower()
    if raw_level in SKILL_LEVEL_VALUES:
        return raw_level

    mapped_level = SKILL_LEVEL_LABEL_TO_VALUE.get(raw_level)
    if mapped_level:
        return mapped_level
    return ""



class GovukSkill(models.Model):
    slug = models.SlugField(
        max_length=120,
        unique=True,
        blank=True,
        help_text="Optional key used for links, for example forensics.",
    )
    title = models.CharField(
        max_length=255,
        help_text="Skill name, for example Forensics.",
    )
    body = RichTextField(
        blank=True,
        features=SKILLS_AND_ROLES_BODY_RICH_TEXT_FEATURES,
        help_text="Short description of the skill.",
    )
    awareness_points = StreamField(
        [
            (
                "point",
                blocks.TextBlock(
                    required=False,
                    max_length=500,
                    rows=3,
                ),
            )
        ],
        blank=True,
        use_json_field=True,
        help_text="Optional awareness-level points.",
    )
    working_points = StreamField(
        [
            (
                "point",
                blocks.TextBlock(
                    required=False,
                    max_length=500,
                    rows=3,
                ),
            )
        ],
        blank=True,
        use_json_field=True,
        help_text="Optional working-level points.",
    )
    practitioner_points = StreamField(
        [
            (
                "point",
                blocks.TextBlock(
                    required=False,
                    max_length=500,
                    rows=3,
                ),
            )
        ],
        blank=True,
        use_json_field=True,
        help_text="Optional practitioner-level points.",
    )
    expert_points = StreamField(
        [
            (
                "point",
                blocks.TextBlock(
                    required=False,
                    max_length=500,
                    rows=3,
                ),
            )
        ],
        blank=True,
        use_json_field=True,
        help_text="Optional expert-level points.",
    )
    is_senior_civil_service = models.BooleanField(
        default=False,
        verbose_name="Senior Civil Service skill",
        help_text=(
            "Senior Civil Service skills describe leadership rather than "
            "ascending proficiency levels."
        ),
    )
    leadership_points = StreamField(
        [
            (
                "point",
                blocks.TextBlock(
                    required=False,
                    max_length=500,
                    rows=3,
                ),
            )
        ],
        blank=True,
        use_json_field=True,
        verbose_name="Examples of leadership",
        help_text="Examples of leadership using this skill, for Senior Civil Service skills.",
    )

    panels = [
        FieldPanel("slug"),
        FieldPanel("title"),
        FieldPanel("body"),
        FieldPanel("is_senior_civil_service"),
        FieldPanel("awareness_points"),
        FieldPanel("working_points"),
        FieldPanel("practitioner_points"),
        FieldPanel("expert_points"),
        FieldPanel("leadership_points"),
    ]

    class Meta:
        verbose_name = "Skill"
        verbose_name_plural = "Skills"
        ordering = ["title", "slug"]

    @staticmethod
    def _normalised_stream_points(stream_value) -> list[dict]:
        if not stream_value:
            return []

        cleaned_entries: list[dict] = []
        for block in stream_value:
            block_type = getattr(block, "block_type", None)
            block_value = getattr(block, "value", None)

            if isinstance(block, dict):
                block_type = block.get("type")
                block_value = block.get("value")
            elif isinstance(block, str):
                block_type = "point"
                block_value = block

            if block_type != "point":
                continue
            point = (block_value or "").strip()
            if point:
                cleaned_entries.append({"type": "point", "value": point})
        return cleaned_entries

    def clean(self):
        super().clean()
        self.title = (self.title or "").strip()
        slug_candidate = slugify((self.slug or self.title or "").strip())[:120]
        self.slug = _next_unique_slug(
            model_class=type(self),
            candidate=slug_candidate,
            instance_id=self.pk,
            fallback="skill",
        )
        self.awareness_points = self._normalised_stream_points(self.awareness_points)
        self.working_points = self._normalised_stream_points(self.working_points)
        self.practitioner_points = self._normalised_stream_points(
            self.practitioner_points
        )
        self.expert_points = self._normalised_stream_points(self.expert_points)

    def points_for_level(self, level: str | None) -> list[str]:
        level_key = _normalised_skill_level(level)
        if not level_key:
            return []

        points_field_map = {
            "awareness": self.awareness_points,
            "working": self.working_points,
            "practitioner": self.practitioner_points,
            "expert": self.expert_points,
        }
        stream_value = points_field_map.get(level_key)
        if stream_value is None:
            return []
        return [
            entry["value"] for entry in self._normalised_stream_points(stream_value)
        ]

    def level_description(self, level: str | None) -> dict:
        """What a page prints for this skill at one level.

        ``points`` are the ends of the "You can:" sentence the page prints
        above them; ``placeholder`` is the framework's one sentence for a level
        that has no description yet. Never both. The published exports carry
        that sentence in the level's cell, so it arrives as a single point, and
        an editor typing it into the points box produces the same thing. Read
        on from "You can:" with a bullet it looked like a broken sentence (Tim
        Young, 11 September 2026); the live service prints it bare, in
        whichever of its two wordings it was written, and so does this. The
        CSV export reads ``points_for_level`` directly and is not affected.
        """
        points = self.points_for_level(level)
        if len(points) == 1 and is_level_not_defined(points[0]):
            return {"points": [], "placeholder": points[0]}
        return {"points": points, "placeholder": None}

    def get_changelog(self) -> dict:
        """Published entries for this skill, newest first, with key dates.

        Filtered in Python rather than with .filter(), which would go back to
        the database and waste the prefetch the skills A to Z does for all 185
        skills at once.
        """
        entries = [entry for entry in self.changelog_entries.all() if entry.live]
        return {"entries": entries, **_changelog_dates(entries)}

    def get_roles_requiring_skill(self) -> list["GovukRole"]:
        """Roles that require this skill at any level, sorted by title."""
        return GovukRole.roles_by_skill_id().get(self.pk, [])

    def get_leadership_points(self) -> list[str]:
        """Examples of leadership, for Senior Civil Service skills."""
        return [
            entry["value"]
            for entry in self._normalised_stream_points(self.leadership_points)
        ]

    def get_level_rows(self) -> list[dict]:
        level_rows: list[dict] = []
        for level_key, level_label in SKILL_LEVEL_CHOICES:
            level_rows.append(
                {
                    "key": level_key,
                    "label": level_label,
                    "ordinal": SKILL_LEVEL_ORDINALS.get(level_key, ""),
                    **self.level_description(level_key),
                }
            )
        return level_rows

    def __str__(self) -> str:
        return self.title or self.slug

    def save(self, *args, **kwargs):
        self.title = (self.title or "").strip()
        slug_candidate = slugify((self.slug or self.title or "").strip())[:120]
        self.slug = _next_unique_slug(
            model_class=type(self),
            candidate=slug_candidate,
            instance_id=self.pk,
            fallback="skill",
        )
        self.awareness_points = self._normalised_stream_points(self.awareness_points)
        self.working_points = self._normalised_stream_points(self.working_points)
        self.practitioner_points = self._normalised_stream_points(
            self.practitioner_points
        )
        self.expert_points = self._normalised_stream_points(self.expert_points)
        super().save(*args, **kwargs)


class GovukRole(models.Model):
    slug = models.SlugField(
        max_length=120,
        unique=True,
        blank=True,
        help_text="Optional key used for links, for example digital-forensics-analyst.",
    )
    title = models.CharField(
        max_length=255,
        help_text="Role name, for example Digital forensics analyst.",
    )
    family = models.CharField(
        max_length=255,
        blank=True,
        default="",
        db_index=True,
        help_text="Role family used to group roles, for example Data.",
    )
    body = RichTextField(
        blank=True,
        features=SKILLS_AND_ROLES_BODY_RICH_TEXT_FEATURES,
        help_text="Optional summary for this role.",
    )
    levels = StreamField(
        [
            (
                "level",
                blocks.StructBlock(
                    [
                        (
                            "title",
                            blocks.CharBlock(
                                required=True,
                                max_length=255,
                                help_text=(
                                    "Role level name, for example "
                                    "Associate digital forensics analyst."
                                ),
                            ),
                        ),
                        (
                            "description",
                            blocks.RichTextBlock(
                                required=False,
                                features=["bold", "italic", "link", "ul", "ol"],
                            ),
                        ),
                        (
                            "grades",
                            blocks.ListBlock(
                                blocks.ChoiceBlock(
                                    required=False,
                                    choices=JOB_GRADE_CHOICES,
                                ),
                                required=False,
                                label="Indicative Civil Service job grades",
                                help_text=(
                                    "Grades this role level is most often "
                                    "performed at. Leave empty to hide the "
                                    "sentence, as management-track levels do."
                                ),
                            ),
                        ),
                        (
                            "skills",
                            blocks.ListBlock(
                                blocks.StructBlock(
                                    [
                                        (
                                            "skill",
                                            SnippetChooserBlock(
                                                "govuk.GovukSkill",
                                                required=True,
                                            ),
                                        ),
                                        (
                                            "level",
                                            blocks.ChoiceBlock(
                                                required=True,
                                                choices=SKILL_LEVEL_CHOICES,
                                            ),
                                        ),
                                    ],
                                    icon="pick",
                                    label="Skill requirement",
                                ),
                                required=False,
                                help_text=(
                                    "Add one or more skill requirements for this role level."
                                ),
                            ),
                        ),
                    ],
                    icon="user",
                    label="Role level",
                ),
            )
        ],
        blank=True,
        use_json_field=True,
        help_text="Role levels and their associated skills.",
    )
    is_senior_civil_service = models.BooleanField(
        default=False,
        verbose_name="Senior Civil Service role",
        help_text=(
            "Senior Civil Service roles have no role levels. Their skills are "
            "listed flat, with examples of leadership."
        ),
    )
    scs_grades = StreamField(
        [("grade", blocks.ChoiceBlock(required=False, choices=SCS_GRADE_CHOICES))],
        blank=True,
        use_json_field=True,
        verbose_name="Indicative Senior Civil Service grades",
        help_text="Grades this role is most often performed at.",
    )
    scs_skills = StreamField(
        [
            (
                "skill",
                SnippetChooserBlock("govuk.GovukSkill", required=False),
            )
        ],
        blank=True,
        use_json_field=True,
        verbose_name="Senior Civil Service skills",
        help_text="Skills required by this role, with no proficiency level.",
    )
    roles_that_could_lead_here = StreamField(
        [("role", SnippetChooserBlock("govuk.GovukRole", required=False))],
        blank=True,
        use_json_field=True,
        verbose_name="Roles that could lead to this role",
        help_text=(
            "Roles someone might do before this one. Curated rather than "
            "derived from shared skills, and listed in the order given."
        ),
    )

    panels = [
        FieldPanel("slug"),
        FieldPanel("title"),
        FieldPanel("family"),
        FieldPanel("body"),
        FieldPanel("levels"),
        FieldPanel("is_senior_civil_service"),
        FieldPanel("scs_grades"),
        FieldPanel("scs_skills"),
        FieldPanel("roles_that_could_lead_here"),
    ]

    class Meta:
        verbose_name = "Role"
        verbose_name_plural = "Roles"
        ordering = ["title", "slug"]

    def clean(self):
        super().clean()
        self.title = (self.title or "").strip()
        slug_candidate = slugify((self.slug or self.title or "").strip())[:120]
        self.slug = _next_unique_slug(
            model_class=type(self),
            candidate=slug_candidate,
            instance_id=self.pk,
            fallback="role",
        )

    @staticmethod
    def _skill_level_label(level: str | None) -> str:
        level_key = _normalised_skill_level(level)
        if not level_key:
            return ""
        return SKILL_LEVEL_LABELS.get(level_key, level_key.title())

    @staticmethod
    def _extract_skill_id(value) -> int | None:
        if type(value) is int and value > 0:
            return value
        if isinstance(value, str):
            parsed = row_id_from_text(value)
            if parsed is not None and parsed > 0:
                return parsed
        skill_pk = getattr(value, "pk", None)
        if type(skill_pk) is int and skill_pk > 0:
            return skill_pk
        return None

    def get_scs_skills(self) -> list[dict]:
        """Skills for a Senior Civil Service role, with leadership examples."""
        skills: list[dict] = []
        seen: set[int] = set()
        for block in self.scs_skills:
            skill = getattr(block, "value", None)
            if skill is None or getattr(skill, "pk", None) in seen:
                continue
            seen.add(skill.pk)
            skills.append(
                {"skill": skill, "leadership_points": skill.get_leadership_points()}
            )
        return skills

    def get_scs_grade_labels(self) -> list[str]:
        return self._grade_labels(self.scs_grades)

    def get_roles_that_could_lead_here(self) -> list["GovukRole"]:
        """The curated career path into this role, in the order given.

        Unlike ``get_related_roles`` this is not inferred from shared skills:
        the framework lists particular roles a person might come from, which
        is a judgement the content team makes.
        """
        roles: list[GovukRole] = []
        seen: set[int] = set()
        for block in self.roles_that_could_lead_here:
            role = getattr(block, "value", None)
            role_pk = getattr(role, "pk", None)
            if role is None or role_pk in seen or role_pk == self.pk:
                continue
            seen.add(role_pk)
            roles.append(role)
        return roles

    @classmethod
    def senior_roles_by_source_role_id(cls) -> dict[int, list["GovukRole"]]:
        """Map each role id to the Senior Civil Service roles it could lead to.

        The content team authors this the other way round, as the roles that
        could lead to a senior one, so the only way to the framework's
        "Senior Civil Service roles that X could lead to" is to turn the
        mapping around. Built in one pass, as ``roles_by_skill_id`` is.

        Read from the raw stream rather than the resolved snippets, the way
        ``get_skill_ids`` reads levels, because every role page builds this and
        resolving each senior role's chooser blocks costs a query apiece.

        The framework's own ordering is not recoverable from the reverse
        mapping, so the roles are listed by title.
        """
        from .pages import RoleRenderingMixin

        index: dict[int, list[GovukRole]] = {}
        for senior_role in cls.objects.filter(is_senior_civil_service=True):
            seen: set[int] = set()
            raw_blocks = (
                getattr(senior_role.roles_that_could_lead_here, "raw_data", None) or []
            )
            for raw_block in raw_blocks:
                if not isinstance(raw_block, dict) or raw_block.get("type") != "role":
                    continue
                source_id = RoleRenderingMixin._extract_role_id(raw_block.get("value"))
                if not source_id or source_id in seen or source_id == senior_role.pk:
                    continue
                seen.add(source_id)
                index.setdefault(source_id, []).append(senior_role)
        for roles in index.values():
            roles.sort(key=lambda role: (role.title or "").strip().lower())
        return index

    def get_skill_ids(self) -> set[int]:
        """Distinct ids of skills required at any level of this role."""
        skill_ids: set[int] = set()
        for block in self.scs_skills:
            skill = getattr(block, "value", None)
            skill_id = self._extract_skill_id(skill)
            if skill_id:
                skill_ids.add(skill_id)

        raw_levels = getattr(self.levels, "raw_data", None)
        if raw_levels:
            for raw_level in raw_levels:
                if not isinstance(raw_level, dict) or raw_level.get("type") != "level":
                    continue
                raw_skills = (raw_level.get("value") or {}).get("skills") or []
                for raw_entry in raw_skills:
                    if not isinstance(raw_entry, dict):
                        continue
                    # ListBlock items appear either as plain struct dicts or
                    # wrapped as {"type": "item", "value": {...}}.
                    entry_value = raw_entry.get("value") if "skill" not in raw_entry else raw_entry
                    if not isinstance(entry_value, dict):
                        continue
                    skill_id = self._extract_skill_id(entry_value.get("skill"))
                    if skill_id:
                        skill_ids.add(skill_id)
            return skill_ids

        for level_block in self.levels:
            if level_block.block_type != "level":
                continue
            for skill_requirement in level_block.value.get("skills") or []:
                skill_id = self._extract_skill_id(skill_requirement.get("skill"))
                if skill_id:
                    skill_ids.add(skill_id)
        return skill_ids

    def get_related_roles(self, count: int = RELATED_ROLES_COUNT) -> list[dict]:
        """Other roles sharing skills with this one, most shared skills first.

        Ordered by number of shared skills descending then title, capped at ``count``.
        """
        own_skill_ids = self.get_skill_ids()
        if not own_skill_ids:
            return []

        matches: list[tuple[GovukRole, set[int]]] = []
        for role in type(self).objects.exclude(pk=self.pk):
            shared_ids = own_skill_ids & role.get_skill_ids()
            if shared_ids:
                matches.append((role, shared_ids))

        matches.sort(
            key=lambda match: (-len(match[1]), (match[0].title or "").strip().lower())
        )
        matches = matches[:count]

        skills_by_id = GovukSkill.objects.in_bulk(
            {skill_id for _, shared_ids in matches for skill_id in shared_ids}
        )
        return [
            {
                "role": role,
                "shared_skills": sorted(
                    (
                        skills_by_id[skill_id]
                        for skill_id in shared_ids
                        if skill_id in skills_by_id
                    ),
                    key=lambda skill: (skill.title or "").strip().lower(),
                ),
            }
            for role, shared_ids in matches
        ]

    def get_changelog(self) -> dict:
        """Published entries for this role, newest first, with key dates."""
        entries = list(self.changelog_entries.filter(live=True))
        return {"entries": entries, **_changelog_dates(entries)}

    @classmethod
    def roles_by_skill_id(cls) -> dict[int, list["GovukRole"]]:
        """Map each skill id to the roles requiring it, sorted by role title.

        Built in a single pass so pages listing many skills do not inspect
        every role's StreamField repeatedly.
        """
        index: dict[int, list[GovukRole]] = {}
        for role in cls.objects.all():
            for skill_id in role.get_skill_ids():
                index.setdefault(skill_id, []).append(role)
        for roles in index.values():
            roles.sort(key=lambda role: (role.title or "").strip().lower())
        return index

    def get_levels_with_skills(self) -> list[dict]:
        role_levels: list[dict] = []
        for level_block in self.levels:
            if level_block.block_type != "level":
                continue

            level_value = level_block.value
            role_level_title = (level_value.get("title") or "").strip()
            role_level_description = level_value.get("description") or ""
            skill_rows: list[dict] = []

            for skill_requirement in level_value.get("skills") or []:
                skill = skill_requirement.get("skill")
                required_level = _normalised_skill_level(skill_requirement.get("level"))
                if skill is None or not required_level:
                    continue

                skill_rows.append(
                    {
                        "skill": skill,
                        "required_level": required_level,
                        "required_level_label": self._skill_level_label(required_level),
                        **skill.level_description(required_level),
                    }
                )

            skill_rows.sort(
                key=lambda row: (
                    (row["skill"].title or "").strip().lower(),
                    row["skill"].pk or 0,
                )
            )

            role_levels.append(
                {
                    "title": role_level_title,
                    "description": role_level_description,
                    "grades": self._grade_labels(level_value.get("grades")),
                    "skills": skill_rows,
                }
            )

        return role_levels

    @staticmethod
    def _grade_labels(raw_grades) -> list[str]:
        """Labels for a level's indicative grades, ordered by seniority."""
        values = {
            str(getattr(raw_grade, "value", raw_grade) or "").strip()
            for raw_grade in (raw_grades or [])
        }
        return [
            JOB_GRADE_LABELS[value]
            for value in sorted(
                values & JOB_GRADE_LABELS.keys(), key=JOB_GRADE_ORDER.get
            )
        ]

    def __str__(self) -> str:
        return self.title or self.slug

    def save(self, *args, **kwargs):
        self.title = (self.title or "").strip()
        slug_candidate = slugify((self.slug or self.title or "").strip())[:120]
        self.slug = _next_unique_slug(
            model_class=type(self),
            candidate=slug_candidate,
            instance_id=self.pk,
            fallback="role",
        )
        super().save(*args, **kwargs)


class GovukChangelogEntry(models.Model):
    """A dated note describing a change to the framework.

    Entries with no role or skill are site-wide and appear on the framework
    home page; entries attached to a role or skill appear in the "Updates"
    section of the relevant page.
    """

    date = models.DateField(
        db_index=True,
        help_text="Date the change was published.",
    )
    role = models.ForeignKey(
        "govuk.GovukRole",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="changelog_entries",
        help_text="Leave blank for a site-wide update.",
    )
    skill = models.ForeignKey(
        "govuk.GovukSkill",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="changelog_entries",
        help_text="Leave blank for a site-wide update.",
    )
    change_type = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Optional label, for example New role or Skills updated.",
    )
    note = RichTextField(
        features=["bold", "italic", "link", "ul", "ol"],
        help_text="What changed.",
    )
    live = models.BooleanField(
        default=True,
        verbose_name="Published",
        help_text="Unpublish to hide this entry without deleting it.",
    )

    panels = [
        FieldPanel("date"),
        FieldPanel("role"),
        FieldPanel("skill"),
        FieldPanel("change_type"),
        FieldPanel("note"),
        FieldPanel("live"),
    ]

    class Meta:
        verbose_name = "Changelog entry"
        verbose_name_plural = "Changelog entries"
        ordering = ["-date", "pk"]

    def clean(self):
        super().clean()
        if self.role_id and self.skill_id:
            raise ValidationError(
                "Choose either a role or a skill for this entry, not both."
            )

    def __str__(self) -> str:
        subject = self.role or self.skill or "Framework"
        return f"{subject} - {self.date}"


def site_wide_changelog() -> dict:
    """Changelog entries about the framework rather than one role or skill."""
    entries = list(
        GovukChangelogEntry.objects.filter(
            role__isnull=True, skill__isnull=True, live=True
        )
    )
    return {"entries": entries, **_changelog_dates(entries)}


def _changelog_dates(entries) -> dict[str, object]:
    """First and last publication dates for a set of changelog entries."""
    dates = sorted(entry.date for entry in entries if entry.date)
    if not dates:
        return {"published_date": None, "last_updated_date": None}
    return {"published_date": dates[0], "last_updated_date": dates[-1]}




