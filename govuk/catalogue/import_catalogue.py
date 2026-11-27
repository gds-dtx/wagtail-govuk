"""Import a catalogue repo into a content main page's tree of pages.

Reads ``catalogue.yml``, ``vocabularies.yml``, ``entries/*.yml`` and ``content/``:

- vocabularies -> type/scope/profession snippets and tags;
- groups -> ``ContentGroupPage``s under the main page;
- entries -> the entry page for their layout (external when they have no content);
- a hosted or repo entry's Markdown -> its body and sub pages, in catalogue order.

Pages are matched by slug under their parent, so a re-import finds them. Existing
entries are untouched without ``update_existing``; nothing is ever deleted.
"""

import io
import posixpath
import zipfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.text import slugify

from .adapters import build_bundle
from .bundle import GuidanceSyncError
from .catalogue import read_catalogue
from .colours import normalised
from .fetch import fetch_files, resolve_commit
from .markdown_blocks import markdown_to_blocks, split_sections
from .render import LinkResolver, render_bundle
from .repository import RepositoryRef

# Other files are listed by name only, as a GitHub download lists them.
TEXT_SUFFIXES = (".md", ".erb", ".yml", ".yaml", ".rb", ".html")
MAX_ZIP_BYTES = 50_000_000
# Link resolution needs a repository; a local folder has none.
_LOCAL = RepositoryRef("local", "catalogue")


@dataclass
class ImportResult:
    created: int = 0
    updated: int = 0
    skipped: int = 0
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        text = (
            f"{self.created} pages created, {self.updated} updated, "
            f"{self.skipped} left as they were."
        )
        if self.warnings:
            text += f" {len(self.warnings)} warning(s)."
        return text


def files_from_directory(directory) -> dict[str, bytes | None]:
    """A catalogue folder's files, by path from its root, as a download gives them."""
    directory = Path(directory)
    files: dict[str, bytes | None] = {}
    for path in sorted(directory.rglob("*")):
        relative = path.relative_to(directory)
        if not path.is_file() or relative.parts[0].startswith("."):
            continue
        name = relative.as_posix()
        files[name] = path.read_bytes() if name.endswith(TEXT_SUFFIXES) else None
    return files


def files_from_zip(data: bytes, *, max_bytes: int = MAX_ZIP_BYTES) -> dict[str, bytes | None]:
    """A zipped catalogue's files; a single top-level folder (as in GitHub's Download ZIP) is dropped."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise GuidanceSyncError("not a zip file") from exc
    members = [info for info in archive.infolist() if not info.is_dir()]
    if sum(info.file_size for info in members) > max_bytes:
        raise GuidanceSyncError(f"unzips to more than {max_bytes // 1_000_000} MB")
    names = {}
    for info in members:
        name = posixpath.normpath(info.filename.replace("\\", "/"))
        if name.startswith(("/", "../")) or name == ".." or "/../" in name:
            raise GuidanceSyncError(f"'{info.filename}' is outside the zip's folder")
        names[name] = info
    tops = {name.split("/", 1)[0] for name in names}
    prefix = f"{tops.pop()}/" if len(tops) == 1 and all("/" in n for n in names) else ""
    files: dict[str, bytes | None] = {}
    for name, info in sorted(names.items()):
        relative = name[len(prefix) :]
        if any(part.startswith(".") for part in relative.split("/")):
            continue
        files[relative] = archive.read(info) if relative.endswith(TEXT_SUFFIXES) else None
    return files


def fetch_repository(repository: RepositoryRef, *, timeout: float = 30.0):
    """``(commit, files)`` for a GitHub repo, through the guarded download."""
    commit = resolve_commit(repository, timeout=timeout)
    return commit, fetch_files(repository, commit, timeout=timeout)


def files_from_github(repository: str, *, branch: str = "", fetch=None):
    """``(files, repository)`` for a catalogue on GitHub, given as ``owner/name``."""
    try:
        ref = RepositoryRef.parse(repository, branch=branch)
    except ValueError as exc:
        raise GuidanceSyncError(str(exc)) from exc
    commit, files = (fetch or fetch_repository)(ref)
    return files, ref.at(commit)


def _date(value: str):
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _split_at_first_section(markdown: str) -> tuple[str, str]:
    """A standard's text before its first ``##``, and the rest."""
    lines = (markdown or "").splitlines(keepends=True)
    for number, line in enumerate(lines):
        if line.startswith("## "):
            return "".join(lines[:number]), "".join(lines[number:])
    return markdown or "", ""


