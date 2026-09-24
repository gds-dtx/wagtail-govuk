# Cutting a site over to Wagtail

> **The release is `main`.** The framework work merged into `main` on
> 18 September 2026 (wagtail-govuk#40), and the image built from it,
> `ghcr.io/gds-dtx/wagtail-govuk:8.0-135-e3fa8d3`, has served the full
> framework content on staging since 21 September (wagtail-instances#50).
> Production deploys a tag from that package, never a preview build. Tag
> `build-119` (branch `cf-launch-candidate-build-119`) is the earlier
> candidate and is kept only as a record; it predates the page-type reshaping
> and its export format is not the one this runbook describes.

This is the runbook for moving a service from its existing publishing platform
onto a new Wagtail instance. It is written from the DDaT Capability Framework
migration, but the shape applies to any instance: content comes across in one
export file, and three categories of state do not travel with it and have to be
recreated by hand.

Read the whole thing before starting. The steps are ordered because several of
them depend on the one before, and two of them are hard to undo once DNS has
moved.

## Where the content actually comes from

**This repository holds no content, and it is not supposed to.** It holds one
codebase serving several government sites. Roles, skills, change notes and page
copy are not in it and must not be added to it: they arrive on an instance
through the JSON export at `/admin/pages/import-export/`, and that export is the
only supported way content reaches production.

That has one consequence worth stating before anything else, because it is easy
to get backwards. A running instance and the repository are not two views of the
same thing. Content on the dev instance is a rehearsal artefact — useful for
checking that a template renders and a redirect resolves, but not the thing
being shipped. What production receives is whatever the export file holds on the
day it is taken. So content being wrong, missing or out of date on an instance is
never fixed by changing code; it is fixed by re-importing, and then re-exporting.

**Do not use a data migration to deliver content.** A few migrations do carry
DDaT-specific values — `0061_seed_roles_that_could_lead_here` is the clearest
example, filling in the four Senior Civil Service progression lists. Every one of
them is written to be a no-op on a database that does not already hold the rows
it refers to, which is exactly what a fresh production database is. They run
before the import, against an empty schema, and correctly do nothing. Relying on
one to populate production would therefore fail silently: migrations succeed, the
site comes up, and the field is simply empty. The export carries these fields
itself — `roles_that_could_lead_here` is serialised on the way out and applied on
the way in, and the importer distinguishes an export that *says nothing* about a
field from one that asks for it to be *emptied* — so the import is the mechanism
and the migrations are only a convenience for instances that already had content
when they ran.

## What travels, and what does not

The admin export at `/admin/pages/import-export/` produces a single JSON
document in the `govuk-page-import-export/v1` format. Its top-level keys are
`format` and `pages`, plus `skills`, `roles`, `changelog` and `wording` when the
framework content is included. That covers the page tree, the framework
snippets, and the change notes.

Three things are outside it:

| Not in the export | Where it lives | How it gets to production |
| --- | --- | --- |
| Redirects | `wagtail.contrib.redirects.Redirect` rows | Not needed in bulk — the framework's live URLs are served natively (see step 4); add one by hand in the CMS only for a retired or renamed page |
| Site settings | `BaseSiteSetting` subclasses in `govuk/models.py` | Re-entered in the CMS |
| Uploaded images and documents | The media volume | Re-uploaded, or copied at the storage layer |

The importer **adds and updates; it never deletes**. Importing a file that omits
a page leaves that page in place. This is deliberate — it makes a re-import safe
to repeat — but it means the import cannot be used to remove anything.

It also reads every top-level key with `.get()`, so a payload holding only
`format` and `pages` will not disturb the snippets. Do not hand-trim a payload
down to a partial `wording` or an empty `changelog`: those keys are applied when
present, and an empty one overwrites what is in the CMS.

## Before you start

- [ ] The production DNS zone exists and is delegated. `production/main.tf` uses
      a `data "aws_route53_zone"` lookup, which fails at plan time if the zone is
      not there, so nothing can be applied until the domain team has finished.
- [ ] The release image exists. Production runs the released package, not the
      preview package dev points at.
- [ ] `django_settings_module = "govuk.settings.production"` on the instance.
      wagtail-govuk#103 renamed `govuk/settings/dev.py`; nothing in the image
      sets `DJANGO_SETTINGS_MODULE`, and the wagtail-iac default is still the
      old name, so an image bump on its own fails at start-up with
      `ModuleNotFoundError: No module named 'govuk.settings.dev'`. With no
      deployment circuit breaker the new task restart-loops while the old one
      keeps serving, which looks like a slow deploy rather than a broken one.
      Not `govuk.settings.development`: that module is SQLite, `DEBUG = True`
      and a fixed secret key.
- [ ] `enable_execute_command = true` on the production instance for the cutover.
      Step 4 needs a shell on the running task. Turn it off afterwards.
- [ ] Somebody owns the old domain's redirect (step 7). The new hostname is its
      own zone and already answers, so go-live is not a DNS move: it is the old
      hostname starting to redirect. That is a change in the old service's
      hosting account, not in this repository.
- [ ] `NOINDEX = "false"` in the production task definition. It defaults to
      `True` (`govuk/settings/base.py`), and a site that launches with the
      default serves `Disallow: /` and a `noindex, nofollow` meta tag on every
      page. This is the single easiest thing to miss and the most expensive.
- [ ] `FEATURE_FEEDBACK = "false"` unless the service wants the built-in feedback
      view. When it is on, `feedback_view` shadows any `/feedback` page, which is
      where the phase banner links site-wide.

## Refreshing the content before you export

The content in the source instance was imported from the live service's exports
on the day the migration was rehearsed. The live service has kept publishing
since. Everything below happens on the **source** instance, before step 2, and
the gap it closes is real rather than theoretical.

Measured on 31 August 2026, the live service's home page links 54 role pages.
Two of them have no page on the dev instance at all:

| Live URL | On dev |
| --- | --- |
| `/role/agile-coach` | 404. Dev holds only `project-agile-coach-new-role`, which is the proposal page, not the role. |
| `/role/data-and-artificial-intelligence-ai-ethicist` | 404. Dev holds the superseded `data-ethicist`. |

Cutting over without refreshing would publish a framework missing a role, and
two live URLs would answer 404 on the first day. Neither would be noticed by any
check that only asks whether the pages that *were* migrated still work.

**Re-import from the live exports immediately before cutover**, not weeks
before:

```bash
python manage.py import_capability_framework <directory-of-live-csvs>
```

The import matches on slug and never deletes, so it is safe to run against
content editors have been working on. That safety has a cost, and the cost is
the second half of this section.

**Then restore the level order.** The CSV lists a role's levels alphabetically,
not by seniority, and the import takes them as it finds them: after a refresh,
Business architect reads "Associate, Business architect, Lead, Trainee" and
Software developer "Apprentice, Developer, Junior, ...". Measured on
10 September 2026 on a database whose order had been right before the refresh.
`import_role_grades` puts the seniority order back (and the indicative job
grades with it), reading both from the live site. Run it every time the CSV
import runs, on whichever instance it ran on:

```bash
python manage.py import_role_grades --save grades.json
```

`--save` keeps what it fetched, so the same order can be re-applied later with
`--json grades.json` once the live site is gone. Run it on the instance itself
(over `aws ecs execute-command`), where the live site's certificate chain is
trusted. From a laptop behind TLS inspection every fetch
fails with `CERTIFICATE_VERIFY_FAILED` and the command reports "Updated 0 roles
(0 reordered)" — read that line; a zero here after a refresh means the order is
still wrong. On 10 September the refreshed rehearsal database needed
"Updated 52 roles (43 reordered)".

**The refresh covers roles, skills and change notes only.** Nothing refreshes
the hand-made pages. On 10 September the live Roadmap said "the last update was
2 September 2026" and the copy on the development instance said 8 June: the
content team had edited live after the export was taken. Before exporting, read
each of the eight supporting pages against live and bring the copy across by
hand where it has moved.

### Read what the import says it did not touch

The import ends by naming everything in the CMS that the exports no longer
publish:

```
In the CMS but not in this file: 1 role and 1 skill. Nothing was deleted.
  role : data-ethicist (its page is still live)
  skill: strategic-data-planning
```

That list is not an error and it is not automatically a list of things to
delete. Three different things land in it:

- **A role or skill genuinely retired from the framework.** Its page is still
  live and still in the navigation, the search index and the skills A to Z.
  Unpublish it, and add a redirect to whatever replaced it.
- **A rename at the source.** The importer sees a new slug as an addition and
  keeps the old one, so a rename arrives as one new page and one apparently
  retired page. `data-ethicist` above is exactly this: the live service now
  publishes the same role as `data-and-artificial-intelligence-ai-ethicist`.
  Unpublish the old page and redirect it to the new one — do **not** delete it,
  because the old URL is the one in circulation.
- **Content an editor made by hand.** It was never in the exports and never will
  be. Leave it alone. The heading says "in the CMS but not in this file" rather
  than "retired" for this reason.

Work through every line. A line nobody has looked at is a page still being
served that nobody intends to serve.

### Then re-check the URLs

A new role imported by the refresh is served at its own `/role/<slug>` straight
away, so it needs no redirect. The one thing to watch is a role that was
**renamed or retired** at the source: its old URL now answers 404, so add a
redirect from the old path to the new page by hand in the CMS
(`/admin/redirects/`). `redirect_coverage.mjs` (step 6) names any live URL that
would 404 on the first day.

### A caveat on slugs

Because a role is served at its own `/role/<slug>`, a link into the live
service only resolves here when our slug matches theirs. Wagtail derives a slug
from the title, and the live service's slugs are not always what that derivation
produces — bracketed abbreviations are the usual cause. `Data and artificial
intelligence (AI) ethicist` slugifies to `data-and-artificial-intelligence-ai-ethicist`
only if the brackets are handled the same way at both ends. After a refresh that
adds roles, compare the new pages' URLs against the live service's: where they
differ, the live URL 404s here.

This is not hypothetical. On 10 September 2026 the live CSV titled the role
"Data and artificial intelligence ethicist", so the refresh created the slug
`data-and-artificial-intelligence-ethicist`; the live site publishes it at
`/role/data-and-artificial-intelligence-ai-ethicist`, and that URL answered 404
on the refreshed instance. Fix it in the CMS by editing the role's slug to match
live's, or add a redirect by hand. The list to check against is the set of
`/role/...` links on live's home page; the old `data-ethicist` role the refresh
reports as "in the CMS but not in this file" is the same role under its previous
name and should be unpublished with a redirect to the new one.

## 1. Bring up the instance

Apply the Terraform for the new instance and wait for the service to be stable.
Confirm what is actually running before doing anything else:

```bash
curl -s https://<production-host>/api/ | python3 -m json.tool
```

`meta.version` is the build tag of the image the task is serving. Check it
matches the tag you intended to deploy. A Terraform apply that succeeded against
a stale tag is indistinguishable from a successful deploy until you look here.

## 2. Export from the source instance

On the instance holding the verified content, go to
`/admin/pages/import-export/`, select the whole page tree along with the skills,
roles and changelog, and export. Keep the file — it is the record of exactly
what was migrated.

Check the file before uploading it: it should be valid JSON, its `format` should
be `govuk-page-import-export/v1`, and the first entry in `pages` should be the
home page.

## 3. Import into production

Sign in to the production admin and upload the file at
`/admin/pages/import-export/`. Read the report it prints rather than assuming
success — it lists what was created, what was updated and what was skipped.

**A file from before the framework page types changed still works.** Every
export taken from an instance running the earlier code names `govuk.ContentPage`
for the framework's pages, `govuk.RolePage` for each role and
`govuk.SkillsAZPage` for the A to Z. The importer reads such a file as one that
names the present types: the home page becomes the Framework main page, pages
with a framework switch on or ticked for the side menu become Framework content
pages, the A to Z becomes the Framework skills page, and the role pages are not
imported because the roles themselves are in the file's `roles` key and are
served from there. The report begins with one line saying exactly this; if it
does not, the file already named the present types. Because the ticks come from
migration `0069`, take the export from an instance that has run it (build 119 or
later), or three of live's five side-menu pages arrive as plain content pages.

The first page in the file is the home page, and a fresh instance ships an
empty placeholder home page. `_replace_placeholder_home_page` swaps the
placeholder for the imported page and repoints the site at it, but only while
the placeholder has no children. **Import before anyone adds a page by hand.**
If the placeholder has picked up children, the import nests the whole site a
level down and every URL gains a `/home/` prefix.

## 4. Check the redirects

This site serves the live service's own URL shapes natively, so there is no
bulk redirect step. The live service publishes roles at `/role/<slug>` and
skills as sections of its `/skills` A to Z. When the framework main page is the
site's home page, as it is for the Capability Framework, this site serves a role
at that same `/role/<slug>` address (with Django's own redirect to the slashed
form) and the skills A to Z at `/skills` — so a bookmark, a search result or a
link inside the migrated content reaches the right page without a redirect.

There is therefore nothing to seed here. Individual pages are the only exception,
and they are handled in the refresh below: a role or skill **retired or renamed**
at the source leaves its old URL with nothing to answer it, so add a redirect for
that one page by hand in the CMS (`/admin/redirects/`). Check coverage over HTTP
with `redirect_coverage.mjs` in step 6.

## 5. Re-enter the site settings

These are `BaseSiteSetting` models, held per site in the CMS, and none of them
came across in step 3. Each has to be set again under
`/admin/settings/`. Copy the values from the source instance rather than from
memory.

**Footer settings** (`FooterSettings`) — the support links, in order. For the
Capability Framework these match the live service exactly:

| Text | URL |
| --- | --- |
| Help | `https://www.gov.uk/help` |
| Cookies statement | `/cookie-statement/` |
| Contact | `https://www.gov.uk/contact` |
| Accessibility Statement | `/accessibility-statement/` |
| Terms and conditions | `https://www.gov.uk/help/terms-conditions` |
| Privacy | `/privacy/` |

**Customise settings** (`CustomiseSettings`) — the `import_capability_framework`
command sets most of these automatically on first run. Confirm they are correct
in the CMS afterwards, and fill in anything the import does not cover:

| Setting | Value |
| --- | --- |
| Service name location | Navigation |
| Service name link | `/` (the site home — the default; the logo keeps its own GOV.UK link because the name is in the navigation) |
| Sign in location | Hidden |
| Search box location | Navigation |
| Allow free-text search results | Off — the box only jumps to a matching role, skill or page as the reader types; there is no free-text results page and `/search/` is not served |
| Header logo | GOV.UK |
| Search placeholder | "Search for roles or skills" (the live service's wording, per the search box specification) |
| Show page feedback prompt | On |
| Page feedback — Follow up URL | `/feedback` |
| Show "Back to top" link | On |
| Maximum content width | Copy the value from the source instance. The live service widens its container to 1100px on large screens (CS32-3542); the platform default is 950px. |

The "Back to top" link is off by default for other sites; `import_capability_framework`
switches it on, but only on a first import. An instance that was cut over
before this setting existed will not have had it set, so tick it on by hand in
**Customise → Show "Back to top" link**.

Also set the error-page contact name and email. The address the live service
publishes, on its home page, its accessibility statement and throughout the
migrated content, is `digitaldatacapabilityframework@dsit.gov.uk`; the
`digitalanddata…` variant in the content designer's draft appears nowhere on
the live service, and dev and staging carry the live one. The page feedback
wording fields (`Intro text`, `Follow up text`) keep their defaults, which are
the live copy.

**Accounts are per instance.** `ADMIN_USER_EMAILS` in the Terraform creates
superusers only. Every editor and moderator has to sign in to the new instance
once (which creates an account with no permissions) and then be added to the
`Editors` or `Moderators` group at `/admin/users/`. Do this before handing the
site to the content team, or the first thing they meet is a CMS that lets them
in and lets them do nothing. `docs/editorial-workflow.md` describes what each
group can do.

**The sidebar's "Further resources" group is managed in "Sidebar settings".**
Under **Capability framework → Sidebar settings**, add the framework content
pages (and Skills A to Z), drag them into the order the live service uses
(Skills A to Z, Propose a change, Download, Job grades, Context and challenges,
Roadmap), and untick any you want hidden without losing its place. **The list
you write is the menu**: a framework page left off it does not appear, which is
how the six above are achieved on an instance whose editors have ticked more
pages into the navigation than the live service shows. Leave the list empty and
every framework page appears in page-tree order, so a site nobody has
configured is not left with an empty menu. These are `SidebarSettings`
(`BaseSiteSetting`) — like the other site settings they are **not** in the page
export and must be re-entered in the CMS on production.

**The `/feedback` page travels in the export** (it has since the 27 August
export), as a content page linking to the GOV.UK Forms survey. The phase banner
and the feedback prompt both point at it. CS32-3561 changes the survey to a
"report a technical issue" form on the evening of 29 September, with new copy
for that page and for the banner wording below, so take those from the ticket
rather than from the live service on cutover day.

**Phase banner settings** (`PhaseBannerSettings`) — `enabled`, the phase, and
the three pieces of wording either side of the feedback link. The phase label
is **`Beta`**: staging deliberately shows `Staging` there, so do not copy that
instance's value. The framework's banner reads "Complete our 3 minute /
feedback survey / to help us improve the framework." with the link pointing at
`/feedback`.

## 6. Verify before touching DNS

Production is still invisible at this point, which makes it the right moment to
check everything. The scripts referred to here live in the migration working
directory rather than in this repository.

```bash
# Every URL the old service publishes still resolves
node redirect_coverage.mjs                      # against the production host

# The robots directive, which is the NOINDEX check
curl -s https://<production-host>/robots.txt    # expect "Disallow:", NOT "Disallow: /"

# The build actually serving traffic
curl -s https://<production-host>/api/ | python3 -m json.tool

# The downloads match the old service's column schemas
node csv_field_diff.mjs
```

Then by hand: the home page, one role page, the Skills A to Z, search, a 404,
the cookie statement and the accessibility statement. Sign in as an editor and
confirm SSO completes and the admin loads.

A useful cross-check on content fidelity is to export the three published CSVs
from production and diff them against the ones the old service publishes:

```bash
python manage.py export_capability_framework /tmp/cutover-check
```

## 7. Redirect the old domain

Only once step 6 is clean. For the Capability Framework this step is not a DNS
move: `understand-digital-data-roles-skills.service.gov.uk` is its own Route 53
zone and has been serving the production instance since 16 September. Go-live
is `ddat-capability-framework.service.gov.uk` starting to answer every path
with a 301 to the same path on the new host, so that the legacy `/role/` and
`/skills` URLs land on the pages this site serves them at rather than on a dead
host.

That redirect lives in the old service's hosting account, in front of the
Strapi frontend, not in `wagtail-iac`: the module's CloudFront distribution
only carries `wagtail_domain` and its `www.` alias, so the new instance cannot
answer for the old name without a certificate and alias for it. Agree the
mechanism and the owner before cutover day, and lower the TTL on the old
records in advance so that the change, and any reversal, propagates quickly.

## 8. Afterwards

- [ ] Set `enable_execute_command` back to `false`.
- [ ] Confirm the old service's URLs redirect rather than 404, from a client
      that has never visited either host.
- [ ] Re-run `redirect_coverage.mjs` once more, after DNS, against the real
      domain.

## Upgrading an instance that already has content

Production starts empty and takes its content from the export, so the above is
the whole story there. The development instance is different: it already holds
the content, in the shape the code had before the framework page types were
reshaped, and migrations `0071` and `0072` convert it in place at container
start (`0071` creates the new types and moves the content, `0072` removes what
they replaced -- two migrations because Postgres will not alter a table in the
same transaction that changed its rows):

- the home page (the shallowest page with a framework switch on) becomes the
  Framework main page;
- every other page with a switch on, and every page ticked "List in the role
  side menu", becomes a Framework content page under it. The tick is what put
  three of live's five side-menu pages in the menu, so it counts;
- the skills A to Z keeps its page and its address under the new type name;
- the header layout (the two tick boxes become three dropdowns) and any hero
  colours are carried into the new settings, so the header and masthead look
  the same after the deploy as before it;
- **every role page is deleted**, with its revisions, workflow state, search
  and reference-index rows. Roles are served from their snippets at
  `/role/<slug>/` from then on. Anything an editor wrote on a role *page*
  (rather than on the role) does not survive: export first if that matters.

Redirects that pointed at a role page go with it, and none is needed in their
place: with the main page as the home page the roles are served at the live
service's own `/role/<slug>` URLs, and the skills A to Z keeps its `/skills`
address. Spot-check a couple of live URLs afterwards all the same.

## Rolling back

Before DNS moves, rollback is free: the old service is still serving and nothing
points at Wagtail.

After DNS moves, rollback means pointing DNS back. Content written in Wagtail
after cutover will not exist on the old platform, so the window in which this is
a clean operation is short. Decide in advance how long you are prepared to leave
the old service running, and say so in the change record.

A bad image is a separate problem from a bad cutover. There is currently no ECS
deployment circuit breaker and no minimum healthy percent set
(`wagtail-iac/ecs.tf`), so a task that fails to start will sit failing rather
than rolling itself back. Watch the service reach a stable state after any
deploy rather than assuming it did.
