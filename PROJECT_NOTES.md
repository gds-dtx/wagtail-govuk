# Project Notes — wagtail-govuk

> Orientation doc for a fresh context window. Read this first, then jump to the
> file paths it names. Everything here is verifiable in the tree; when in doubt,
> trust the code over this doc and update this doc.

## What it is

A **Wagtail 8.0 / Django 6.1.1** CMS themed with the **GOV.UK Design System**
(`govuk-frontend` 6.0.0). Single Django app named **`govuk`** — there is no
`home` app despite Wagtail's default template leaving `home`-named fossils
around (see "Gotchas"). Owned by `gds-dtx`, deployed as a container to GHCR.

Content is authored in Wagtail admin and served with GOV.UK-styled templates.
The site also exposes a **REST API**, **OIDC/SSO auth**, and a **content
discovery** subsystem that ingests external feeds. A major subsystem is the
**Capability Framework** — see below.

## Runtime & environment

- **Python ≥3.12** (CI uses 3.13). Prod image per Dockerfile.
- **No global `python`** on this machine — always use `.venv/bin/python`.
- Install: `pip install -e .` (deps live in `pyproject.toml`, not requirements.txt).
- Package deps (pinned in `pyproject.toml`): Django==6.1.1, wagtail==8.0,
  gunicorn, whitenoise, django-allauth (socialaccount+openid),
  djangorestframework, pyjwt[crypto], psycopg2-binary.

### Settings layout (`govuk/settings/`)

| Module | Use | DB | Notes |
|---|---|---|---|
| `base.py` | shared base, imported by all | — | env-var helpers `_bool_env`, `_parse_csv_env`; `FEATURE_FLAGS`; MIDDLEWARE; OIDC_TOKEN_AUTH |
| `development.py` | **default** for `manage.py` locally; also the dev environment | SQLite (`db.sqlite3`) | `DEBUG=True`, dummy `SECRET_KEY="abc123"`, verbose logging on |
| `production.py` | staging / production (server / container) | PostgreSQL (env vars) | requires `SECRET_KEY`, `DATABASE_*`, `BASE_URL`, `DOMAIN`, `OIDC_*` |
| `runtime.py` | picks development vs deployed | — | `runserver` defaults to development; gunicorn must set `DJANGO_SETTINGS_MODULE` explicitly |

`DJANGO_SETTINGS_MODULE` defaults to `govuk.settings.development` for local workflows.

### Common commands

```bash
.venv/bin/python manage.py check
.venv/bin/python manage.py migrate
.venv/bin/python manage.py runserver
# Tests (SQLite, local settings); the run prints the current count:
.venv/bin/python manage.py test govuk
# Narrow to one module:
.venv/bin/python manage.py test govuk.tests.test_role_page_layout
# Seed framework content from the public CSV exports:
.venv/bin/python manage.py import_capability_framework /path/to/csvs/
```

## Code map (`govuk/`)

Big files first — these are where most work lands:

