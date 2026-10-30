# Content catalogue

The content catalogue is one place to find the standards, policies, guidance,
methods and tools that the government technology profession works to,
wherever they are published. It is a branch of the Wagtail page tree, and
editors manage it in the admin like any other page.

It is behind the `GUIDANCE` feature flag (`FEATURE_GUIDANCE`). With the flag
off:

- none of its page types can be created;
- its pages and routes return 404;
- its pages are left out of search, the pages API, the service navigation and
  tag listings;
- its snippets are not in the admin menu;
- the import and export commands and views refuse to run.

## The page tree

```
ContentMainPage                    the catalogue's root, and a content page
├── ContentGroupPage               "Standards and policies", "Guidance", …
│   ├── ContentEntryRequirementsPage   a standard or principles
│   │   └── ContentRequirementPage     one numbered point
│   ├── ContentEntryPolicyPage         a policy, in numbered sections
│   ├── ContentEntryGuidePage          guidance or a strategy
│   │   └── ContentGuidePage           a page, to any depth
│   ├── ContentEntryStepsPage          a method
│   │   ├── ContentStepPage            one numbered step
│   │   └── ContentGuidePage           a reference page
│   ├── ContentEntryToolPage           a resource or product, with its links
│   └── ContentEntryExternalPage       published elsewhere
└── ContentPage / TagListingsPage  the main page's own pages
```

- **`ContentMainPage`** is edited and rendered exactly like a content page.
  - It can sit at the root, or under a content page, a tag listing or the
    framework main page.
  - Its `all/` route lists every entry beneath it, with search and filters.
    `all` is therefore the one child slug it cannot use.
- **`ContentGroupPage`** lists its entries as cards.
  - Its cards are grouped into a section for each type, in the types' order.
  - Within each section, primary entries come first, then the rest by title.
  - It has a description and optional content blocks above the cards.
  - It has the same search and topic, profession, type and scope filters as
    `all/`.
- **Entry pages** hold what the catalogue records about an entry:
  - its descriptions, type, primary and withdrawn switches, scope, professions
    and topics;
  - its owner and contact, who it is for, and its related entries;
  - its version and review dates;
  - and its front page's content.

  An entry's type is limited to the types that use its page type's layout. Its
  search description defaults to its short description.
- **An external entry** has no content of its own. Its card, and its own
  address, go straight to its source. It is still a page so that it is
  edited, published and moderated in the same place as every other entry.
- **Numbering:** a standard's points and a method's steps are numbered by
  their order in the tree. To renumber them, move the pages.
- **Side navigation:** a guide's navigation, and a method's reference pages,
  come from the live pages beneath the entry.

Unpublishing an entry takes it off its group page and `all/`.

## Vocabularies

Types, scopes and professions are snippets under **Content catalogue** in
the admin menu. Editors may add and change them. Deleting one is left to
moderators, because every entry using it depends on it.

| Snippet | Fields |
| --- | --- |
| Type | Key, label, plural (the section heading on a group page), description, and layout: requirements, policy, guide, steps, tool or media |
| Scope | Key, label, description |
| Profession | Key, label, description |

Topics are the site's own tags (`GovukTag`).

## Content blocks

An entry's body, and each page within it, uses a content page's blocks plus
three components that guidance often needs:

- **Warning text:** the GOV.UK warning text component.
- **Details:** the GOV.UK details component, with a summary and its text.
- **Code:** a code sample, with an optional language.

The text blocks allow inline code, which a content page's text does not.

A policy's sections use the same blocks, without cards or banners. When a page
renders, its headings get GitHub-style anchors, and its h2s and h3s become its
contents list.

## Import and export

Import and export use the catalogue repo's format: `catalogue.yml`,
`vocabularies.yml`, one `entries/<id>.yml` per entry, and a `content/<id>/`
folder for each hosted entry with its `guidance.yml` and Markdown.

```bash
# From a folder, or from a public GitHub repo as owner/name
.venv/bin/python manage.py import_content_catalogue <main-page-id> <folder | owner/name> \
    [--branch main] [--draft] [--update]
.venv/bin/python manage.py export_content_catalogue <main-page-id> <new-folder>
```

You can also run both from the content main page's edit view. Its
**Catalogue import and export** panel links to an import page and a zip
download. The import page takes a zip of the catalogue folder (GitHub's
"Download ZIP" works) or a GitHub repository. Both need permission to publish
the main page.

### What the import does

1. **Vocabularies:** upserts the types, scopes and professions by key, and
   adds any tags that do not exist yet.
2. **Groups:** makes a group page for each group, matched by slug, under the
   main page.
