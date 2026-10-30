"""Write a content main page's catalogue out in the catalogue repo's format.

The inverse of ``import_catalogue``: ``catalogue.yml``, ``vocabularies.yml``,
an ``entries/<slug>.yml`` for each entry, and for each entry with pages of its
own a ``content/<slug>/`` folder holding its ``guidance.yml`` and Markdown.
What the import made comes back out as it went in, near enough to import
again. What does not survive the trip:

- every entry with content is written as hosted, whether it was imported
  from the catalogue's own folder or from a repo of its own;
- a guide's pages come back under one navigation heading, not the source's;
- a block with no Markdown form (cards, a banner) is written as its HTML,
  which the import keeps as an HTML block;
- the catalogue's ``featured`` list, which the site does not use;
- each type's group is where its entries are; a type with no entries has
  none, so it is not listed.

What is exported is what is published. An entry that never has been is
written from its latest draft, as ``status: draft``; a page within an entry
that never has been is left out.
"""

import io
import posixpath
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from .markdown_blocks import blocks_to_markdown

SCHEMA = "guidance-catalogue/v0"
MANIFEST_SCHEMA = "guidance-as-code/v0"


@dataclass
class ExportResult:
    files: dict[str, str] = field(default_factory=dict)
    entries: int = 0
    warnings: list[str] = field(default_factory=list)

    def write(self, directory) -> None:
        directory = Path(directory)
        for name, text in self.files.items():
            path = directory / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

    def zip_bytes(self) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, text in self.files.items():
                archive.writestr(name, text)
        return buffer.getvalue()


def _yaml(data) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=88)


def _document(title: str, body: str, front_matter: dict | None = None) -> str:
    matter = {"title": title, **{k: v for k, v in (front_matter or {}).items() if v}}
    return f"---\n{_yaml(matter)}---\n\n# {title}\n\n{body}".rstrip("\n") + "\n"


def _review_in(last, next_) -> str:
    """``next_review_on`` as the ``review_in`` the format records it by."""
    if not last or not next_ or next_ <= last:
        return ""
    months = (next_.year - last.year) * 12 + next_.month - last.month
    if next_.day != last.day:
        return f"{(next_ - last).days} days"
    if months % 12 or months == 12:
        return f"{months} months"
    return f"{months // 12} years"


