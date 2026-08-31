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
- **The chrome** — base template, GOV.UK Design System components, error pages,
  `robots.txt`, `security.txt`, the JWKS view, the OIDC login flow.
- **The admin page import and export** — the format is generic; it carries the
  framework's fields only when the flag is on.

## What belongs to the Capability Framework

Present in the codebase on every instance, reachable on none but a framework:

- **Snippets** — `GovukRole`, `GovukSkill`, `GovukChangelogEntry`.
- **Settings** — `CapabilityFrameworkWordingSettings`, 38 fields of framework
  vocabulary, registered in the admin only under the flag.
- **Page types** — `RolePage` and `SkillsAZPage`, whose `can_create_at` and
  `can_exist_under` both return `False` without the flag.
- **Downloads** — `/download/<name>.csv`, which 404s without the flag.
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

When you add something to the framework, ask the three questions the audit
asked:

1. **Can an editor on another site see it?** Panels, menu items, choosers,
   help text.
2. **Does another site's request touch it?** Queries, and especially writes —
   `for_site` and `for_request` both create rows.
3. **Does another site's data model carry it?** A field on a shared page type is
   the hardest of the three to take back, because removing it is a migration on
   a codebase somebody else is also deploying.

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