- **`models.py`** (~5 300 lines) — all content types + settings models.

  **Page type hierarchy** (all subclass `Page` directly or via abstract bases):
  - `BaseContentPage` (abstract) — the ~12 shared hero/body/settings fields.
  - `FrameworkFieldsMixin` (abstract) — the 4 framework switches
    (`show_role_navigation`, `show_framework_updates`, `show_framework_welcome`,
    `framework_welcome_body`) plus the framework `get_context` logic.
  - **`ContentPage(BaseContentPage)`** — plain content, no framework options.
  - **`FrameworkMainPage(RoutablePageMixin, …, FrameworkFieldsMixin, BaseContentPage)`**
    — framework root, `max_count = 1`. Serves each `GovukRole` as a virtual
    page at `role/<slug>/` (the live service's own URL shape, so with the main
    page as the site's home page a role is served at `/role/<slug>/` and needs
    no redirect). The sidebar's role navigation and further-resources
    links are built from snippets at render time, not stored pages.
  - **`FrameworkContentPage(FrameworkFieldsMixin, BaseContentPage)`** — framework
    content; can only be created under `FrameworkMainPage`; always shows the role
    navigation (forced in `get_context`).
  - **`FrameworkSkillsPage(Page)`** — skills A–Z index, `max_count = 1`.
  - `TagListingsPage`, `SectionPage` — general-purpose; no framework fields in
    their panels.

  Snippets / other models: `GovukSkill`, `GovukRole` (slug, title, family,
  levels, SCS fields, progression), `GovukTag`, `ExternalContentItem`,
  `ContentDiscoverySource`, `Feedback`, `EdDSAKeyPair`, `PageUsefulnessVote`
  (one row per "Is this page useful?" yes/no answer — answer, path, site,
  created_at; no PII).

  Site settings (`BaseSiteSetting`): `CustomiseSettings` (order 1 — leads the
  Settings menu), `FooterSettings` (order 2), `PhaseBannerSettings` (order 3),
  `ErrorPagesSettings` (order 4). `CustomiseSettings` holds **location dropdowns** for `service_name_location`,
  `sign_in_location` (header / navigation / hidden — "hidden" still shows Sign Out
  for authenticated users), `search_location`, and `header_logo`.
  `service_name_link` (default `/`, grouped with `service_name_location` under a
  "Service name" panel) sets where the service name links: in the header-bar
  placement the logo and name are one link to it, in the navigation placement
  only the name links to it while the logo keeps its own `https://www.gov.uk`
  link (see `base.html` header block).
  `enable_search_results_page` (default on) is the free-text search switch: turn
  it off and the header box keeps its jump-to-a-role/skill/page autocomplete but
  submits nothing (no `<form>`, so Enter is inert and the magnifier is a
  decorative in-input icon) and `search_view` 404s. The
  `import_capability_framework` command sets it off on first import.
  `show_page_feedback_prompt` and `show_back_to_top` (both default off) toggle,
  respectively, the per-page feedback prompt and a "Back to top" link that
  appears bottom-left once the reader scrolls past the first screen. Hero colour
  fields were removed; migration 0072 carries a site's colours into `extra_css`
  as the CSS they produced. `content_max_width` (optional px) feeds
  `render_custom_css`, which sets `--govuk-content-width` and shifts main.css's
  1030px centring breakpoint to width + 80. `render_custom_css` (served at
  `/gen/custom.css`, gated by `has_custom_css`) also passes through `extra_css`.
  `ErrorPagesSettings` (order 4) makes the **404 / 500 pages editor-editable**:
  per-page heading + `RichTextField` body (each blank = the Design System's
  default wording), plus the shared **Error contact** fields
  (`error_contact_link_text`, `error_contact_email`, `error_contact_about` — the
  optional contact sentence) that used to live on `CustomiseSettings`. Exposed to
  templates as `error_pages_settings` by
  `context_processors.navigation_and_breadcrumbs`.
  `MaintenanceModeSettings` (order 5, "Maintenance mode") holds the site-close
  switch and the 503 page's heading + body (moved here from `ErrorPagesSettings`
  by migration 0079). Its `enabled` toggle is **planned maintenance**: readers
  meet the 503 page while signed-in staff are let through. The `MAINTENANCE_MODE`
  env var remains as an **emergency override** — a hard close for everyone but
  the exempt paths (health check, admin), for when the admin/DB can't be relied
  on. `MaintenanceModeMiddleware` reads both; it now runs after the auth
  middleware so it can see `request.user`.
  `CapabilityFrameworkWordingSettings` and
  `SidebarSettings` (both BaseSiteSetting, gated by `FEATURE_FLAGS["SKILLS"]`)
  live in the Capability Framework admin group, not Settings. `SidebarSettings`
  (ClusterableModel + `SidebarNavigationItem` Orderable rows) is the editor's
  control of the sidebar "Further resources" list — which framework pages show,
  in what order; an unlisted framework child is shown by default, appended after.

  **Framework nav helpers** (module-level functions near the bottom of the file):
  `framework_main_page()`, `role_route_url()`, `role_navigation_groups()`,
  `further_resources_group()`, `framework_home_link()`, `without_framework_pages()`.

