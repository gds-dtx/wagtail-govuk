# Platform boundaries

This codebase is a platform, not a project. One repository serves several
government sites; what makes an instance particular to a service is meant to
live in its environment variables and in the settings its editors hold in the
CMS, not in a fork.

The DDaT Capability Framework is the first service built on it, and the biggest
by some distance. This document draws the line between what every instance
gets and what belongs to that one service, so that the next service on this
codebase knows what it is inheriting and the framework's own work does not
quietly widen the shared half.

## The feature flags

Four flags, read once at startup from the environment in
`govuk/settings/base.py`:

| Flag | Environment variable | What it turns on |
| --- | --- | --- |
| `SKILLS` | `FEATURE_SKILLS` | The whole Capability Framework: roles, skills, changelog, the wording settings, the CSV downloads |
| `FEEDBACK` | `FEATURE_FEEDBACK` | The feedback form, its snippet listing and its URL |
| `ORGANISATIONS` | `FEATURE_ORGANISATIONS` | **Nothing yet.** No code reads it |
| `PEOPLE_FINDER` | `FEATURE_PEOPLE_FINDER` | **Nothing yet.** No code reads it |

The last two are declarations of intent. They were named for verticals nobody
has built, and reading the settings file will suggest a capability the codebase
does not have. Leave them or remove them, but do not assume switching one on
does anything.

`SKILLS` is misnamed for what it now covers — it began as the Skills A to Z and
grew into the whole framework. Renaming it means changing the environment
variable in `wagtail-instances` at the same time, so it has been left alone.
Read it as "the Capability Framework".

## What every instance gets

These are the platform. They carry no service's vocabulary and they are
site-scoped, so two instances configure them independently:

- **Page types** — `ContentPage`, `SectionPage`, `TagListingsPage`.
- **Site settings** — the phase banner, the footer, `CustomiseSettings` for the
  service name and header, the authenticated-redirect rules, the EdDSA signing
  keys, and content discovery with its sources and imported items.
- **Tags** — `GovukTag` and the tagged-item models on each page type.
- **Search** — `govuk/search_backend.py`, over pages, cards, tags and external
  content.
- **The pages API** — `/api/pages/`, which is public: `WagtailPages` sets
  `AllowAny`, overriding the authenticated mixin it also inherits.
- **The chrome** — base template, GOV.UK Design System components, error pages,
  `robots.txt`, `security.txt`, the JWKS view, the OIDC login flow.
- **The admin page import and export** — the format is generic; it carries the
  framework's fields only when the flag is on.

## What belongs to the Capability Framework

Present in the codebase on every instance, reachable on none but a framework:

- **Snippets** — `GovukRole`, `GovukSkill`, `GovukChangelogEntry`.
- **Settings** — `CapabilityFrameworkWordingSettings`, 38 fields of framework
  vocabulary, registered in the admin only under the flag.
- **Page types** — `RolePage` and `SkillsAZPage`, which cannot be created or
  moved without the flag (`can_create_at`, `can_exist_under`), 404 rather than
  serve if one reaches the site another way (`serve`), and are left out of every
  generic public listing (`without_framework_pages`).
- **Downloads** — `/download/<name>.csv`, which 404s without the flag, and the
  attachment component that offers it: `rewrite_csv_download_links` in
  `govuk/attachments.py`.
- **Fields on `ContentPage`** — `show_role_navigation`, `show_framework_updates`,
  `show_framework_welcome` and `framework_welcome_body`. These are the awkward
  ones: they sit on the page type every instance builds with. See below.

## The rule: the flag has to gate structure, not just visibility

The failure this document exists to prevent is subtle. It is easy to gate the
things you can see — a snippet in the admin menu, a page type in the chooser, a
URL — and to leave the framework wired into shared objects behind them. An audit
on 31 August 2026 found exactly that, in three places:

- The framework's four fields on `ContentPage` were offered to editors on every
  site, flag or no flag. An editor on another service saw three switches whose
  captions came from settings their admin does not register.
- `ContentPage.get_context` fetched the framework wording on every render.
  Wagtail's `BaseSiteSetting.for_site` is a `get_or_create` — it reads like a
  read and is a write — so every site rendering any content page created a
  Capability Framework settings row for a feature it had switched off.
- The search backend queried the role and skill tables. It held no reference to
  the feature flags at all, so search was the one route that reached the
  framework's snippets on a site with the framework off.

All three are fixed. The tests that hold them are
`govuk/tests/test_content_page_panels.py` and
`SearchWithoutTheFrameworkTests` in `govuk/tests/test_search_backend.py`.

### What the second pass found

A re-audit the same day, over the fix as well as the codebase, found three
more — including one in the fix itself. All three have the same cause, and it
is worth stating on its own because it is the part that is easy to get wrong
twice:

