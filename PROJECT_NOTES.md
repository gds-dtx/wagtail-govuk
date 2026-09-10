# Project Notes — wagtail-govuk

> Orientation doc for a fresh context window. Read this first, then jump to the
> file paths it names. Everything here is verifiable in the tree; when in doubt,
> trust the code over this doc and update this doc.

## What it is

A **Wagtail 7.4.3 / Django ≥6.0** CMS themed with the **GOV.UK Design System**
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
- Package deps: Django, wagtail==7.4.3, gunicorn, whitenoise, django-allauth
  (socialaccount+openid), djangorestframework-simplejwt[crypto], psycopg2-binary.

### Settings layout (`govuk/settings/`)

| Module | Use | DB | Notes |
|---|---|---|---|
| `base.py` | shared base, imported by all | — | env-var helpers `_bool_env`, `_parse_csv_env`; `FEATURE_FLAGS`; MIDDLEWARE; SIMPLE_JWT |
| `local.py` | **default** for `manage.py` locally | SQLite (`db.sqlite3`) | `DEBUG=True`, dummy `SECRET_KEY="abc123"`, verbose logging on |
| `dev.py` | server / container | PostgreSQL (env vars) | requires `SECRET_KEY`, `DATABASE_*`, `BASE_URL`, `DOMAIN`, `OIDC_*` |
| `runtime.py` | picks local vs non-local | — | `runserver` defaults to local; gunicorn must set `DJANGO_SETTINGS_MODULE` explicitly |

`DJANGO_SETTINGS_MODULE` defaults to `govuk.settings.local` for local workflows.

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

- **`models.py`** (~5 500 lines) — all content types + settings models.

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
  `ContentDiscoverySource`, `Feedback`, `EdDSAKeyPair`.

  Site settings (`BaseSiteSetting`): `PhaseBannerSettings` (order 3),
  `FooterSettings` (order 2), `CustomiseSettings` (order 1 — leads the Settings
  menu). `CustomiseSettings` holds **location dropdowns** for `service_name_location`,
  `sign_in_location` (header / navigation / hidden — "hidden" still shows Sign Out
  for authenticated users), `search_location`, and `header_logo`. Hero colour
  fields were removed; migration 0072 carries a site's colours into `extra_css`
  as the CSS they produced. `content_max_width` (optional px) feeds
  `render_custom_css`, which sets `--govuk-content-width` and shifts main.css's
  1030px centring breakpoint to width + 80. `render_custom_css` (served at
  `/gen/custom.css`, gated by `has_custom_css`) also passes through `extra_css`. `CapabilityFrameworkWordingSettings` and
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

- **`views.py`** (~450 lines) — custom views (search, profile, custom CSS, etc.).

Subsystem modules (smaller, single-purpose):

- **Auth / SSO:** `oidc.py`, `authentication.py`, `adapters.py` (allauth),
  `jwt_tokens.py`, `middleware.py` (`AdminOIDCLoginMiddleware`,
  `AuthenticatedUserRedirectMiddleware`). JWKS/JWT via simplejwt (RS256).
- **API:** `api.py` (DRF + Wagtail API v2 router), served under `/api/`.
- **Content discovery:** `content_discovery.py`, `content_discovery_import.py`
  (CSV upsert by `(site_id, url)`), management command
  `sync_external_content.py`. Ingests external feeds into `ExternalContentItem`.
- **Import/export:** `page_import_export.py` (admin views for page import/export);
  `import_capability_framework.py` (management command — seeds skills, roles,
  `FrameworkMainPage` home, `FrameworkSkillsPage`, and on first run sets
  customise settings and home-page switches).
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
`security.txt`; `/gen/custom.css`; `/search/`; `/robots.txt`;
`/feedback` (flag-gated); Wagtail page serving at `/`.

## Capability Framework subsystem

The framework is a gated subsystem (`FEATURE_FLAGS["SKILLS"]`). Key concepts:

- **Roles are snippets, not pages.** `GovukRole` (slug, title, family, levels,
  SCS fields) is the data model. Each role is served at
  `/<FrameworkMainPage-url>/role/<slug>/` by `FrameworkMainPage.serve_role` —
  a `RoutablePageMixin` route. There is no `RolePage` model.
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
- **Live-service redirects:** `/role/<slug>` and `/skill/<slug>` — seed with
  `seed_live_service_redirects` after import.
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

## Tests

Around 870 tests across 70 test modules under `govuk/tests/`, all `test_*.py`
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
  leaf (`0074_customisesettings_content_max_width_and_more` when this was
  written). `0071` and `0072` convert an existing instance's framework content
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
  the live service's URL: `govuk.live_service_links` writes no redirect for a
  role whose route URL already is its live path.
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
