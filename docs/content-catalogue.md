# Content catalogue

A **content main page** lists the standards, policy, guidance and tools for the
government technology profession, from a catalogue kept in a GitHub
repository (for example
[MattyHayward/content-catalogue-prototype](https://github.com/MattyHayward/content-catalogue-prototype)).

The page is a content page in every way: its hero, blocks, tags and settings are
edited as on any other content page. Beneath it, the site serves the
catalogue's groups and entries, built from the catalogue rather than from
pages in the admin.

Turned on by `FEATURE_GUIDANCE`. With the flag off:

- the page type cannot be created;
- the page and everything beneath it return 404;
- the sync view and command refuse to run;
- the page is left out of the pages API, search, the service navigation and
  tag listings.

## Setting one up

1. Add a **Content main page** where the catalogue should live, for example at
   `/content/`.
2. Under **Catalogue**, enter the repository as `owner/name`, and its branch.
3. Publish. The first publish syncs the catalogue, and so does any later
   publish that changes those settings.

Later changes to the catalogue are picked up in either of two ways:

- **Sync now**, on the page's edit view. It is under **Catalogue sync status**
  and needs publish permission. Tick "Re-render even if nothing has changed" to
  force a full re-render.
- `manage.py sync_content_catalogue`, for example on a schedule. Use
  `--page-id N` to sync one page and `--force` to re-render.

Set `GUIDANCE_GITHUB_TOKEN` to raise GitHub's limit of 60 API requests an
hour. Each sync makes one request for the catalogue and one for each `repo`
entry.

## What is served where

With the page at `/content/`:

| Address | Serves |
| --- | --- |
| `/content/` | The page itself, with its own blocks |
| `/content/<group>/` | A group's entries as cards, in a section for each of the group's types (headed with the type's `plural`). In each section, entries with `primary: true` come first |
| `/content/all/` | Every entry, primary first then by title, 24 to a page |
| `/content/<group>/<entry>/` | An entry with content of its own, in its type's layout |
| `/content/<group>/<entry>/<page>/` | One of that entry's pages |

The groups are the catalogue's (`groups` in `vocabularies.yml`), and each type
names its group. A type with no group (`blog`) is not listed.

An entry asked for under the wrong group 301-redirects to its own group, so
moving a type to another group keeps its entries' old addresses working.

A card shows the entry's title, its short description and "Source: <name>",
except for content held in the catalogue itself (a `hosted` entry), which is the
site's own and names no source. A primary entry's card has a darker top edge,
and comes first in its section and in "all".

An **external** entry has no page: its card links straight to where it is
published. The exception is a tool with `links`, which gets a
page of its own.

The page can still have child pages of its own. Only `all` and the group
slugs are taken by the catalogue.

## Searching and filtering

The group and `all/` pages read these query parameters. Each filter can be
repeated: an entry matching any of a filter's values passes it.

| Parameter | Matches |
| --- | --- |
| `q` | Title, the short and long descriptions, topic and profession labels, and owner. Results are ranked by relevance, and primary entries are ranked higher |
| `tag` | A topic from `tags` |
| `profession` | A profession from `professions` |
| `type` | A type from `types` |
| `scope` | A scope from `scope` |

For example, `/content/all/?q=cloud&profession=architecture`.

The group and `all/` pages have a search box and a dropdown each for topic,
profession, type and scope, side by side. Each dropdown lists only the values
in use, with how many entries choosing one would show given the other filters
already chosen. The form sends one value per filter; a link can send several. To put a search box on the main page, add a
Raw HTML block with a form whose `action` is `all/` and an input named `q`.

## Layouts

Each type names a layout in `vocabularies.yml`.

| Layout | Types | Content | The page |
| --- | --- | --- | --- |
| `requirements` | standard, principles | `guidance.yml` with `requirements:` (pages in numbered order), and a `summary` in each page's front matter | The introduction, the numbered points with their summaries, then the rest. Each point is a page with "point N of M" and previous and next links |
| `policy` | policy | `index.md` with one `##` section per part, and `version`, `effective_from`, `last_reviewed_on` and `review_in` in its front matter | The version, dates and next review, the introduction, then each section numbered in an accordion. A link to a section's anchor opens it |
| `guide` | guidance, strategy | `guidance.yml` with `navigation:` (nested with `pages:`), or a tech-docs repository such as The GDS Way | Pages with the source's side navigation and a contents list, or a single page |
| `steps` | method | `guidance.yml` with `steps:` (pages in numbered order). Other `navigation:` groups are reference pages | The numbered steps, each with "Step N of M" and previous and next links, beside a side navigation |
| `tool` | resource, product | `version` and `links` on the entry itself | What it is, its version and owner, and its links |

Standards and policies carry their title in a full-width blue band, as GOV.UK
heads an HTML publication: the kind of document (or, on a point's page, the
standard it belongs to), the title, and its version and dates.
A policy always shows all four of version, effective from, last reviewed and
next review, saying "Not stated" where its front matter has none. A method's
front page has a start button to its first step.

Every entry page ends with a details bar: owner and contact, who it is for,
scope, version, review dates, professions and topics as badges, its related
entries, and links to its source, to report a problem, and to suggest a change
to its catalogue entry.

A `<details>` element in the Markdown becomes the GOV.UK details component.
The catalogue's `content/example-markdown-guide` shows every kind of Markdown
the site renders.

## Sources

| `source.type` | Content comes from |
| --- | --- |
| `external` | Nowhere: the card links to `path`, unless the entry is a tool with `links` |
| `hosted` | `path` in the catalogue repository, read from the same download as the catalogue |
| `repo` | Another GitHub repository: `path` as `owner/name`, at `ref` if it is pinned, otherwise at the head of `branch`. A tech-docs repository is read from its root, so leave out `directory` |

## How a sync works

1. The site resolves the catalogue branch's commit. If the commit, the source
   settings and the rendering code are all unchanged, and no `repo` entry's
   commit has moved, nothing is fetched.
2. Otherwise it downloads the catalogue once and reads every entry.
3. **Entries left out:**
   - a draft, or an entry of a type with no group, is left out without a
     warning;
   - an entry with mistakes (a missing field, an unknown vocabulary value, an
     address that is not https) is left out and listed in the sync warnings.
4. **Rendering content:** each `hosted` and `repo` entry's content is
   rendered as follows:
   - Markdown is parsed as CommonMark with tables, and the resulting HTML is
     cleaned by nh3;
   - GOV.UK classes are added, heading anchors are added, and links are
     rewritten to the entry's pages.

   A `repo` entry whose commit has not moved keeps its stored pages.
5. **Swapping in:** everything is swapped in within one transaction.
6. **Failures:**
   - if the catalogue itself fails, the last good copy keeps serving and the
     error shows on the edit view;
   - if one entry fails, the rest still sync. The failed entry keeps its last
     good pages, or, with none, its card links to its source on GitHub. The
     error shows on the edit view.

`RENDER_VERSION` in `govuk/catalogue/render.py` is stored with each copy. Raise
it when a change alters what a sync stores, so that the next sync re-renders
even where no commit has moved.

## Safety

- **Downloads:** syncs make https requests to `api.github.com` and
  `codeload.github.com` only. Every request goes through the same address
  guards as content discovery, and downloads are capped at 25 MB.
- **Archive handling:** archives are read in memory, never extracted, and only
  their regular files are used.
- **Rendered HTML:** a source's HTML is cleaned to an allowlist with nh3, which
  removes every `class`, `style`, script and event handler. GOV.UK classes are
  added afterwards.
- **Images:** a page with images allows those images' https origins in its
  content security policy. No other page does.

## Not in this build

- Catalogue entries are not in site-wide search or the API.
- The card, filter and group page designs are a first version, for the
  interface design to replace.
