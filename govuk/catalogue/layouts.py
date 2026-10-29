"""Shape an entry's rendered content for its layout.

``render.render_bundle`` turns a hosted or repo entry into pages and a
navigation tree, the same for every layout. This adds what each layout needs on
top:

- **requirements**: the pages in the manifest's ``requirements:`` order,
  numbered, each with the ``summary`` from its front matter;
- **policy**: the index split at each ``##`` into numbered sections, for the
  accordion, with any text before the first kept as its introduction;
- **steps**: the manifest's ``steps:`` numbered in order (or, without one, its
  first navigation group), every other navigation group being reference pages
  outside the numbering;
- **guide**: the navigation tree as it is.

The tool layout has no pages: its page is the entry's own fields.
"""

from bs4 import BeautifulSoup, Tag

from .render import RenderedBundle


def shape(layout: str, rendered: RenderedBundle) -> tuple[dict, list[dict]]:
    """``(structure, documents)`` to store for one entry.

    ``structure`` is the entry's site-wide data (navigation, numbered
    sequence, banner, redirects); each document is a dict of a
    ``CatalogueDocument``'s fields.
    """
    structure = dict(rendered.site)
    documents = [
        {
            "path": document.path,
            "source_path": document.source_path,
            "title": document.title,
            "markdown": document.markdown,
            "html": document.html,
            "headings": document.headings,
            "metadata": document.metadata,
            "number": None,
            "intro_html": "",
            "sections": [],
        }
        for document in rendered.documents
    ]
    by_path = {document["path"]: document for document in documents}

    if layout == "steps" and not structure.get("sequence"):
        # The older form: the first navigation group is the numbered steps.
        navigation = list(structure.get("navigation") or [])
        if navigation:
            first = navigation.pop(0)
            structure["sequence"] = [
                item["path"] for item in first.get("items", []) if item.get("path")
            ]
            structure["navigation"] = navigation
    if layout in ("requirements", "steps"):
        sequence = [path for path in structure.get("sequence") or [] if path in by_path]
        structure["sequence"] = sequence
        for number, path in enumerate(sequence, start=1):
            by_path[path]["number"] = number
    else:
        structure["sequence"] = []

    if layout == "policy" and "" in by_path:
        index = by_path[""]
        index["intro_html"], index["sections"] = policy_sections(index["html"])
    return structure, documents


def policy_sections(html: str) -> tuple[str, list[dict]]:
    """``(introduction, sections)`` from a policy page, split at each ``<h2>``.

    Each section is ``{"number", "title", "anchor", "html"}``. The ``<h2>``
    itself is dropped from the section's HTML: the accordion shows it.
    """
    soup = BeautifulSoup(html, "html.parser")
    intro: list[str] = []
    sections: list[dict] = []
    for node in list(soup.contents):
        if isinstance(node, Tag) and node.name == "h2":
            sections.append(
                {
                    "number": len(sections) + 1,
                    "title": node.get_text(" ", strip=True),
                    "anchor": node.get("id") or f"section-{len(sections) + 1}",
                    "html": "",
                }
            )
            continue
        (sections[-1].setdefault("parts", []) if sections else intro).append(str(node))
    for section in sections:
        section["html"] = "".join(section.pop("parts", [])).strip()
    return "".join(intro).strip(), sections
