"""Each page, role and skill as the content index holds it.

A converter per type reads the item's own fields, in the order its page shows
them, into ``Part``s of Markdown, and its details (tags, a catalogue entry's
owner and review dates, a role's family) into ``metadata``. ``None`` means the
item is not one a signed-out reader can reach, so the index leaves it out:

- a page must be live, under no view restriction, of a type whose flag is on,
  and reachable through a site;
- a role or skill must be live, with the framework on and its page (the
  framework main page, or the skills A to Z) live and public.

The rows are read as they are, which with Wagtail's drafts is the published
version: a saved draft lives in a revision until it is published.
"""

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from functools import cached_property

from django.conf import settings
from django.utils.text import slugify
from wagtail.models import Page, Site

from govuk.models import (
    BaseContentPage,
    CatalogueType,
    ContentEntryExternalPage,
    ContentEntryGenericPage,
    ContentEntryGuidePage,
    ContentEntryPolicyPage,
    ContentEntryRequirementsPage,
    ContentEntryStepsPage,
    ContentEntryToolPage,
    ContentGroupPage,
    ContentGuidePage,
    ContentRequirementPage,
    ContentStepPage,
    FrameworkMainPage,
    FrameworkSkillsPage,
    GovukRole,
    IndexedDocument,
    TagListingsPage,
    entry_page_models,
    framework_main_page,
    without_framework_pages,
    without_guidance_pages,
)

from .chunking import Chunk, Part, chunk_parts, parts_markdown
from .markdown import Links, rich_text_markdown, stream_markdown

SUB_PAGE_MODELS = (ContentRequirementPage, ContentStepPage, ContentGuidePage)