3. **Entries:** makes each entry's page in its type's group, using the page
   type for its layout. An entry with an external source and no links becomes
   an external entry.
4. **Content:**
   - A hosted entry's Markdown is read from its `content/` folder.
   - A repo entry's Markdown is fetched from GitHub, through the guarded
     download in `govuk/catalogue/fetch.py`. Both the catalogue's own format
     and tech-docs repos (such as The GDS Way) are read.
   - The Markdown becomes blocks:
     - A standard's text before its first `##` is its introduction. The rest
       follows its points.
     - A policy is split into sections at each `##`.
     - Points and steps are made in the `requirements:` or `steps:` order.
     - A guide's pages are nested as its navigation nests them.
5. **Links:** a link between an entry's own pages becomes a page link, or the
   page's address if it is to a heading on that page. Related entries are
   linked once every entry exists.

The import matches pages by slug where they belong: a group under the main
page, an entry under its group, a page within an entry under its parent.
Running it again therefore finds what it made last time:

- Without `--update`, existing pages are left as editors have them.
- With `--update`, they are rewritten from the source. This loses editors'
  changes.
- A page that the source no longer has is never deleted.
- An existing page of another type is reported and left alone.
- Each entry is imported in its own transaction, so one that fails is reported
  and the rest still import.

`--draft` saves everything it makes or changes as drafts, to publish from the
admin.

Pages within an entry take the last part of their source path as their slug
(`topics/alerting.md` becomes `…/alerting/`), with a number added when two
would clash.

**Markdown to blocks:**

| Markdown | Block |
| --- | --- |
| Paragraphs, headings, lists, links, bold, italic, inline code | `text` |
| A table of plain text | `table` |
| A table with links, code or emphasis in it | `raw_html` (a GOV.UK table) |
| `> [!WARNING]`, `[!CAUTION]`, `[!IMPORTANT]` | `warning_text` |
| `> [!NOTE]`, `[!TIP]`, a plain quote | `inset_text` |
| Fenced or indented code | `code` |
| `<details>` | `details` |
| Images, SVG, `<sup>` and any other allowed HTML | `raw_html`, cleaned |

Some things are dropped or simplified:

- the page's first `#` heading, which is its title;
- HTML comments;
- `h5` and `h6`, which become `h4`;
- `<abbr>` and `<span>`, which are unwrapped;
- `<kbd>`, which becomes inline code.

Everything is cleaned with `nh3` first. Relative images in a catalogue
imported from GitHub are served from the repo at the imported commit. From a
local folder they cannot be, and the import warns.

### What the export writes, and what does not round-trip

The export writes what is published. An entry that has never been published is
written from its latest draft, as `status: draft`. A page within an entry that
has never been published is left out. The output passes the catalogue repo's
`scripts/validate.py`, and importing it again makes the same tree.

These do not survive the round trip:

- **Sources:** every entry with content is written as hosted, including one
  first imported from a repo of its own.
- **Groups:** each type's group is the one its entries are in. If a type's
  entries are in more than one group, the export warns. A type with no entries
  has no group, so the next import does not list it.
- **Navigation:** a guide's pages come back under one navigation heading
  ("Pages", or "Reference" for a method), not the source's headings.
- **Callouts and lists:** `[!TIP]` and plain quotes come back as `[!NOTE]`.
  Loose lists come back tight.
- **Blocks with no Markdown form:** cards and banners are written as their HTML,
  with a warning. The next import keeps that HTML as a raw HTML block.
- **The catalogue file:** `catalogue.yml`'s `featured` list, which the site
  does not use, is not written. Its descriptions come from the main page's
  title and search description.

## Code map

| Path | What it holds |
| --- | --- |
| `govuk/models/catalogue/pages.py` | The page types, `CatalogueFlagMixin`, `without_guidance_pages()` |
| `govuk/models/catalogue/vocabulary.py` | The vocabulary snippets |
| `govuk/models/catalogue/blocks.py` | The catalogue's blocks |
| `govuk/catalogue/presentation.py` | What each page's template is given |
| `govuk/catalogue/filtering.py` | Search and filters for group pages and `all/` |
| `govuk/catalogue/markdown_blocks.py` | Markdown to blocks, and back |
| `govuk/catalogue/import_catalogue.py`, `export_catalogue.py` | The import and export |
| `govuk/catalogue/catalogue.py`, `adapters.py`, `render.py`, `fetch.py` | Reading the format |
| `govuk/wagtail_hooks/catalogue.py` | The admin import and export views |
| `govuk/templates/catalogue/` | The front end |
