# wagtail-govuk

A Wagtail content management system with a GOV.UK Design System front end. One
codebase serves several government sites, each configured by environment
variables and by site settings held in the CMS, so most of what makes an
instance particular to a service lives in its configuration rather than in a
fork.

See [CONTRIBUTING.md](CONTRIBUTING.md) if you want to send a change back.

## Documentation

- [Platform boundaries](docs/platform-boundaries.md) — what every instance
  gets, what belongs to the Capability Framework alone, the feature flags and
  what they do and do not gate, and the one-site-per-database rule. Read this
  before building the second service on this codebase.
- [Accessibility](docs/accessibility.md) — the component baseline, the
  guarantees under automated test, known deviations and audit status.
- [Security](docs/security.md) — response headers, the authorisation model, the
  dependency policy, and what has and has not been tested. Reporting a
  vulnerability is in [SECURITY.md](SECURITY.md).
- [Editorial workflow](docs/editorial-workflow.md) — how content is drafted,
  moderated and published, who can do what, and where the audit trail is.
- [Cutting a site over to Wagtail](docs/cutover.md) — the migration runbook.
  Start with [where content comes from](docs/cutover.md#where-the-content-actually-comes-from):
  none of it is in this repository. Also covers the three kinds of state that do
  not travel in a content export, and how to refresh content before you go.
- [Decommissioning the old service](docs/decommission.md) — what happens to the
  system being replaced, and why not at cutover.
- [The published downloads](docs/downloads.md) — the three CSVs and their
  column schemas.
- [Logging, monitoring and alerting](docs/monitoring.md) — what is emitted,
  what is watched, and the alerts still to agree.
- [Analytics](docs/analytics.md) — there is none; the options, and the decision
  that has to be made before there is.
- [User journeys](docs/user-journeys.md) — the journeys to test, written so
  somebody who did not build it can run them.
- [Delivery status](docs/delivery-status.md) — every Jira ticket, its evidence,
  and what is genuinely left.

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

By default the project uses `govuk/settings/local.py` for local development which is configured to use SQLite. You can override this by setting the `DJANGO_SETTINGS_MODULE` environment variable to point to a different settings file. WSGI servers such as Gunicorn must set `DJANGO_SETTINGS_MODULE` explicitly to a non-local settings module.

```bash
# Run checks, apply migrations, and start the server
python manage.py check
python manage.py migrate
python manage.py runserver
```

## Environment Variables

- `DJANGO_SETTINGS_MODULE`: The settings module to use for the project. Local `manage.py` workflows default to `govuk.settings.local`; Gunicorn requires an explicit non-local settings module.

When using `govuk.settings.dev`, the following variables are required:

- `SECRET_KEY`: A secret key for the Django project. Required for production.
- `DATABASE_NAME`: PostgreSQL database name.
- `DATABASE_USER`: PostgreSQL user.
- `DATABASE_PASSWORD`: PostgreSQL password.
- `DATABASE_HOST`: PostgreSQL host.
- `DATABASE_PORT`: PostgreSQL port (defaults to `5432`).
- `DOMAIN`: Hostname for `ALLOWED_HOSTS` (in addition to `*`).
- `BASE_URL`: Full base URL used for CSRF, CORS, Wagtail admin URLs, and OIDC redirects (for example `https://example.com`).
- `OIDC_CLIENT_ID`: Internal Access client ID.
- `OIDC_CLIENT_SECRET`: Internal Access client secret.
- `OIDC_JWKS_URL`: JWKS URL for API bearer token verification. Defaults to `https://sso.service.security.gov.uk/.well-known/jwks.json`.
- `OIDC_ISSUER`: Expected JWT issuer for API bearer token verification. Defaults to `https://sso.service.security.gov.uk`.
- `OIDC_TOKEN_AUDIENCE`: Expected JWT audience for API bearer token verification. Defaults to `OIDC_CLIENT_ID`.
- `INCOMING_REQUEST_INFO_LOGGING`: Enable info logging for inbound Django requests, including all sent request headers. Defaults to `false`.
- `CONTENT_DISCOVERY_REQUEST_INFO_LOGGING`: Enable info logging for outbound content discovery requests, including all sent request headers. Defaults to `false`.

## Bulk Import Content Discovery Sources (CSV)

You can bulk create or update content discovery sources from CSV either:

1. In Wagtail admin: `Settings` -> `Content discovery` -> `Import sources CSV`
2. Via management command:

```bash
python manage.py import_content_discovery_sources ./sources.csv --site-id 1
```

Rows are upserted by `(site_id, url)`.

- `site_id`: Optional in CSV if you pass `--site-id`.
- `url`: Required.
- `name`: Optional.
- `disable_tls_verification`: Optional boolean (`true/false`, `1/0`, `yes/no`, `on/off`).
- `default_tags`: Optional tag keys (`GovukTag.slug`) separated by `|`.

Example CSV:

```csv
site_id,url,name,disable_tls_verification,default_tags
1,https://example.gov.uk/feed.xml,Example feed,false,policy|news
1,https://api.github.com/orgs/alphagov/repos,GOV.UK repos,false,open-source
```

## Licence

The code in this repository is published under the [MIT Licence](LICENCE).

Documentation and content are © Crown copyright and available under the terms
of the [Open Government Licence v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/),
except where otherwise stated.