**The page import is the route that crosses the boundary.** It matches on slug,
applies every concrete field it finds in the payload, and creates pages for any
model it can resolve. It is generic by design, which is why it is a platform
feature rather than a framework one. It is also why a check written against
stored data is not enough: the data can arrive from a framework site.

- **A stored `True` outlived the site it was set on.** The first fix keyed the
  render off the three booleans alone. Hiding their panels does not clear the
  columns, and the export carries them, so importing a framework export
  elsewhere switched the framework on for a page whose editor had no switch to
  turn it off. The probe rendered a role navigation listing the content page
  itself. The guard now reads the flag as well as the switches.
- **`RolePage` and `SkillsAZPage` served after an import.** Wagtail checks
  `can_exist_under` when a page is created or moved in the admin, and the
  import is not the admin. The importer warns, but a warning in a deployment
  log is not the same as the page not being public. Both now 404.
- **The CSV attachment card rendered anywhere.** `page_body` runs
  `rewrite_csv_download_links` over every content page on every site, and the
  download URL is registered unconditionally — it is the view that 404s. So a
  paragraph holding only a link to `/download/roles.csv` became a file card
  whose size was measured by running the framework's CSV writers over
  `GovukRole`, pointing at a page that 404s. Left as an ordinary link it still
  404s, but it looks like the mistake it is.

These three are held by `test_a_switch_stored_by_another_site_is_not_honoured`
in `govuk/tests/test_content_page_panels.py`,
`DownloadsBelongToTheFrameworkTests` in `govuk/tests/test_csv_attachments.py`,
and a serve test each in `govuk/tests/test_role_page.py` and
`govuk/tests/test_skills_az_page.py`. Each was checked by reverting the fix and
watching it fail; the CSV one fails with the three `GovukRole` and `GovukSkill`
queries printed, which is the leak stated in the plainest form it has.

There is one thing here that is deliberately not fixed. The import still
*creates* framework pages and *applies* framework fields on a site with the
flag off; `_report_skills_feature_is_off` in `govuk/page_import_export.py` says
so plainly in the import report. Refusing the rows outright would mean an
operator moving content between a framework site and a staging copy with the
flag off silently loses it. The rows are now inert — nothing serves them and
nothing renders them — which is the behaviour worth guaranteeing.

### What the third pass found: 404 is only half an answer

The second pass made the framework's page types 404 on a site without the
framework. That stops them being read. It does not stop them being *named*, and
four surfaces on this codebase list pages generically — they take a base `Page`
queryset and hand back whatever is in the tree. Left alone they went on
advertising titles, URLs and search descriptions for pages that answer 404 to
everyone who clicks. A visitor reads that as a broken site rather than as a site
that does not have the framework, which is worse than the original leak in the
one way that matters: it is visible to the public.

The pages API is the sharpest of the four. `WagtailPages` in `govuk/api.py`
mixes in `AuthenticatedAPIViewSetMixin` and then sets
`permission_classes = [AllowAny]`, which overrides it. The endpoint is public.
Anything it lists is published to anyone who asks, and `?type=govuk.RolePage` is
the obvious way to go looking.

The fix is one helper, `without_framework_pages` in `govuk/models.py`. It takes a
page queryset and returns it minus `RolePage` and `SkillsAZPage`, or returns it
untouched when the flag is on, so the framework's own site is unaffected and
there is no second code path to keep in step. Every generic public listing goes
through it:

| Surface | Where |
| --- | --- |
| The public pages API, listing and detail | `WagtailPages.get_queryset`, `govuk/api.py` |
| Front-end search | `_build_page_results`, `govuk/search_backend.py` |
| The service navigation menu | `navigation_and_breadcrumbs`, `govuk/context_processors.py` |
| Tag listings | `TagListingsPage._page_listing_querysets`, `govuk/models.py` |

Tag listings are the exception to the shape: there the role pages come from a
whole queryset of their own rather than a filter on a shared one, so the
queryset is not built at all and there is no query to run. That turned out to
matter more than it sounds. `_available_filter_tags` reached into the list of
querysets **by position** — `page_querysets[2]` was the role pages — so
shortening the list made every tag listing page on a non-framework site a 500.
It is keyed by model now, and
`test_the_page_still_renders_with_the_role_queryset_absent` holds it. The lesson
is smaller than the boundary work and worth writing down anyway: when a flag can
remove an element, stop indexing the collection.

**Not applied to the Wagtail admin, deliberately.** An editor who has ended up
with these pages needs to see them in the explorer to delete them. Hiding them
there would leave a site with pages nobody can find and nobody can remove.

The same sweep turned up one more, away from the listings: `/role/<slug>` and
`/skill/<slug>` are the live Capability Framework's URL shapes, and
`seed_live_service_redirects` writes a couple of hundred permanent redirects
from them onto whatever pages carry those roles today. The import already
declined to seed without the flag; the management command did not, so an
operator working a shared runbook on another service could fill its redirects
admin with somebody else's URLs, each pointing at a page that now 404s — and
`--check` would then print its cutover all-clear over the top. The guard is in
`live_service_redirect_targets`, which is the rule both routes and the cutover
check reach through, so a third caller inherits it; the command says the flag is
off rather than reporting three zeros.