class _Exporter:
    def __init__(self, main_page):
        self.main_page = main_page
        self.result = ExportResult()

    # -- the catalogue -----------------------------------------------------------------

    def run(self) -> ExportResult:
        from wagtail.models import Page

        from govuk.models import ContentGroupPage, entry_page_models

        groups = list(ContentGroupPage.objects.child_of(self.main_page).order_by("path"))
        entries = []
        for page in (
            Page.objects.descendant_of(self.main_page)
            .type(*entry_page_models())
            .specific()
            .order_by("path")
        ):
            if not page.live:
                page = page.get_latest_revision_as_object()
            entries.append(page)
        group_slugs = {group.path: group.slug for group in groups}
        entry_groups = {page.pk: group_slugs.get(page.path[: -page.steplen], "") for page in entries}

        self.result.files["catalogue.yml"] = self._catalogue()
        self.result.files["vocabularies.yml"] = self._vocabularies(groups, entries, entry_groups)
        entry_ids = {page.pk for page in entries}
        for page in entries:
            self.result.files[f"entries/{page.slug}.yml"] = self._entry(page, entry_ids)
            self.result.entries += 1
        return self.result

    def _catalogue(self) -> str:
        main = self.main_page
        description = main.search_description or main.title
        return "# The catalogue as a whole. Each entry is its own file in entries/.\n" + _yaml(
            {
                "schema": SCHEMA,
                "title": main.title,
                "short_description": description[:160],
                "long_description": description,
                "vocabularies": "vocabularies.yml",
                "entries": "entries/",
            }
        )

    def _vocabularies(self, groups, entries, entry_groups) -> str:
        from govuk.models import CatalogueProfession, CatalogueScope, CatalogueType

        type_groups: dict[int, Counter] = {}
        for page in entries:
            if entry_groups[page.pk]:
                type_groups.setdefault(page.entry_type_id, Counter())[entry_groups[page.pk]] += 1

        types = {}
        for entry_type in CatalogueType.objects.all():
            value = {"label": entry_type.label}
            if entry_type.plural:
                value["plural"] = entry_type.plural
            if entry_type.description:
                value["description"] = entry_type.description
            value["layout"] = entry_type.layout
            counts = type_groups.get(entry_type.pk)
            if counts:
                value["group"] = counts.most_common(1)[0][0]
                if len(counts) > 1:
                    self.result.warnings.append(
                        f"{entry_type.key} entries are in {len(counts)} groups; the format "
                        f"puts every entry of a type in one, so they are all in "
                        f"'{value['group']}' on import"
                    )
            types[entry_type.key] = value

        tags = {}
        for page in entries:
            for tag in page.topics.all():
                tags[tag.slug] = {"label": tag.name}

        def plain(model):
            out = {}
            for row in model.objects.all():
                out[row.key] = {"label": row.label}
                if row.description:
                    out[row.key]["description"] = row.description
            return out

        return _yaml(
            {
                "groups": {
                    group.slug: {"label": group.title, "description": group.description}
                    if group.description
                    else {"label": group.title}
                    for group in groups
                },
                "types": types,
                "scope": plain(CatalogueScope),
                "professions": plain(CatalogueProfession),
                "tags": dict(sorted(tags.items())),
            }
        )

    # -- an entry ------------------------------------------------------------------------

    def _entry(self, page, entry_ids) -> str:
        from govuk.models import ContentEntryExternalPage, ContentEntryToolPage

        data = {
            "id": page.slug,
            "title": page.title,
            "short_description": page.short_description,
            "long_description": page.long_description,
            "type": page.entry_type.key,
        }
        if page.primary:
            data["primary"] = True
        data["scope"] = page.scope.key
        data["tags"] = [tag.slug for tag in page.topics.all()]
        data["professions"] = [profession.key for profession in page.professions.all()]
        if page.withdrawn:
            data["status"] = "withdrawn"
        elif not page.live:
            data["status"] = "draft"
        for name in ("tags", "professions"):
            if not data[name]:
                self.result.warnings.append(f"{page.slug}: has no {name}; the import skips it")
        owner = {"organisation": page.owner_organisation}
        if page.owner_team:
            owner["team"] = page.owner_team
        contact = {
            key: value
            for key, value in (
                ("email", page.contact_email),
                ("url", page.contact_url),
                ("slack", page.contact_slack),
            )
            if value
        }
        if contact:
            owner["contact"] = contact
        data["owner"] = owner
        if page.applies_to:
            data["applies_to"] = page.applies_to

        related = []
        for block in page.related_entries or []:
            target = block.value
            if target is not None and target.pk in entry_ids and target.pk != page.pk:
                related.append(target.slug)
        if related:
            data["related"] = list(dict.fromkeys(related))

        is_tool = isinstance(page, ContentEntryToolPage)
        if is_tool:
            if page.version:
                data["version"] = page.version
            links = [
                {k: v for k, v in dict(block.value).items() if v}
                for block in page.links
            ]
            if links:
                data["links"] = links

        if isinstance(page, ContentEntryExternalPage) or (is_tool and page.source_url):
            data["source"] = {
                "type": "external",
                "name": page.source_name or urlsplit(page.source_url).netloc,
                "path": page.source_url,
            }
        else:
            directory = f"content/{page.slug}"
            data["source"] = {"type": "hosted", "name": self._site_name(), "path": directory}
            self._content(page, directory)
        return _yaml(data)

    def _site_name(self) -> str:
        site = self.main_page.get_site()
        return (site.site_name or site.hostname) if site else "tech.gov.uk"

    # -- an entry's content ----------------------------------------------------------

    def _content(self, entry, directory):
        from govuk.models import (
            ContentEntryPolicyPage,
            ContentEntryRequirementsPage,
            ContentEntryStepsPage,
            ContentEntryToolPage,
            ContentGuidePage,
            ContentRequirementPage,
            ContentStepPage,
        )

        # Every page's file, so links between them can be written as files.
        files = {entry.pk: "index.md"}
        numbered = []
        if isinstance(entry, ContentEntryRequirementsPage | ContentEntryStepsPage):
            model = ContentRequirementPage if isinstance(entry, ContentEntryRequirementsPage) else ContentStepPage
            numbered = list(model.objects.child_of(entry).live().order_by("path"))
            for page in numbered:
                files[page.pk] = f"{page.slug}.md"
        guide_pages = list(ContentGuidePage.objects.descendant_of(entry).live().order_by("path"))
        slugs_by_path = {entry.path: ""}
        for page in guide_pages:
            parent = slugs_by_path.get(page.path[: -page.steplen])
            if parent is None:
                continue  # beneath an unpublished page
            slugs_by_path[page.path] = posixpath.join(parent, page.slug)
            files[page.pk] = f"{slugs_by_path[page.path]}.md"
        pages = {page.pk: page for page in [entry, *numbered, *guide_pages]}
        urls = {pages[page_id].get_url(): name for page_id, name in files.items()}
        urls.pop(None, None)

        def converter(current):
            def link_href(attrs):
                href = attrs.get("href", "")
                target = None
                if attrs.get("linktype") == "page":
                    target_id = int(attrs.get("id") or 0)
                    if target_id in files:
                        target = files[target_id]
                    else:
                        from wagtail.models import Page

                        found = Page.objects.filter(pk=target_id).first()
                        return found.full_url if found else None
                elif href:
                    address, _, fragment = href.partition("#")
                    if address in urls:
                        target = urls[address] + (f"#{fragment}" if fragment else "")
                if target is None:
                    return None
                return posixpath.relpath(target, posixpath.dirname(current) or ".")

            return link_href

        def markdown(stream, current):
            warnings = []
            text = blocks_to_markdown(stream, link_href=converter(current), warnings=warnings)
            self.result.warnings.extend(f"{entry.slug}/{current}: {w}" for w in warnings)
            return text

        out = {}
        front = {
            "last_reviewed_on": entry.last_reviewed_on.isoformat() if entry.last_reviewed_on else "",
            "review_in": _review_in(entry.last_reviewed_on, entry.next_review_on),
        }
        if not isinstance(entry, ContentEntryToolPage):
            front = {
                "version": entry.version,
                "effective_from": entry.effective_from.isoformat() if entry.effective_from else "",
                **front,
            }
        if isinstance(entry, ContentEntryRequirementsPage):
            body = markdown(entry.body, "index.md") + "\n" + markdown(entry.closing_body, "index.md")
        elif isinstance(entry, ContentEntryPolicyPage):
            parts = [markdown(entry.body, "index.md")]
            for block in entry.sections or []:
                parts.append(f"## {block.value['title']}\n\n" + markdown(block.value["body"], "index.md"))
            body = "\n".join(parts)
        elif isinstance(entry, ContentEntryToolPage):
            self.result.warnings.append(
                f"{entry.slug}: a tool with no source address is written as hosted, with no pages"
            )
            body = entry.long_description + "\n"
        else:
            body = markdown(entry.body, "index.md")
        out["index.md"] = _document(entry.title, body, front)

        for page in numbered:
            out[files[page.pk]] = _document(
                page.title, markdown(page.body, files[page.pk]), {"summary": page.summary}
            )
        for page in guide_pages:
            if page.pk in files:
                out[files[page.pk]] = _document(page.title, markdown(page.body, files[page.pk]))

        manifest = {
            "schema": MANIFEST_SCHEMA,
            "title": entry.title,
            "owner": {"default": entry.owner_organisation},
            "anchors": "github",
            "content_dir": ".",
            "index": "index.md",
        }
        if numbered:
            key = "requirements" if isinstance(entry, ContentEntryRequirementsPage) else "steps"
            manifest[key] = [files[page.pk] for page in numbered]
        tree = self._navigation(entry, guide_pages, files)
        if tree:
            title = "Reference" if isinstance(entry, ContentEntryStepsPage) else "Pages"
            manifest["navigation"] = [{"title": title, "pages": tree}]
        out["guidance.yml"] = _yaml(manifest)

        for name, text in out.items():
            self.result.files[f"{directory}/{name}"] = text

    @staticmethod
    def _navigation(entry, guide_pages, files) -> list:
        by_parent: dict[str, list] = {}
        for page in guide_pages:
            if page.pk in files:
                by_parent.setdefault(page.path[: -page.steplen], []).append(page)

        def items(parent_path):
            out = []
            for page in by_parent.get(parent_path, []):
                children = items(page.path)
                out.append({"path": files[page.pk], "pages": children} if children else files[page.pk])
            return out

        return items(entry.path)


def export_catalogue(main_page) -> ExportResult:
    """The catalogue beneath ``main_page``, as the catalogue repo's files."""
    return _Exporter(main_page).run()