class _Importer:
    def __init__(self, main_page, files, *, repository, fetch, publish, update_existing, user):
        self.main_page = main_page
        self.files = files
        self.repository = repository
        self.fetch = fetch or fetch_repository
        self.publish = publish
        self.update_existing = update_existing
        self.user = user
        self.result = ImportResult()
        self.entry_pages: dict[str, object] = {}
        self.related: dict[str, list[str]] = {}
        # Slugs given out this run, by parent, so two documents never share a page.
        self.slugs: dict[int, set[str]] = {}

    def _add_child(self, parent, page):
        if not self.publish:
            page.live = False
            page.has_unpublished_changes = True
        parent.add_child(instance=page)
        self.result.created += 1
        return page

    def _save(self, page, *, updated: bool = False):
        revision = page.save_revision(user=self.user, clean=False)
        if self.publish:
            revision.publish(user=self.user)
        if updated:
            self.result.updated += 1

    def _existing(self, model, parent, slug):
        """The page at ``slug`` under ``parent``, or None; one of another type is an error."""
        from wagtail.models import Page

        found = Page.objects.child_of(parent).filter(slug=slug).first()
        if found is None:
            return None
        found = found.specific
        if not isinstance(found, model):
            raise GuidanceSyncError(
                f"'{parent.title}' already has a {found._meta.verbose_name} at '{slug}', "
                f"not a {model._meta.verbose_name}; left as it is"
            )
        return found

    def vocabularies(self, vocab: dict):
        from govuk.models import (
            CatalogueProfession,
            CatalogueScope,
            CatalogueType,
            GovukTag,
        )

        for model, name in (
            (CatalogueType, "types"),
            (CatalogueScope, "scope"),
            (CatalogueProfession, "professions"),
        ):
            for order, (key, value) in enumerate(vocab.get(name, {}).items()):
                values = {
                    "label": value.get("label") or key,
                    "description": value.get("description") or "",
                    "sort_order": order,
                }
                if model is CatalogueType:
                    values["plural"] = value.get("plural") or ""
                    values["layout"] = value.get("layout") or "guide"
                    # Left as it is when absent, so an older export keeps the chosen colour.
                    colour = normalised(value.get("colour"))
                    if colour:
                        values["colour"] = colour
                row = model.objects.filter(key=key).first()
                if row is None:
                    model.objects.create(key=key, **values)
                elif self.update_existing:
                    for attr, item in values.items():
                        setattr(row, attr, item)
                    row.save()
        for key, value in vocab.get("tags", {}).items():
            if not GovukTag.objects.filter(slug=key).exists():
                GovukTag.objects.create(slug=key, name=value.get("label") or key)

    def groups(self, vocab: dict) -> dict:
        from govuk.models import ContentGroupPage

        pages = {}
        for key, group in vocab.get("groups", {}).items():
            title = group.get("label") or key
            description = group.get("description") or ""
            page = self._existing(ContentGroupPage, self.main_page, key)
            if page is None:
                page = self._add_child(
                    self.main_page,
                    ContentGroupPage(title=title, slug=key, description=description),
                )
                self._save(page)
            elif self.update_existing:
                page.title, page.description = title, description
                self._save(page, updated=True)
            else:
                self.result.skipped += 1
            pages[key] = page
        return pages

    def entry(self, record, group_page):
        from govuk import models

        layout_models = {
            "requirements": models.ContentEntryRequirementsPage,
            "policy": models.ContentEntryPolicyPage,
            "guide": models.ContentEntryGuidePage,
            "steps": models.ContentEntryStepsPage,
            "tool": models.ContentEntryToolPage,
            "generic": models.ContentEntryGenericPage,
        }
        model = layout_models.get(record.layout) if record.has_pages else None
        model = model or models.ContentEntryExternalPage

        page = self._existing(model, group_page, record.id)
        self.entry_pages[record.id] = page
        if page is not None and not self.update_existing:
            self.result.skipped += 1
            return
        created = page is None
        if created:
            page = model(title=record.title, slug=record.id)
        self._entry_fields(page, record)
        if created:
            self._add_child(group_page, page)
        page.professions.set(models.CatalogueProfession.objects.filter(key__in=record.professions))
        page.topics.set(models.GovukTag.objects.filter(slug__in=record.tags))
        if record.source.get("type") in ("hosted", "repo") and model is not models.ContentEntryToolPage:
            self._content(page, record)
        self._save(page, updated=not created)
        self.entry_pages[record.id] = page
        self.related[record.id] = record.related

    def _entry_fields(self, page, record):
        from govuk.models import CatalogueScope, CatalogueType

        owner = record.owner
        contact = owner.get("contact") or {}
        external = record.source.get("type") == "external"
        page.title = record.title
        page.short_description = record.short_description
        page.long_description = record.long_description
        page.search_description = record.short_description
        page.entry_type = CatalogueType.objects.get(key=record.type)
        page.scope = CatalogueScope.objects.get(key=record.scope)
        page.primary = record.primary
        page.withdrawn = record.status == "withdrawn"
        page.owner_organisation = owner.get("organisation", "")
        page.owner_team = owner.get("team", "")
        page.contact_email = contact.get("email", "")
        page.contact_url = contact.get("url", "")
        page.contact_slack = contact.get("slack", "")
        page.applies_to = record.applies_to
        page.source_name = record.source.get("name", "") if external else ""
        page.source_url = record.source.get("path", "") if external else ""
        page.version = record.version
        if hasattr(page, "links"):
            page.links = [{"type": "link", "value": link} for link in record.links]

    def _content_files(self, record) -> tuple[dict, RepositoryRef | None]:
        """The entry's own files, and the repository they are served from (if any)."""
        source = record.source
        if source["type"] == "hosted":
            directory = source["path"].strip("/") + "/"
            files = {
                path[len(directory) :]: data
                for path, data in self.files.items()
                if path.startswith(directory)
            }
            if self.repository is None:
                return files, None
            subdirectory = posixpath.join(self.repository.subdirectory, directory).strip("/")
            return files, RepositoryRef(
                self.repository.owner,
                self.repository.name,
                branch=self.repository.branch,
                subdirectory=subdirectory,
                commit=self.repository.commit,
            )
        try:
            repository = RepositoryRef.parse(
                source["path"],
                branch=source.get("branch", ""),
                subdirectory=source.get("directory", ""),
                pin=source.get("ref", ""),
            )
        except ValueError as exc:
            raise GuidanceSyncError(str(exc)) from exc
        commit, files = self.fetch(repository)
        return files, repository.at(commit)

    def _content(self, entry, record):
        files, repository = self._content_files(record)
        bundle = build_bundle(files)
        rendered = render_bundle(bundle, repository or _LOCAL)
        self.result.warnings.extend(f"{record.id}: {warning}" for warning in rendered.warnings)
        documents = {document.path: document for document in bundle.documents}
        resolver = LinkResolver(bundle, repository or _LOCAL)
        pages = self._pages(entry, record.layout, rendered.site, documents)

        def converters(document):
            def link_target(href):
                found = resolver.target(href, document.url_path)
                if not found:
                    # Maybe a repo file: link it on GitHub (a local folder has nowhere to point).
                    if repository is None:
                        return None
                    address = resolver.href(href, document.url_path, source_name=document.source_path)
                    return (None, address) if address != href else None
                page = entry if found["path"] == "" else pages.get(found["path"])
                if page is None:
                    return None
                fragment = found["fragment"]
                return page.pk, f"{page.get_url()}#{fragment}" if fragment else ""

            def image_src(src):
                if repository is not None:
                    return resolver.src(src, document.url_path)
                if not src.startswith(("https://", "http://", "data:")):
                    self.result.warnings.append(
                        f"{record.id}: image '{src}' is in a local folder; "
                        "import from GitHub for it to show"
                    )
                return src

            return {"link_target": link_target, "image_src": image_src}

        index = documents.get("")
        if index is not None:
            self._entry_body(entry, record.layout, index, converters(index))
        for path, page in pages.items():
            document = documents[path]
            page.title = document.title
            page.body = markdown_to_blocks(document.markdown, **converters(document))
            if hasattr(page, "summary"):
                page.summary = document.metadata.get("summary", "")
            self._save(page, updated=not page._created_now)

    def _pages(self, entry, layout, site, documents) -> dict:
        """The pages within the entry, in the catalogue's order, by document path."""
        pages: dict[str, object] = {}
        navigation = site.get("navigation") or []
        if layout in ("requirements", "steps"):
            model = self._model(
                "ContentRequirementPage" if layout == "requirements" else "ContentStepPage"
            )
            for path in site.get("sequence") or []:
                if path in documents and path:
                    pages[path] = self._sub_page(model, entry, documents[path])
            if layout == "steps":
                self._tree(entry, [i for g in navigation for i in g.get("items", [])], documents, pages)
        elif layout == "guide":
            self._tree(entry, [i for g in navigation for i in g.get("items", [])], documents, pages)
            for path, document in documents.items():
                # A page in no navigation is still on the source's site: keep it.
                if path and path not in pages and not document.metadata.get("hidden"):
                    pages[path] = self._sub_page(self._model("ContentGuidePage"), entry, document)
        else:
            # A policy or generic entry is one page.
            extra = sorted(document.source_path for path, document in documents.items() if path)
            if extra:
                self.result.warnings.append(
                    f"{entry.slug}: a {layout} entry is one page; left out {', '.join(extra)}"
                )
        return pages

    def _tree(self, parent, items, documents, pages):
        """Guide pages, nested as the source's navigation nests them."""
        model = self._model("ContentGuidePage")
        for item in items:
            path = item.get("path")
            if not path or item.get("fragment") or path not in documents:
                continue
            page = pages.get(path)
            if page is None:
                page = pages[path] = self._sub_page(model, parent, documents[path])
            # Steps and requirements cannot hold children, so nested items sit beside them.
            self._tree(page if isinstance(page, model) else parent, item.get("children", []), documents, pages)

    def _sub_page(self, model, parent, document):
        taken = self.slugs.setdefault(parent.pk, set())
        base = slugify(posixpath.basename(document.path)) or "page"
        slug, number = base, 2
        while slug in taken:
            slug, number = f"{base}-{number}", number + 1
        taken.add(slug)
        page = self._existing(model, parent, slug)
        if page is None:
            page = self._add_child(parent, model(title=document.title, slug=slug))
            page._created_now = True
        else:
            page._created_now = False
        return page

    def _entry_body(self, entry, layout, index, convert):
        if layout == "requirements":
            # Text before the first ``##`` comes before the points; the rest after.
            intro, rest = _split_at_first_section(index.markdown)
            entry.body = markdown_to_blocks(intro, **convert)
            entry.closing_body = markdown_to_blocks(rest, **convert)
        elif layout == "policy":
            intro, sections = split_sections(index.markdown, **convert)
            entry.body = intro
            entry.sections = [{"type": "section", "value": section} for section in sections]
        else:
            entry.body = markdown_to_blocks(index.markdown, **convert)
        metadata = index.metadata
        entry.version = metadata.get("version") or entry.version
        entry.effective_from = _date(metadata.get("effective_from"))
        entry.last_reviewed_on = _date(metadata.get("last_reviewed_on"))
        entry.next_review_on = _date(metadata.get("next_review_on"))

    @staticmethod
    def _model(name):
        from govuk import models

        return getattr(models, name)

    def related_entries(self):
        for entry_id, related in self.related.items():
            targets = [self.entry_pages[r] for r in related if self.entry_pages.get(r)]
            missing = [r for r in related if not self.entry_pages.get(r)]
            if missing:
                self.result.warnings.append(
                    f"{entry_id}: related {', '.join(missing)} not in the catalogue; left out"
                )
            if targets:
                page = self.entry_pages[entry_id]
                page.related_entries = [{"type": "entry", "value": target.pk} for target in targets]
                self._save(page)


