# Project Notes — wagtail-govuk

> Orientation doc for a fresh context window. Read this first, then jump to the
> file paths it names. Everything here is verifiable in the tree; when in doubt,
> trust the code over this doc and update this doc.

## What it is

A **Wagtail 7.4.1 / Django ≥6.0** CMS themed with the **GOV.UK Design System**
(`govuk-frontend` 6.0.0). Single Django app named **`govuk`** — there is no
`home` app despite Wagtail's default template leaving `home`-named fossils
around (see "Gotchas"). Owned by `gds-dtx`, deployed as a container to GHCR.

Content is authored in Wagtail admin and served with GOV.UK-styled templates.
The site also exposes a **REST API**, **OIDC/SSO auth**, and a **content
discovery** subsystem that ingests external feeds.

## Runtime & environment

- **Python ≥3.12** (local dev here is 3.14; CI uses 3.13). Prod image per Dockerfile.
- **No global `python`** on this machine — always use `.venv/bin/python`.
- Install: `pip install -e .` (deps live in `pyproject.toml`, not requirements.txt).
- Package deps: Django, wagtail==7.4.1, gunicorn, whitenoise, django-allauth
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
# Tests (SQLite, local settings) — 232 tests as of this writing:
.venv/bin/python manage.py test govuk --settings=govuk.settings.local
# Narrow to one module:
.venv/bin/python manage.py test govuk.tests.test_wagtail_rich_text_hooks --settings=govuk.settings.local
```

Note: `manage.py test` prints 7 pre-existing `treebeard.E001` system-check
warnings — harmless noise, unrelated to any change.

## Code map (`govuk/`)

Big files first — these are where most work lands:

- **`models.py`** (~2800 lines) — all content types + settings models. Key page
  types: `ContentPage`, `RolePage`, `SkillsAZPage`, `TagListingsPage`,
  `SectionPage` (each a Wagtail `Page`). Snippet/other models: `GovukSkill`,
  `GovukRole`, `GovukTag`, `ExternalContentItem`, `ContentDiscoverySource`,
  `Feedback`, `EdDSAKeyPair`. Site settings (BaseSiteSetting):
  `PhaseBannerSettings`, `FooterSettings`, `CustomiseSettings`,
  `ContentDiscoverySettings`, `AuthenticatedRedirectSettings`, `EdDSAKeySettings`.
  Rich text: `LinkBlock` / `LinkStructValue` for StreamField links.
- **`wagtail_hooks.py`** (~1170 lines) — Draftail rich-text customisations and
  admin hooks. Home of the **unified GOV.UK button** rich-text feature
  (`GOVUK_BUTTON_*` constants, `GovukButtonLinkHandler`,
  `GovukButtonLinkElementHandler`, `govuk_button_entity`) and the **raw-HTML
  embed** feature. Registers editor JS from `static/js/` (see Gotchas).
- **`views.py`** (~270 lines) — custom views (search, profile, custom CSS, etc.).

Subsystem modules (smaller, single-purpose):

- **Auth / SSO:** `oidc.py`, `authentication.py`, `adapters.py` (allauth),
  `jwt_tokens.py`, `middleware.py` (`AdminOIDCLoginMiddleware`,
  `AuthenticatedUserRedirectMiddleware`). JWKS/JWT via simplejwt (RS256).
- **API:** `api.py` (DRF + Wagtail API v2 router), served under `/api/`.
- **Content discovery:** `content_discovery.py`, `content_discovery_import.py`
  (CSV upsert by `(site_id, url)`), management command
  `sync_external_content.py`. Ingests external feeds into `ExternalContentItem`.
- **Search:** `search_backend.py`, search view + template.
- **Import/export:** `page_import_export.py` (admin views for page import/export).
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

## Tests

~40 test modules under `govuk/tests/`, all `test_*.py`, 232 tests total. Rich-text
hook tests (`test_wagtail_rich_text_hooks.py`) reload the hooks module in `setUp`
and use `SimpleTestCase`. Heavy coverage of auth/OIDC/JWT, content discovery,
each page type, middleware, and settings.

## CI / Deploy (`.github/workflows/`)

- **`pr-test.yml`** — on PR: Python 3.13, `pip install`, `manage.py check`, then
  `manage.py test`.
- **`deploy.yml`** — builds & pushes Docker image to
  `ghcr.io/gds-dtx/wagtail-govuk:<version>`. Version from app.
- `dependabot.yml` present.
- `Dockerfile` + `docker-compose.yml` at repo root.

## Gotchas / conventions

- **`.venv/bin/python`, never `python`** — bare `python` is not on PATH here.
- **Static JS lives at `static/js/`, un-namespaced** — matching `main.css`/
  `main.js`/`cyber.css`. It was recently moved out of the old
  `static/govuk/js/` sub-namespace, and a dead duplicate under `static/home/`
  (a Wagtail-default-template fossil — there is no `home` app in INSTALLED_APPS)
  was deleted. Reference editor JS as e.g. `"js/draftail-govuk-button.js"`.
  Static changes need `collectstatic` + hard refresh to show in a running admin.
- **Unified GOV.UK button** rich-text entity: variant (default/start/secondary/
  warning) + open-in-new-tab are stored as `data-govuk-button-*` attributes on
  the `<a>`; one Draftail toolbar entry, not several. Legacy
  `linktype="govuk-start-button"` markup was converted to the new format by data
  migration `0050_*` and the back-compat shims removed. (Historical migrations
  hardcode marker strings rather than importing from `wagtail_hooks.py`, since
  those symbols get deleted.)
- **Migrations:** 46 numbered migrations; latest is `0050_*`. Data migrations
  here walk RichTextField/JSONField content and `bulk_update` (no signals/
  revisions), with fail-loud post-checks.
- **Auth is OIDC/SSO-first** — default `account_login` redirects to OIDC
  (`oidc_login_redirect`). SSO defaults point at
  `sso.service.security.gov.uk`.
- Buttons/rich-text only appear in **unrestricted** `RichTextField`s; most
  RichTextFields/StreamFields use restricted `features` lists.
