# Guidance as code

A **guidance collection page** serves guidance that lives in a GitHub
repository, such as [The GDS Way](https://github.com/alphagov/gds-way). An
editor adds the page and gives it the repository. The pages, their titles and
order, the side navigation, review dates and owners all come from the
repository.

Turned on by `FEATURE_GUIDANCE`. Prototype: GitHub repositories only.

## Setting one up

1. In the admin, add a **Guidance collection page** wherever it should live.
   It can be the site's home page.
2. Under **Source**, enter:
   - the repository, for example `https://github.com/alphagov/gds-way`;
   - the branch;
   - optionally a directory, if the repository holds more than one set of
     guidance.
3. Publish. The first publish syncs the repository, and so does any later
   publish that changes the source settings.

A sync downloads the repository at the branch's current commit, renders every
page and stores the result, so pages are served from the database. If a sync
fails, the previous copy keeps serving and the error is shown on the page's
edit view.

A sync with no new commit does nothing, unless the site has since been
deployed with rendering changes, in which case it re-renders.

To pick up new commits:

- use **Sync now** on the page's edit view (under Sync status); or
- run `manage.py sync_guidance`, for example on a schedule. Pages whose branch
  has not moved are left alone. Use `--page-id N` to sync one page and
  `--force` to re-render anyway.

Set `GUIDANCE_GITHUB_TOKEN` to raise GitHub's unauthenticated API limit of 60
requests an hour.

## What is served where

With the page at `/the-gds-way/`:

| Address | Serves |
| --- | --- |
| `/the-gds-way/` | The guidance's index page |
| `/the-gds-way/standards/alerting/` | One page of guidance |
| `/the-gds-way/standards/alerting.html` | 301 to the page above (the source site's own addresses) |
| Paths in the source's `redirects` | 301 to where the source sends them |
| `/api/guidance/` | JSON: every synced collection on the site |
| `/api/guidance/<page-id>/` | JSON: one collection's site details, navigation and page list |
| `/api/guidance/<page-id>/docs/<path>` | JSON: one page's metadata, headings, rendered HTML and source Markdown |

The page cannot have child pages, because every path below it belongs to the
guidance.

## Formats a repository can be in

### A guidance manifest (`guidance.yml`)

This is the format in which a repository controls its own presentation. Put a
`guidance.yml` at the root of the repository, or at the root of the
configured directory:

```yaml
schema: guidance-as-code/v0
title: The GDS Way
description: Technical standards and guidance used by teams building software
banner: "**For internal use by the GDS Digital Products community.**"  # Markdown, optional; front page only
owner:
  default: "#gds-way"          # a page's owner when its frontmatter names none
  slack_workspace: gds         # turns "#channel" owners into Slack links
source_host: https://gds-way.digital.cabinet-office.gov.uk  # links to it count as internal
anchors: github                # or tech-docs, to keep a tech-docs site's heading ids
content_dir: docs              # where the Markdown is; default "."
index: index.md                # default index.md
navigation:                    # the side navigation, in order
  - title: Software development
    pages:
      - standards/programming-languages.md
      - path: manuals/code-review.md
        title: Code review       # optional; defaults to the page's title
      - path: manuals/programming-languages.md
        pages:                   # nested pages, to any depth
          - manuals/programming-languages/java.md
      - url: https://www.gov.uk/service-manual
        title: Service Manual
sections:                      # optional: pages listed under a page that is
  - page: standards/orphan.md  # not itself in the navigation
    title: Related guidance
    pages: [standards/source-code/use-github.md]
redirects:                     # old address -> page
  standards/git.html: standards/source-code/working-with-git.md
```

The sidebar is headed by the collection page's own title, linking to the
guidance's front page, and keeps the same groups on every page. A page's
nested pages show beneath it only while the reader is on that page or one of
them. Breadcrumbs follow the same tree. A link to a heading on the front page,
or on the page an item is nested under, is left out of the sidebar: it is
in-page navigation, and the page's own contents list covers it. A page listed
more than once is highlighted at its shallowest listing.

Every `.md` file under `content_dir` is a page. Files and directories starting
with `.` or `_` are skipped. A page's address is its path without `.md`, and an
`index.md` or `README.md` stands for its directory.

Page frontmatter fields, all optional:

```yaml
---
title: How to manage alerts     # default: the first "# heading"
description: One line, for listings and the API
last_reviewed_on: 2026-08-19
review_in: 6 months             # days, weeks, months or years
owner: "#alerts"                # Slack channel, email address or URL
status: withdrawn               # shows only the title and a withdrawn warning
hidden: true
---
```

The Markdown is CommonMark with tables. Raw HTML passes through the sanitiser
described below. GitHub alerts render as GOV.UK components, so a file reads
the same on GitHub as it does here:

- `> [!WARNING]`, `> [!CAUTION]` and `> [!IMPORTANT]` become warning text;
- `> [!NOTE]` and `> [!TIP]` become inset text.

Links between pages can be written as relative `.md` paths.

### A tech-docs repository, as it stands

A repository built on
[tech-docs-template](https://github.com/alphagov/tech-docs-template), which is
how The GDS Way is built, is read as it is, with no changes to it. This applies
when there is no `guidance.yml` and the repository has `config/tech-docs.yml`
and a `source/` directory.

- **Site:** comes from `config/tech-docs.yml`: `service_name`, `description`,
  `default_owner_slack`, `owner_slack_workspace`, `host` and `redirects`.
- **Banner:** the partials wrapped around every page by the layout named in
  `config.rb`. For The GDS Way that is the site banner. It is shown once,
  below the heading on the front page, rather than above every page.
- **Pages:** every `source/**/*.html.md.erb`, served at its tech-docs address
  without the `.html`.
- **Navigation:** the index page's `content_for :sidebar` list. Each top-level
  item is a group, and its nested links are the group's pages, with the
  `_nav-*` partials inlined. Another page's own sidebar (such as The GDS Way's
  "Style guides") nests under that page's entry; if the page is not in the
  navigation it is shown as a group of its own, on that page and the pages it
  lists.
- **ERB:** only these are translated, with no Ruby run:
  - `current_page.data.title`;
  - `partial '…'`;
  - `warning_text('…')`;
  - `Time.current.year`;
  - `<%%` escapes;
  - `content_for` blocks;
  - the index page's "withdrawn guidance" sitemap loop, which is replaced with
    a generated list.

  Anything else is dropped, and listed in the page's sync warnings.
- **Page details:** `layout: withdrawn`, `hide_in_navigation`, `owner_slack`,
  `last_reviewed_on` and `review_in` work as they do on the tech-docs site.
- **Heading ids** match tech-docs', so links to `#anchors` still land.

### Converting a tech-docs repository to the manifest format

```bash
manage.py export_guidance <page-id> <empty-directory>
```

This writes a synced collection out as a manifest repository: `guidance.yml`,
plus one Markdown file per page with no ERB in it. The navigation is in the
manifest, and `warning_text` calls become GitHub alerts. The addresses and
anchors stay the same, so syncing the export gives the same site. The one
exception is that only pages are exported: a link to another file in the
source repository (The GDS Way links to its `editorconfig`) needs that file
copied across by hand.

## Safety

Everything in a source repository is untrusted.

- **Downloads:** the tarball goes through the same outbound guards as content
  discovery: no private or loopback addresses, re-checked on every redirect.
  It is capped at 25 MB.
- **Archive handling:** the archive is read in memory, never extracted, and
  only its regular files are used.
- **Rendered HTML:** `nh3` strips everything off a short allow-list, including
  every `class`, `style`, script and event handler. The GOV.UK classes are
  added afterwards by this code. Inline SVG is allowed, for diagrams.
- **Images:** they load from `raw.githubusercontent.com` at the synced commit.
  Only pages with an image add that host to their Content Security Policy.

## Limits of the prototype

- Public GitHub repositories only. `GUIDANCE_GITHUB_TOKEN` is sent to the
  GitHub API, not with the tarball download.
- Guidance pages are not in site search yet.
- Syncs run in the request (Sync now, publish) or in the command. There is no
  background worker.
