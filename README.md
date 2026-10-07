# wagtail-govuk

A Wagtail content management system with a GOV.UK Design System front end. One
codebase serves several government sites, each configured by environment
variables and by site settings held in the CMS, so most of what makes an
instance particular to a service lives in its configuration rather than in a
fork.

See [CONTRIBUTING.md](CONTRIBUTING.md) if you want to send a change back, and
[SECURITY.md](SECURITY.md) to report a vulnerability.

## Local Setup

1. Create and activate a virtual environment

```bash
python -m venv .venv
source .venv/bin/activate
# For Windows
# .venv\Scripts\activate
```

2. Install the project via `pyproject.toml`

```bash
python -m pip install --upgrade pip
pip install -e .
```

3. Run the development server

By default the project uses `govuk/settings/development.py` for local development which is configured to use SQLite. You can override this by setting the `DJANGO_SETTINGS_MODULE` environment variable to point to a different settings file. WSGI servers such as Gunicorn must set `DJANGO_SETTINGS_MODULE` explicitly to a deployed settings module.

```bash
# Run checks, apply migrations, and start the server
python manage.py check
python manage.py migrate
python manage.py runserver
```

## Environment Variables

- `DJANGO_SETTINGS_MODULE`: The settings module to use for the project. Local `manage.py` workflows default to `govuk.settings.development`; Gunicorn requires an explicit deployed settings module, normally `govuk.settings.production`.

When using `govuk.settings.production`, the following variables are required:

- `SECRET_KEY`: A secret key for the Django project.
- `DATABASE_NAME`: PostgreSQL database name.
- `DATABASE_USER`: PostgreSQL user.
- `DATABASE_PASSWORD`: PostgreSQL password.
- `DATABASE_HOST`: PostgreSQL host.
- `DATABASE_PORT`: PostgreSQL port (defaults to `5432`).
- `BASE_URL`: Full base URL used for CSRF, CORS, Wagtail admin URLs, and OIDC redirects (for example `https://example.com`).
- `DOMAIN`: Hostname of the site. Used for the default Wagtail `Site` and as the fallback for `ALLOWED_HOSTS`.
- `OIDC_CLIENT_ID`: Internal Access client ID.
- `OIDC_CLIENT_SECRET`: Internal Access client secret.

Optional:

