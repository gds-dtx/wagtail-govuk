# Security

What this codebase does about security, what has been verified, and what has
not. It backs Jira CS32-3307 and the licensing and transparency half of
CS32-3309. How to report a vulnerability is in [SECURITY.md](../SECURITY.md).

The short version: the engineering is in place and verified running. **This
build has never had an independent penetration test or vulnerability scan**,
which is an acceptance criterion on CS32-3307 and cannot be closed by the team
on its own. It is the item on that ticket most likely to hold up production, and
it has commissioning lead time before any remediation time.

## Response headers

Set in `govuk/settings/base.py` and confirmed on the running dev instance on
31 August 2026 by reading the response headers rather than the settings file —
a setting only matters if it survives the proxy in front of it.

| Header | Value in production configuration |
| --- | --- |
| `content-security-policy` | `default-src 'self'; script-src 'self' 'nonce-…'; style-src 'self'; img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; frame-src 'none'; object-src 'none'; base-uri 'self'; form-action 'self'` |
| `strict-transport-security` | `max-age=31536000; includeSubDomains; preload` |
| `x-frame-options` | `SAMEORIGIN` |
| `x-content-type-options` | `nosniff` |
| `referrer-policy` | `same-origin` |
| `cross-origin-opener-policy` | `same-origin` |
| `permissions-policy` | accelerometer, camera, display-capture, geolocation, gyroscope, magnetometer, microphone, payment and usb all `()` |

The front-end CSP carries **no `unsafe-inline`**: scripts run under a
per-response nonce (`django.utils.csp`, Django 6). The admin is served a
loosened policy by `govuk.middleware.AdminCSPMiddleware` — `unsafe-inline` for
scripts and styles, and `www.gravatar.com` for images — because Wagtail's admin
JavaScript requires it. That is a deliberate, scoped deviation: it applies to
`/admin/` only, which is behind authentication, and it is worth re-testing after
any Wagtail major upgrade in case the requirement has gone away.

`SECURE_HSTS_INCLUDE_SUBDOMAINS` and `SECURE_HSTS_PRELOAD` default to `False`
in `base.py` and are switched on per environment. Leave them off until every
hostname under the domain is HTTPS — preload is very hard to reverse.

## Cookies and sessions

- `SESSION_COOKIE_SECURE` and `CSRF_COOKIE_SECURE` both default to `True`.
  Django defaults the CSRF one to `False`, which would have made it the only
  credential on an HTTPS-only site able to travel in clear text.
- `CSRF_COOKIE_HTTPONLY` is deliberately **not** set: Wagtail's admin
  JavaScript reads the cookie to build the `X-CSRFToken` header.
- Sessions last 12 hours (`SESSION_COOKIE_AGE`), do not slide on each request,
  and do not expire at browser close.
- The public site sets no analytics or marketing cookies. See
  [analytics.md](analytics.md) — this is the assumption CS32-3314 was closed on.

## Authorisation

Two defects were found and fixed during the migration. Both were authorisation
rather than authentication, which is the class that automated scanning does not
find:

1. **Import/Export asked only for admin access.** Any account that could reach
   the Wagtail admin at all could upload a file that rewrote every role and
   skill on the site, or export a payload containing a password-protected
   page's shared password. It now asks the same page, snippet and settings
   permissions the rest of the admin asks for the same objects.
2. **Front-end search listed pages the reader could not open.** Group- and
   password-restricted pages appeared in results for any signed-in account,
   leaking their titles and URLs. Search now shows a reader only what they
   would be served.

Beyond those, a with-root import can no longer silently rebuild the site
beneath a second `/home/`, and the rich-text and changelog parsing patterns no
longer backtrack catastrophically on hostile input.

Non-superuser groups that run imports need the matching `govuk.*` snippet
permissions granted. Migration `0065_editor_snippet_permissions` does that for
the standard Editors and Moderators groups; **it is not yet deployed to any
instance**, so on dev the gap is masked by five of eight accounts being
superusers. See [editorial-workflow.md](editorial-workflow.md).

## Deployed settings

- `DEBUG` defaults to `False`. It previously defaulted to `True`, which exposed
  tracebacks with settings and environment on any unhandled error.
- `ALLOWED_HOSTS` is environment-driven with no wildcard, falling back to
  `DOMAIN`. `deployment_allowed_hosts()` adds the task's own address so the load
  balancer health check, which arrives by IP, still passes.