def import_catalogue(
    main_page,
    files: dict[str, bytes | None],
    *,
    repository: RepositoryRef | None = None,
    fetch=None,
    publish: bool = True,
    update_existing: bool = False,
    user=None,
) -> ImportResult:
    """Import the catalogue in ``files`` into ``main_page``'s tree.

    ``repository`` (when from GitHub) serves hosted entries' images; ``fetch``
    stands in for GitHub. Each entry has its own transaction, so one failure
    is reported and the rest still import.
    """
    catalogue = read_catalogue(files)
    importer = _Importer(
        main_page,
        files,
        repository=repository,
        fetch=fetch,
        publish=publish,
        update_existing=update_existing,
        user=user,
    )
    importer.result.warnings.extend(catalogue.warnings)
    with transaction.atomic():
        importer.vocabularies(catalogue.vocabularies)
        groups = importer.groups(catalogue.vocabularies)
    for record in catalogue.entries:
        group = groups.get(record.group)
        if group is None:
            importer.result.warnings.append(f"{record.id}: group '{record.group}' is not defined")
            continue
        counts = (importer.result.created, importer.result.updated, importer.result.skipped)
        slugs = {parent: set(taken) for parent, taken in importer.slugs.items()}
        try:
            with transaction.atomic():
                importer.entry(record, group)
        except (GuidanceSyncError, ValidationError) as exc:
            # Rolled back, so restore the counts too.
            importer.result.created, importer.result.updated, importer.result.skipped = counts
            importer.slugs = slugs
            importer.entry_pages.pop(record.id, None)
            importer.related.pop(record.id, None)
            message = "; ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)
            importer.result.warnings.append(f"{record.id}: {message}")
    with transaction.atomic():
        importer.related_entries()
    return importer.result