- **`wagtail_hooks.py`** (~1 400 lines) — Draftail rich-text customisations and
  admin hooks. `CapabilityFrameworkViewSetGroup` groups Skills, Roles and Changelog
  snippets plus the Capability Framework Wording setting under one "Capability
  framework" admin menu item. The wording setting is still registered via
  `register_setting` (for its edit view/permissions) but removed from the Settings
  submenu via `construct_settings_menu`.

- **`search_backend.py`** — page search plus dedicated `_build_skill_results` and
  `_build_role_results` (both return a `result_type` badge — "Skill"/"Role").
  Skill results have no `search_description` (title + badge is enough).
  `views.search_suggest_view` (`/search/suggest/?q=`) regroups the same results
  as `{text, link, type}` JSON, roles then skills then pages, for the header
  box's accessible-autocomplete (`setSiteSearchAutocomplete` in `main.js`;
  library vendored as `accessible-autocomplete-3.0.1.min.js`).

- **`views.py`** (~500 lines) — custom views (search, profile, custom CSS,
  framework CSV downloads, etc.).

Subsystem modules (smaller, single-purpose):

- **Auth / SSO:** `oidc.py`, `authentication.py`, `adapters.py` (allauth),
  `jwt_tokens.py`, `middleware.py` (`AdminOIDCLoginMiddleware`,
  `AuthenticatedUserRedirectMiddleware`). JWKS/JWT via PyJWT (RS256).
- **API:** `api.py` (DRF + Wagtail API v2 router), served under `/api/`.
  JSON-only — the DRF browsable UI is disabled (`base.py`).
- **Admin reports:** `reports.py` — `PageUsefulnessReportView` (Wagtail
  `ReportView`) reads `PageUsefulnessVote` rows and shows yes/no counts per page
  path (pooled across sites), with a date-range filter and CSV/XLSX export.
  Registered in `wagtail_hooks.py` (its URLs under `register_admin_urls`, its
  menu item via `register_reports_menu_item` at `order=100` so it sits at the
  top of the admin Reports menu). Votes are written by `page_feedback_view`
  (`views.py`) — the "Is this page useful?" answer is now stored as a row *and*
  logged. The `prune_page_usefulness_votes --days N` command bounds the table.
- **Framework CSV downloads:** `capability_framework_csv.py` writes the three
  published CSVs (skills, roles, …) to any file-like object; `views.framework_csv_view`
  serves each at `/download/<name>.csv`, generated at request time (not stored);
  `attachments.py` renders them as GOV.UK attachment components on the download page.
  `capability_framework.py` converts framework CSV content ↔ Wagtail rich text
  (used by the import command and CSV round-tripping).
- **Content discovery:** `content_discovery.py`, `content_discovery_import.py`
  (CSV upsert by `(site_id, url)`), management commands
  `sync_external_content.py` and `import_content_discovery_sources.py`. Ingests
  external feeds into `ExternalContentItem`.
- **Import/export:** `page_import_export.py` (admin views for page import/export);
  management commands `import_capability_framework.py` (seeds skills, roles,
  `FrameworkMainPage` home, `FrameworkSkillsPage`, and on first run sets
  customise settings and home-page switches) and `export_capability_framework.py`
  (writes the framework back out to CSV).
- **Misc:** `context_processors.py`, `middleware.py` (security headers, CSP,
  CORS), `logging_utils.py`, `utils.py`, `forms.py`, `view_robots.py`,
  `view_securitytxt.py`, `templatetags/{govuk_admin,govuk_filters}.py`.

### Middleware order (base.py)

Custom middleware: `IncomingRequestDebugLoggingMiddleware`,
`SecurityHeadersMiddleware`, `AdminCSPMiddleware`, `CorsMiddleware`,
`AdminOIDCLoginMiddleware`, `AuthenticatedUserRedirectMiddleware` — interleaved
with Django/whitenoise/allauth/wagtail-redirects middleware.

### URLs (`govuk/urls.py`)

OIDC login routes → `/login/`, `/accounts/…`; Wagtail admin → `/admin/`;
`/django-admin/`; API → `/api/` (+ `/api/health/`); `.well-known/jwks.json` &
`security.txt`; `/gen/custom.css`; `/search/`; `/download/<name>.csv`
(framework CSVs, before the Wagtail catch-all); `/robots.txt`;
`/feedback` (flag-gated); `/page-feedback/` (records an "Is this page useful?"
answer); Wagtail page serving at `/`. Admin-only: the Page usefulness report at
`/admin/reports/page-usefulness/`.

