# wagtail-govuk

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

By default the project uses `govuk/settings/local.py` for local development which is configured to use SQLite. You can override this by setting the `DJANGO_SETTINGS_MODULE` environment variable to point to a different settings file.

```bash
# Run checks, apply migrations, and start the server
python manage.py check
python manage.py migrate
python manage.py runserver
```

## Environment Variables

- `DJANGO_SETTINGS_MODULE`: The settings module to use for the project. Defaults to `govuk.settings.local`.

When using `govuk.settings.dev`, the following variables are required:

- `SECRET_KEY`: A secret key for the Django project. Required for production.
- `DATABASE_NAME`: PostgreSQL database name.
- `DATABASE_USER`: PostgreSQL user.
- `DATABASE_PASSWORD`: PostgreSQL password.
- `DATABASE_HOST`: PostgreSQL host.
- `DATABASE_PORT`: PostgreSQL port (defaults to `5432`).
- `DOMAIN`: Hostname for `ALLOWED_HOSTS` (in addition to `*`).
- `BASE_URL`: Full base URL used for CSRF, CORS, and Wagtail admin URLs (for example `https://example.com`).
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