@dataclass
class DocumentData:
    kind: str
    title: str
    url_path: str
    site_id: int | None
    origin: str = IndexedDocument.Origin.SITE
    source_url: str = ""
    description: str = ""
    type_label: str = ""
    breadcrumbs: list[dict] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    parts: list[Part] = field(default_factory=list)
    last_updated: datetime | None = None

    @property
    def markdown(self) -> str:
        return parts_markdown(self.parts)

    def chunks(self) -> list[Chunk]:
        return chunk_parts(self.parts)

    def digest(self) -> str:
        payload = json.dumps(asdict(self), default=str, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def eligible_pages():
    """Every page a signed-out reader is served, by the rules above.

    ``public()`` drops a page under a view restriction on it or any page above
    it. Only the page itself must be live: Wagtail routes through a page that
    is not to the live pages beneath it.
    """
    pages = Page.objects.live().public().filter(depth__gt=1)
    return without_guidance_pages(without_framework_pages(pages))


class IndexContext:
    """What reading items looks up, looked up once for a run of them."""

    def __init__(self):
        self._links: dict[str, Links] = {}
        self._sites: dict[int, Site] = {}
        self._page_paths: dict[int, str] = {}

    def links(self, root_url: str) -> Links:
        if root_url not in self._links:
            self._links[root_url] = Links(root_url)
        return self._links[root_url]

    def site(self, site_id: int) -> Site | None:
        if site_id not in self._sites:
            self._sites[site_id] = Site.objects.select_related("root_page").filter(pk=site_id).first()
        return self._sites[site_id]

    def page_path(self, page) -> str:
        if page.pk not in self._page_paths:
            parts = page.get_url_parts()
            self._page_paths[page.pk] = parts[2] if parts else ""
        return self._page_paths[page.pk]

    @cached_property
    def framework_enabled(self) -> bool:
        return bool(settings.FEATURE_FLAGS.get("SKILLS"))

    @cached_property
    def main_page(self):
        """The framework main page roles are served under, if readers can reach it."""
        if not self.framework_enabled:
            return None
        page = framework_main_page()
        return page if page and _is_public(page) else None

    @cached_property
    def skills_page(self):
        if not self.framework_enabled:
            return None
        return FrameworkSkillsPage.objects.live().public().order_by("path").first()

    @cached_property
    def senior_roles_by_source(self) -> dict:
        return GovukRole.senior_roles_by_source_role_id()

    @cached_property
    def type_labels(self) -> dict[int, str]:
        return dict(CatalogueType.objects.values_list("pk", "label"))

    def skill_url(self, skill) -> str:
        if self.skills_page is None or not skill.slug:
            return ""
        return f"{self.skills_page.get_full_url()}#{skill.slug}"


def _is_public(page) -> bool:
    return Page.objects.filter(pk=page.pk).live().public().exists() and page.get_url_parts() is not None


# -- pages ---------------------------------------------------------------------------


def page_document(page, ctx: IndexContext) -> DocumentData | None:
    """A specific ``page`` as the index holds it, or None to leave it out."""
    if type(page) is Page:
        return None  # the bare page type: a placeholder with nothing on it
    url_parts = page.get_url_parts()
    if url_parts is None:
        return None
    site_id, root_url, page_path = url_parts
    document = DocumentData(
        kind=IndexedDocument.Kind.PAGE,
        title=page.title,
        url_path=page_path,
        site_id=site_id,
        description=page.search_description or "",
        breadcrumbs=_breadcrumbs(page, site_id, ctx),
        metadata={"page_type": page._meta.label},
        last_updated=page.last_published_at,
    )
    links = ctx.links(root_url)
    for models, read in _PAGE_READERS:
        if isinstance(page, models):
            read(page, document, links, ctx)
            break
    return document


def _breadcrumbs(page, site_id: int, ctx: IndexContext) -> list[dict]:
    """The pages above ``page``, below its site's home page."""
    site = ctx.site(site_id)
    if site is None:
        return []
    ancestors = page.get_ancestors().filter(depth__gt=site.root_page.depth)
    return [{"title": ancestor.title, "url_path": ctx.page_path(ancestor)} for ancestor in ancestors]


def _tag_names(page) -> list[str]:
    tags = getattr(page, "tags", None)
    return sorted(tag.name for tag in tags.all()) if tags is not None else []


def _content_page(page, document: DocumentData, links: Links, ctx) -> None:
    document.metadata["tags"] = _tag_names(page)
    document.parts.append(Part(rich_text_markdown(page.hero_intro, links)))
    if isinstance(page, FrameworkMainPage) and page.show_framework_welcome:
        document.parts.append(Part(stream_markdown(page.framework_welcome_body, links)))
    document.parts.append(Part(stream_markdown(page.body, links)))


def _tag_listings_page(page, document: DocumentData, links: Links, ctx) -> None:
    document.metadata["tags"] = _tag_names(page)
    document.parts.append(Part(rich_text_markdown(page.hero_intro, links)))
    document.parts.append(Part(rich_text_markdown(page.free_text, links)))


def _skills_page(page, document: DocumentData, links: Links, ctx) -> None:
    document.parts.append(Part(rich_text_markdown(page.hero_intro, links)))
    document.parts.append(Part(rich_text_markdown(page.body, links)))


def _group_page(page, document: DocumentData, links: Links, ctx) -> None:
    document.parts.append(Part(page.description or ""))
    document.parts.append(Part(stream_markdown(page.body, links)))


def _numbered_list(pages, links: Links) -> str:
    lines = []
    for number, page in enumerate(pages, start=1):
        line = f"{number}. [{page.title}]({links.page(page)})"
        lines.append(f"{line}: {page.summary}" if page.summary else line)
    return "\n".join(lines)


def _sequence(entry, model) -> list:
    """A standard's points or a method's steps, numbered as their pages are."""
    return list(model.objects.child_of(entry).live().public().order_by("path"))


def _entry_metadata(entry, links: Links, ctx: IndexContext) -> dict:
    def day(value):
        return value.isoformat() if value else ""

    related = [
        block.value.specific
        for block in entry.related_entries or []
        if block.value is not None and block.value.live
    ]
    return {
        "layout": entry.layout,
        "type": ctx.type_labels.get(entry.entry_type_id, ""),
        "scope": entry.scope.label,
        "professions": sorted(profession.label for profession in entry.professions.all()),
        "topics": sorted(tag.name for tag in entry.topics.all()),
        "primary": entry.primary,
        "withdrawn": entry.withdrawn,
        "owner_organisation": entry.owner_organisation,
        "owner_team": entry.owner_team,
        "contact_email": entry.contact_email,
        "contact_url": entry.contact_url,
        "contact_slack": entry.contact_slack,
        "applies_to": entry.applies_to,
        "source_name": entry.source_name,
        "source_url": entry.source_url,
        "version": entry.version,
        "effective_from": day(entry.effective_from),
        "last_reviewed_on": day(entry.last_reviewed_on),
        "next_review_on": day(entry.next_review_on),
        "related": [{"title": page.title, "url": links.page(page)} for page in related],
    }


def _entry_page(entry, document: DocumentData, links: Links, ctx: IndexContext) -> None:
    document.type_label = ctx.type_labels.get(entry.entry_type_id, "")
    document.description = document.description or entry.short_description
    document.source_url = entry.source_url
    if isinstance(entry, ContentEntryExternalPage):
        document.origin = IndexedDocument.Origin.EXTERNAL
    document.metadata.update(_entry_metadata(entry, links, ctx))
    parts = [Part(entry.long_description or "")]

    if isinstance(entry, ContentEntryRequirementsPage):
        parts.append(Part(stream_markdown(entry.body, links), anchors=True))
        points = _sequence(entry, ContentRequirementPage)
        if points:
            parts.append(Part(_numbered_list(points, links), heading="Points"))
        parts.append(Part(stream_markdown(entry.closing_body, links), anchors=True))
    elif isinstance(entry, ContentEntryPolicyPage):
        parts.append(Part(stream_markdown(entry.body, links), anchors=True))
        for number, block in enumerate(entry.sections or [], start=1):
            title = block.value["title"]
            parts.append(
                Part(
                    stream_markdown(block.value["body"], links),
                    heading=f"{number}. {title}",
                    anchor=slugify(title) or f"section-{number}",
                    anchors=True,
                )
            )
    elif isinstance(entry, ContentEntryStepsPage):
        parts.append(Part(stream_markdown(entry.body, links), anchors=True))
        steps = _sequence(entry, ContentStepPage)
        if steps:
            parts.append(Part(_numbered_list(steps, links), heading="Steps"))
    elif isinstance(entry, ContentEntryGuidePage):
        parts.append(Part(stream_markdown(entry.body, links), anchors=True))
    elif isinstance(entry, ContentEntryToolPage):
        lines = []
        for block in entry.links or []:
            link = block.value
            line = f"- [{link['title']}]({link['url']})"
            lines.append(f"{line}: {link['description']}" if link.get("description") else line)
        parts.append(Part("\n".join(lines)))
    elif isinstance(entry, ContentEntryGenericPage):
        # Laid out as a content page's body, whose headings get no ids.
        parts.append(Part(stream_markdown(entry.body, links)))
    document.parts.extend(parts)


def _sub_page(page, document: DocumentData, links: Links, ctx: IndexContext) -> None:
    entry = page.entry_page
    if entry is not None:
        document.type_label = ctx.type_labels.get(entry.entry_type_id, "")
        document.metadata["entry"] = {"title": entry.title, "url": links.page(entry)}
        if isinstance(page, ContentRequirementPage | ContentStepPage):
            sequence = [item.pk for item in _sequence(entry, type(page))]
            if page.pk in sequence:
                document.metadata["number"] = sequence.index(page.pk) + 1
                document.metadata["of"] = len(sequence)
    if getattr(page, "summary", ""):
        document.parts.append(Part(page.summary))
    document.parts.append(Part(stream_markdown(page.body, links), anchors=True))


def _title_only(page, document: DocumentData, links: Links, ctx) -> None:
    """A page type the index has no converter for: its title and description."""


# Most specific first: a catalogue main page is a content page too.
_PAGE_READERS = (
    (entry_page_models(), _entry_page),
    (SUB_PAGE_MODELS, _sub_page),
    (ContentGroupPage, _group_page),
    (BaseContentPage, _content_page),
    (TagListingsPage, _tag_listings_page),
    (FrameworkSkillsPage, _skills_page),
    (Page, _title_only),
)


def entry_for(page):
    """The catalogue entry a page within one belongs to, or None."""
    page = page.specific
    if isinstance(page, SUB_PAGE_MODELS):
        return page.entry_page
    return None


# -- roles and skills ----------------------------------------------------------------


def role_document(role, ctx: IndexContext) -> DocumentData | None:
    main = ctx.main_page
    if not role.live or main is None or not role.slug:
        return None
    site_id, root_url, main_path = main.get_url_parts()
    links = ctx.links(root_url)
    document = DocumentData(
        kind=IndexedDocument.Kind.ROLE,
        title=role.title,
        url_path=main_path + main.reverse_subpage("serve_role", args=[role.slug]),
        site_id=site_id,
        type_label="Role",
        breadcrumbs=[{"title": main.title, "url_path": main_path}],
        metadata={
            "family": role.family,
            "senior_civil_service": role.is_senior_civil_service,
            "skill_ids": sorted(role.get_skill_ids()),
        },
        last_updated=role.last_published_at,
    )
    parts = [Part(rich_text_markdown(role.body, links))]

    if role.is_senior_civil_service:
        grades = role.get_scs_grade_labels()
        if grades:
            parts.append(Part("Grades: " + ", ".join(grades), heading="Grades"))
        for row in role.get_scs_skills():
            skill = row["skill"]
            if not skill.live:
                continue
            text = _skill_link(skill, ctx)
            if row["leadership_points"]:
                text += "\n\n" + "\n".join(f"- {point}" for point in row["leadership_points"])
            parts.append(Part(text, heading=skill.title))

    for level in role.get_levels_with_skills():
        sections = [rich_text_markdown(level["description"], links)]
        if level["grades"]:
            sections.append("Indicative Civil Service job grades: " + ", ".join(level["grades"]))
        skill_lines = []
        for row in level["skills"]:
            skill = row["skill"]
            if not skill.live:
                continue
            skill_lines.append(f"- {_skill_link(skill, ctx)} at {row['required_level_label'].lower()} level")
            if row["placeholder"]:
                skill_lines.append(f"  - {row['placeholder']}")
            skill_lines += [f"  - {point}" for point in row["points"]]
        if skill_lines:
            sections.append("Skills:\n\n" + "\n".join(skill_lines))
        parts.append(Part("\n\n".join(section for section in sections if section), heading=level["title"]))

    lead_here = [other for other in role.get_roles_that_could_lead_here() if other.live]
    if lead_here:
        parts.append(Part(_role_list(lead_here, main, links), heading="Roles that could lead here"))
    leads_to = [other for other in ctx.senior_roles_by_source.get(role.pk, []) if other.live]
    if leads_to:
        parts.append(
            Part(_role_list(leads_to, main, links), heading="Senior Civil Service roles this could lead to")
        )
    document.parts = parts
    return document


def _skill_link(skill, ctx: IndexContext) -> str:
    url = ctx.skill_url(skill)
    return f"[{skill.title}]({url})" if url else skill.title


def _role_list(roles, main, links: Links) -> str:
    base = links.page(main)
    return "\n".join(
        f"- [{other.title}]({base}{main.reverse_subpage('serve_role', args=[other.slug])})"
        for other in roles
    )


def skill_document(skill, ctx: IndexContext) -> DocumentData | None:
    skills_page = ctx.skills_page
    if not skill.live or skills_page is None:
        return None
    url_parts = skills_page.get_url_parts()
    if url_parts is None:
        return None
    site_id, root_url, page_path = url_parts
    links = ctx.links(root_url)
    document = DocumentData(
        kind=IndexedDocument.Kind.SKILL,
        title=skill.title,
        url_path=f"{page_path}#{skill.slug}" if skill.slug else page_path,
        site_id=site_id,
        type_label="Skill",
        breadcrumbs=[{"title": skills_page.title, "url_path": page_path}],
        metadata={"senior_civil_service": skill.is_senior_civil_service},
        last_updated=skill.last_published_at,
    )
    parts = [Part(rich_text_markdown(skill.body, links))]
    for row in skill.get_level_rows():
        if row["points"]:
            points = "\n".join(f"- {point}" for point in row["points"])
            parts.append(Part(f"You can:\n\n{points}", heading=row["label"]))
        elif row["placeholder"]:
            parts.append(Part(row["placeholder"], heading=row["label"]))
    leadership = skill.get_leadership_points()
    if leadership:
        parts.append(Part("\n".join(f"- {point}" for point in leadership), heading="Leadership examples"))
    document.parts = parts
    return document