- `NOINDEX` defaults to `True`, so an instance that is brought up without being
  told otherwise serves `Disallow: /`. That is the safe default for a
  pre-launch environment and the single easiest thing to forget at cutover —
  it is a checklist item in [cutover.md](cutover.md).

## Dependencies

`pyproject.toml` pins `wagtail==7.4.3` and holds Django to the 6.0 series
(`Django>=6.0.2,<6.1`; 6.1 dropped `django.utils.cache.cc_delim_re`, which
Django REST Framework still imports).

Wagtail 7.4.1 carried fifteen published advisories, the most serious being
PYSEC-2026-616. **The instances are still serving 7.4.1**: 7.4.3 is on the
release branch and reaches a running site only with the next image build. Until
then the advisories apply to the deployed service, not just to a version string
in a file.

`.github/dependabot.yml` raises grouped weekly pull requests for pip and GitHub
Actions. `.github/workflows/pr-test.yml` runs `ruff check`, `manage.py check`
and the full test suite on every pull request. There is no dependency
vulnerability scan in CI — `pip-audit` would be a cheap addition and is not yet
made.

## Secret sweep of history

Run 31 August 2026 over **all 289 commits and 1,767 text blobs reachable from
any ref**, against patterns for AWS access key IDs and secret keys, private key
blocks, GitHub and Slack tokens, Google API keys, JWTs, database URLs
containing passwords, Django `SECRET_KEY` literals, and generic
password/token/secret assignments.

**Nothing was found that needs rotating.** The only matches were three literal
passwords in four test files, all of them fixtures for tests that create a user
and immediately log in as them:

| Literal | Files |
| --- | --- |
| `password="password"` | `govuk/tests/test_search_view.py`, `govuk/tests/test_tag_listings_page.py` |
| `password="correct-horse-battery-staple"` | `govuk/tests/test_search_view.py`, `govuk/tests/test_import_export_permissions.py` |
| `password="unused-password"` | `govuk/tests/test_authenticated_redirect_middleware.py` |

These are test data, they authenticate against a database created and destroyed
by the test run, and they should stay as they are.

The sweep is a point-in-time result, not a control. It is worth enabling GitHub
push protection and secret scanning on the repository so the next one cannot be
committed, which is a repository setting rather than a code change.

## The `/security*` redirect

Anything on these instances whose path begins with `security` is redirected
off-site, before the request reaches the application. Reproduced on dev on
31 August 2026:

```
/security.txt         302 -> https://vulnerability-reporting.service.security.gov.uk/.well-known/security.txt
/security-architect/  302 -> https://vulnerability-reporting.service.security.gov.uk/.well-known/security.txt
/securityfoo          302 -> https://vulnerability-reporting.service.security.gov.uk/.well-known/security.txt
/secure               301 -> /secure/                                    (normal handling)
```

The rule is meant to catch the one `.well-known/security.txt` path. It is
matching an unanchored `security` prefix instead, so it also swallows the
**Security architect** role page — a real role in the framework, now
unreachable at its own address. This site's own redirect works correctly and
delivers the reader straight into it: `/role/security-architect` `301`s to
`/security-architect/`, which is then taken off-site.

It is not fixable from this repository. The rule lives in the shared
`wagtail-iac` module that every one of these instances is built from
(`source = "github.com/gds-dtx/wagtail-iac"` in each `wagtail-instances`
definition), and another site on the same platform behaves identically. It
needs a change by whoever owns that module, narrowing the match to the exact
path.

This is tracked as T25 on CS32-3526 and is a **go-live blocker**: the only
site-side workaround is renaming the role's URL, which breaks the live
service's own `/role/security-architect` address and is not a good trade.

## Outstanding

- [ ] **Independent penetration test or vulnerability scan, with high and
      critical findings remediated before launch.** Not booked. CS32-3307.
- [ ] **Narrow the `/security*` redirect** in `wagtail-iac`. Needs an owner.
      CS32-3526 T25.
- [ ] **Deploy 7.4.3.** The advisories apply to what is running, not to the
      branch.
- [ ] Alerting thresholds for 5xx and authentication failures — see
      [monitoring.md](monitoring.md). CS32-3484.
- [ ] Add a dependency vulnerability scan to CI.
- [ ] Turn on GitHub secret scanning and push protection.
- [x] Response headers verified on a running instance (31 August 2026).
- [x] Both authorisation defects fixed, with regression tests.
- [x] `DEBUG` and `ALLOWED_HOSTS` hardened.
- [x] Secret sweep of the full history (31 August 2026, clean).