## Capability Framework subsystem

The framework is a gated subsystem (`FEATURE_FLAGS["SKILLS"]`). Key concepts:

- **Roles are snippets, not pages.** `GovukRole` (slug, title, family, levels,
  SCS fields) is the data model. Each role is served at
  `/<FrameworkMainPage-url>/role/<slug>/` by `FrameworkMainPage.serve_role` —
  a `RoutablePageMixin` route. There is no `RolePage` model.
- **Roles and Skills carry Wagtail workflow.** `GovukRole` and `GovukSkill` are
  `WorkflowMixin`/`DraftStateMixin`/`LockableMixin`/`RevisionMixin` snippets
  (roles are also `PreviewableMixin`, previewing through `role_page.html`), so an
  edit is a draft until published. The default "Moderators approval" workflow is
  bound to both content types by migration `0081`. **Every public read filters
  `live=True`** (serve_role, skills A-Z, `role_navigation`, search, CSV
  downloads, and the `roles_by_skill_id`/`senior_roles_by_source_role_id`/
  `get_related_roles` index builders); admin, import and management-command reads
  are left unfiltered. Rows created directly (as the CSV import does) default to
  `live=True`, so an import still produces a published, served site.
- **Sidebar nav** is fully data-driven. `role_navigation_groups()` enumerates all
  live `GovukRole` snippets grouped by `family`. The "Further resources" group
  lists live children of the `FrameworkMainPage` (i.e. `FrameworkContentPage`s
  and the `FrameworkSkillsPage`); which show and in what order is set in
  `SidebarSettings` (Capability framework → Sidebar settings), with unlisted
  children shown by default, appended in tree order. The sidebar's top link reads
  "Home" and points at the `FrameworkMainPage`, highlighted when it is current.
- **FrameworkContentPage** always shows the role navigation (forced in
  `get_context` regardless of stored field values) and highlights itself in the
  "Further resources" group. It offers no welcome layout, no updates block, and no
  hero-styling toggles in its settings panels.
- **Admin group:** Skills, Roles, Changelog and Capability Framework Wording
  snippets/settings are under one "Capability framework" admin menu item (see
  `CapabilityFrameworkViewSetGroup` in `wagtail_hooks.py`).
- **Import command:** `import_capability_framework <dir>` seeds roles, skills,
  the `FrameworkMainPage` (replacing the Wagtail placeholder home), a
  `FrameworkSkillsPage`, and on first run writes customise settings (service name
  in navigation, search in navigation, sign-in hidden, GOV.UK logo) and sets the
  three framework switches on the home page. Re-runnable safely.
- **Live-service URLs:** no redirect seeding — this site serves the old
  service's shapes natively (a role at `/role/<slug>` with the framework main
  page as home; the skills A to Z at `/skills`). Only a role/skill retired or
  renamed at the source needs a manual redirect in the CMS.
- **Role grades (SCS):** not in the public CSV; need `import_role_grades` separately.

## Feature flags (env-driven, `base.py`)

`FEATURE_SKILLS`, `FEATURE_ORGANISATIONS`, `FEATURE_PEOPLE_FINDER`,
`FEATURE_FEEDBACK` → `settings.FEATURE_FLAGS` dict. Tests commonly override with
`@override_settings(FEATURE_FLAGS=...)`.

## Static & templates

- **`govuk/static/`** — GOV.UK frontend bundle (`govuk-frontend-6.0.0.min.{css,js}`),
  `main.css`, `main.js`, `cyber.css`, `assets/` (fonts, images, manifest), and
  **`js/`** for Draftail editor plugins (`draftail-govuk-button.js`,
  `draftail-raw-html.js`).
- **`govuk/templates/`** — `base.html` plus subdirs: `govuk/`, `includes/`,
  `accounts/`, `feedback/`, `search/`, `wagtailcore/`, `wagtailsettings/`.
  Key framework templates: `govuk/role_page.html` (served by the role route),
  `govuk/framework_skills_page.html`, `govuk/content_page.html` (shared by
  framework page types), `includes/role_navigation.html`.