These are held by `PagesApiWithoutTheFrameworkTests` in
`govuk/tests/test_api.py`, `ServiceNavigationWithoutTheFrameworkTests` in
`govuk/tests/test_context_processors.py`,
`TagListingsWithoutTheFrameworkTests` in
`govuk/tests/test_tag_listings_page.py`, and
`test_a_role_page_is_not_a_search_result` in
`govuk/tests/test_search_backend.py`. Each class also asserts the flag-on
behaviour, so a future change that empties these listings on the framework's own
site fails too.

### What the fourth pass found: rows a migration writes everywhere

`0065_editor_snippet_permissions` gives the Editors and Moderators groups the
permissions they need over the snippets. Some of those snippets are the
framework's, and a migration cannot ask what the instance is for, so it writes
them everywhere. On an instance with the flag off, 6 of Editors' 20 permissions
and 10 of Moderators' 26 point at `GovukRole`, `GovukSkill`,
`GovukChangelogEntry` and `CapabilityFrameworkWordingSettings` — models that
site does not use.

This is deliberate. A migration that branches on a feature flag makes the
database depend on the environment it happened to be migrated in: the same
codebase then produces two different databases, and a later migration has no way
to tell which one it has. The rows are also inert. The models are not registered
as snippets without the flag, so nothing lists them, and the group edit screen
offers object permissions only for models that are registered — checked on a
non-framework instance, where the only snippet it names is external content.

So the residue on another service is these permission rows and the framework's
own tables, which every instance's schema carries because the models are defined
unconditionally. Neither is reachable, and both are the price of one codebase
rather than a fork. Write them down rather than gating the migration.

When you add something to the framework, ask the three questions the audit
asked, the fourth the second pass added, the fifth from the third and the sixth
from the fourth:

1. **Can an editor on another site see it?** Panels, menu items, choosers,
   help text.
2. **Does another site's request touch it?** Queries, and especially writes —
   `for_site` and `for_request` both create rows.
3. **Does another site's data model carry it?** A field on a shared page type is
   the hardest to take back, because removing it is a migration on a codebase
   somebody else is also deploying.
4. **Could the import put it there?** If the answer to question 3 is yes, then
   yes: the export carries every concrete field and the import applies them.
   Gate on the flag, not on what the row says.
5. **If it cannot be read, can it still be listed?** Refusing to serve something
   and refusing to name it are two different fixes. Anything that lists pages
   generically needs the second one.
6. **Does a migration write it?** Data migrations run on every instance and
   cannot read the flag safely. Whatever they write lands on services that do
   not want it and the flag cannot take it back, so it has to be inert by
   construction.

The fields on `ContentPage` are there because question 3 was answered late. They
stay, because the alternative is a schema change on a shared page type; they are
simply not offered where they do not belong. If a second service ever needs
`ContentPage` to be smaller than it is, the right move is a framework-only page
type, not another flag.

## One site per database

**Do not put two Wagtail Sites in one database while `FEATURE_SKILLS` is on.**

The framework's snippets have no foreign key to a site. `GovukRole`,
`GovukSkill` and `GovukChangelogEntry` are global to the database, so where two
sites share one, they share the framework's content: the role side navigation
on the second site lists the first site's roles and links to them by the first
site's hostname. This was reproduced end to end.

`GovukTag` has no site either. That is a platform model rather than a framework
one, so the same caution applies to any instance, framework or not: two sites in
one database share one tag vocabulary.

Nothing about that is hypothetical, and nothing about it is currently live —
the deployment gives each instance its own Aurora database, which is why it has
never been seen. `govuk/apps.py` logs a warning after every migrate when the
flag is on and more than one `Site` row exists, so the condition announces
itself rather than waiting to be noticed by an editor.

Fixing it properly means a site foreign key on four models and a data migration
to fill it in. Worth doing before anyone needs two sites in one database, and
not before.

## Configuring a new instance

Everything below is environment or CMS configuration; none of it is a code
change.

- Set `FEATURE_SKILLS=false` and leave the other three off.
- Set `DOMAIN`, `BASE_URL`, the database variables and the OIDC client — see
  the README's environment variable list.
- Set the service name, header and footer in `Settings` in the admin. They are
  `BaseSiteSetting`s, so they belong to the site rather than the deployment.
- Build with `ContentPage` and `SectionPage`. `TagListingsPage` gives you a tag
  index if you want one.
- Content arrives through the admin page import, not through this repository
  and not through a data migration. See
  [cutover.md](cutover.md#where-the-content-actually-comes-from).