- `ALLOWED_HOSTS`: Comma-separated list of hosts the app answers on. There is no wildcard; falls back to `DOMAIN`. The task's own address is added for the load balancer health check.
- `NOINDEX`: Defaults to `true`, which serves `Disallow: /` from `robots.txt` and a `noindex` meta tag on every page. Set it to `false` on a site that should be indexed.
- `DEBUG`: Defaults to `False`.
- `LOG_LEVEL`: Application log level (defaults to `INFO`). Logs are written to stdout as JSON.
- `ADMIN_USER_EMAILS`: Comma-separated email addresses that are created or updated as superusers after `migrate`.
- `OIDC_PROVIDER_ID`: allauth provider ID (defaults to `internal-access`).
- `OIDC_JWKS_URL`: JWKS URL for API bearer token verification. Defaults to `https://sso.service.security.gov.uk/.well-known/jwks.json`.
- `OIDC_ISSUER`: Expected JWT issuer for API bearer token verification. Defaults to `https://sso.service.security.gov.uk`.
- `OIDC_TOKEN_AUDIENCE` / `OIDC_TOKEN_AUDIENCES`: Expected JWT audience, or a comma-separated list of them, for API bearer token verification. Defaults to `OIDC_CLIENT_ID`.
- `FEATURE_SKILLS`, `FEATURE_NEWS`, `FEATURE_FEEDBACK`, `FEATURE_ORGANISATIONS`, `FEATURE_PEOPLE_FINDER`: Feature flags, all off by default. See [Feature flags](#feature-flags).
- `SCHEDULED_PUBLISHING`: Defaults to `false`, which hides the go-live and expiry fields in the admin. Turn it on only once something runs `manage.py publish_scheduled` on a timer.
- `MAINTENANCE_MODE`: Emergency close of the whole site behind the 503 page, for everyone including editors. `MAINTENANCE_RESUME_TEXT` sets the "back at" wording and `MAINTENANCE_RETRY_AFTER` the `Retry-After` header in seconds (defaults to `3600`). Planned maintenance is the **Maintenance mode** site setting instead.
- `MEDIA_S3_BUCKET`: Store uploaded images and documents in this S3 bucket instead of on the local filesystem. `MEDIA_S3_REGION`, `MEDIA_S3_LOCATION` (key prefix, defaults to `media`), `MEDIA_S3_CUSTOM_DOMAIN` and `MEDIA_S3_QUERYSTRING_AUTH` configure it further. Credentials come from the task role.
- `CACHE_URL`: Redis URL(s) for a shared cache. Needs `redis` added to the dependencies; keys are prefixed with `CACHE_KEY_PREFIX` or `DOMAIN`.
- `HSTS_SECONDS`, `HSTS_INCLUDE_SUBDOMAINS`, `HSTS_PRELOAD`: HSTS header. Leave the last two off until every hostname under the domain is HTTPS.
- `SESSION_COOKIE_AGE`: Session length in seconds (defaults to 12 hours).
- `INCOMING_REQUEST_INFO_LOGGING`: Enable info logging for inbound Django requests and their header names. Header values are redacted except for a fixed allow-list. Defaults to `false`.
- `CONTENT_DISCOVERY_REQUEST_INFO_LOGGING`: Enable info logging for outbound content discovery requests, including all sent request headers. Defaults to `false`.

## Feature flags

Read once at startup from the environment into `settings.FEATURE_FLAGS`
(`govuk/settings/base.py`).

| Flag | Environment variable | What it turns on |
| --- | --- | --- |
| `SKILLS` | `FEATURE_SKILLS` | The Capability Framework: roles, skills, changelog, its page types, wording and sidebar settings, and the CSV downloads |
| `NEWS` | `FEATURE_NEWS` | News articles and the news index page type |
| `FEEDBACK` | `FEATURE_FEEDBACK` | The built-in feedback form at `/feedback`, and its snippet listing. When on, it shadows any CMS page at `/feedback` |
| `ORGANISATIONS` | `FEATURE_ORGANISATIONS` | Nothing. No code reads it |
| `PEOPLE_FINDER` | `FEATURE_PEOPLE_FINDER` | Nothing. No code reads it |

A flag gates structure only. With it off, the feature's page
types cannot be created, return 404 if the page import puts one in the tree,
and are left out of search, the pages API, the service navigation and tag
listings. The admin explorer still shows them, so they can be deleted.

Data migrations cannot read the flags, so a few rows they write exist on every
instance — for example the Editors and Moderators permissions over the
framework's snippets. They are inert where the flag is off.


## Capability Framework

Turned on by `FEATURE_SKILLS`.

- **Roles and skills are snippets**, authored under **Capability framework** in
  the admin. `FrameworkMainPage` (one per instance) serves each live role at
  `role/<slug>/` beneath it, so with the main page as the site's home page a
  role lives at `/role/<slug>/`. `FrameworkSkillsPage` (one per instance) is the
  skills A to Z.
- **Framework content pages** can only be created under the main page. They,
  and the skills A to Z, make up the side menu's **Further resources** group.
  **Sidebar settings** decides the menu: once any page is listed, only the
  listed and ticked pages are shown, in the order given. With an empty list
  every framework page is shown in tree order.
- **Downloads** are generated from live content at request time, so they cannot
  fall behind the site:
  - `/download/roles.csv`
  - `/download/skills.csv`
  - `/download/changelog.csv`

  A link to one of these in rich text renders as a GOV.UK attachment component.
  The columns are defined in `govuk/capability_framework/csv_downloads.py`.
- **Management commands:**

  ```bash
  python manage.py import_capability_framework <dir>   # seed from roles.csv and skills.csv
  python manage.py export_capability_framework <dir>   # write the three published CSVs
  python manage.py import_role_grades [--save FILE | --json FILE] [--dry-run]
  ```


## Editing and publishing

**Access.** The admin is behind single sign-on: an unauthenticated request to
`/admin/` or `/django-admin/` redirects to the OIDC provider. Signing in creates
an account with no permissions. An administrator then can then add it to a group at
`/admin/users/`. Accounts are per instance.

| Group | Can |
| --- | --- |
| **Editors** | Create and edit pages and snippets, save drafts, submit for moderation |
| **Moderators** | Everything Editors can, plus approve, publish, unpublish and delete |

Superusers (including those from `ADMIN_USER_EMAILS`) pass every permission
check and also manage users, groups and settings.

**Moderation.** Pages, roles and skills use the **Moderators approval**
workflow: a save creates a revision, **Submit for moderation** sends it for
review, and a moderator's approval publishes it. A moderator can also publish
directly. Change notes have no draft state and are live when saved.

**Scheduling** is hidden unless `SCHEDULED_PUBLISHING` is on (see above).

**Site settings** (`/admin/settings/`: Various settings including Customise, Footer, Phase banner, Error pages, Maintenance mode, and the framework's Wording 
and Sidebar settings) are per site.

**Maintenance mode** (the site setting) closes the site behind the 503 page for
the public while signed-in staff with admin access get through. The health
check, admin, sign-in and static assets stay open either way.

**Reports.**
- **Reports → Page usefulness** counts the yes and no answers to "Is this page
  useful?" by page, when enabled, with a date filter and CSV or Excel export.
  `python manage.py prune_page_usefulness_votes --days N` deletes old answers.
- **Reports → Site history** is Wagtail's audit log of every publish, edit,
  move, delete and workflow action, with the account and time. Each item's
  History tab shows its own slice, and any earlier revision can be restored
  from there.

## Moving content between instances

This repository holds no content. Content moves between instances through the
JSON import and export at `/admin/pages/import-export/`, in the
`govuk-page-import-export/v1` format. It carries the page tree and, when the
framework is on, its skills, roles, change notes and wording.

- The import **adds and updates; it never deletes**. A file that omits a page
  leaves that page in place.
- Change notes have no identifier, so the import
  replaces a role's, skill's or the framework's entries with the file's set.
- It respects the importing account's permissions. Pages are published only if
  that account could publish them; otherwise they are saved as drafts. Every
  imported page gets a revision and an audit log entry.
- Not included: redirects, site settings, and uploaded images and documents.
  Recreate those on the target instance.


## Licence

The code in this repository is published under the [MIT Licence](LICENCE).

Documentation and content are © Crown copyright and available under the terms
of the [Open Government Licence v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/),
except where otherwise stated.