- **Error pages:** `404.html` and `500.html` render their heading/body from
  `ErrorPagesSettings`, and `503.html` from `MaintenanceModeSettings` (all
  falling back to Design System wording); they share `includes/error_contact.html`
  for the contact sentence.
  `socialaccount/authentication_error.html` overrides allauth's default failed
  sign-in page (a spent/re-used OIDC callback code) with a GOV.UK-styled page;
  the override is by template precedence only (govuk/templates ahead of app
  dirs), the 401 status is unchanged.

## Tests

Around 1000 tests across 77 test modules under `govuk/tests/`, all `test_*.py`
(`ls govuk/tests/test_*.py | wc -l` and the test run give the current numbers). A shared
test helper (`govuk/tests/framework_helpers.py`) provides `make_framework_main_page`
and `role_url` for the framework-specific tests.

## CI / Deploy (`.github/workflows/`)

- **`pr-test.yml`** — on PR: Python 3.13, `pip install`, `manage.py check`, then
  `manage.py test`.
- **`deploy.yml`** — builds & pushes Docker image to
  `ghcr.io/gds-dtx/wagtail-govuk:<version>`. Version from app.
- `dependabot.yml` present.
- `Dockerfile` + `docker-compose.yml` at repo root.

## Gotchas / conventions

- **`.venv/bin/python`, never `python`** — bare `python` is not on PATH here.
- **Static files live under `govuk/static/`** — `main.css`, `main.js` and
  `cyber.css` at the top level, editor JS under `govuk/static/govuk/js/`. Check
  `wagtail_hooks.py` for how a file is referenced before adding one. Static
  changes need `collectstatic` + hard refresh to show in a running admin.
- **Migrations:** numbered with gaps; `ls govuk/migrations | tail -1` is the
  leaf (`0078_pageusefulnessvote` when this was written; 0077 moves the
  error-contact fields off `CustomiseSettings` onto the new
  `ErrorPagesSettings`, 0078 adds the `PageUsefulnessVote` table). `0071` and `0072` convert an existing instance's framework content
  in place and delete its role pages (two migrations because Postgres will not
  alter a table in the same transaction that changed its rows) — see `docs/cutover.md`, "Upgrading an instance
  that already has content". Data migrations walk RichTextField/JSONField
  content and `bulk_update` (no signals/revisions), with fail-loud post-checks;
  0071 is the exception (raw SQL and `page.move()`) and stops rather than
  guesses when a role page has children.
- **`wagtail.contrib.routable_page` is in `INSTALLED_APPS`** — required for
  `FrameworkMainPage.serve_role`. The route is at `role/<slug>/` (NOT bare
  `<slug>/` — that would shadow the main page's child pages; the one slug it
  does shadow is a child page called `role`). Singular `role` because that is
  the live service's URL, so with the framework main page as home a role is
  served at its live path (`/role/<slug>`) and needs no redirect.
- **`FrameworkMainPage` and `FrameworkSkillsPage` are each limited to one per
  instance** (`max_count = 1`, which Wagtail counts across the whole tree, not
  per site; the admin enforces it through `can_create_at` and the page import
  refuses to create a second). `FrameworkMainPage` has no `parent_page_types`
  restriction — it can be created anywhere in the tree (the general container
  types list it in their `subpage_types`); `max_count` keeps it to one.
  `FrameworkContentPage` may only be a child of `FrameworkMainPage`.
- **No `RolePage` or `SkillsAZPage`** — these were removed. The former
  `SkillsAZPage` is `FrameworkSkillsPage`; the former `RolePage` is gone entirely.
  Historical migrations still name them; do not delete those migrations.
- **`HEX_COLOR_VALIDATOR`** is defined in `models.py` and appears unused now that
  the hero-colour fields were removed, but it is referenced by historical
  migrations — leave it.
- **Auth is OIDC/SSO-first** — default `account_login` redirects to OIDC
  (`oidc_login_redirect`). SSO defaults point at
  `sso.service.security.gov.uk`.
- Buttons/rich-text only appear in **unrestricted** `RichTextField`s; most
  RichTextFields/StreamFields use restricted `features` lists.
