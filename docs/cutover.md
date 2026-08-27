# Cutting a site over to Wagtail

This is the runbook for moving a service from its existing publishing platform
onto a new Wagtail instance. It is written from the DDaT Capability Framework
migration, but the shape applies to any instance: content comes across in one
export file, and three categories of state do not travel with it and have to be
recreated by hand.

Read the whole thing before starting. The steps are ordered because several of
them depend on the one before, and two of them are hard to undo once DNS has
moved.

## What travels, and what does not

The admin export at `/admin/pages/import-export/` produces a single JSON
document in the `govuk-page-import-export/v1` format. Its top-level keys are
`format` and `pages`, plus `skills`, `roles`, `changelog` and `wording` when the
framework content is included. That covers the page tree, the framework
snippets, and the change notes.

Three things are outside it:

| Not in the export | Where it lives | How it gets to production |
| --- | --- | --- |
| Redirects | `wagtail.contrib.redirects.Redirect` rows | `manage.py seed_live_service_redirects` |
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
- [ ] `enable_execute_command = true` on the production instance for the cutover.
      Step 4 needs a shell on the running task. Turn it off afterwards.
- [ ] `NOINDEX = "false"` in the production task definition. It defaults to
      `True` (`govuk/settings/base.py`), and a site that launches with the
      default serves `Disallow: /` and a `noindex, nofollow` meta tag on every
      page. This is the single easiest thing to miss and the most expensive.
- [ ] `FEATURE_FEEDBACK = "false"` unless the service wants the built-in feedback
      view. When it is on, `feedback_view` shadows any `/feedback` page, which is
      where the phase banner links site-wide.

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

The first page in the file is the home page, and a fresh instance ships an
empty placeholder home page. `_replace_placeholder_home_page` swaps the
placeholder for the imported page and repoints the site at it, but only while
the placeholder has no children. **Import before anyone adds a page by hand.**
If the placeholder has picked up children, the import nests the whole site a
level down and every URL gains a `/home/` prefix.

## 4. Seed the redirects

The live service publishes roles at `/role/<slug>` and skills at
`/skill/<slug>`. Wagtail serves a role at `/<slug>` and every skill as a section
of the Skills A to Z. Without redirects, every bookmark, every search result and
every link inside the migrated content itself answers 404 — the welcome copy
alone links 37 roles the old way.

```bash
aws ecs execute-command --cluster <cluster> --task <task-id> \
  --container <container> --interactive \
  --command "python manage.py seed_live_service_redirects --hostname <production-host>"
```

`--hostname` scopes the redirects to one site, so a shared instance leaves its
other sites alone. The command creates or updates and deletes nothing, so it is
safe to run again after content moves — and it should be, because each redirect
points at whichever page carries that role or skill at the time it runs.

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

**Customise settings** (`CustomiseSettings`) — for the Capability Framework:
`show_service_name_in_navigation` on, `search_placeholder` set to
"Search for roles and skills", `hide_sign_in_link` on, and the error-page
contact name and email. Confirm the contact address with the service team
before entering it; the address carried in the migration fixture is not
necessarily the one the service wants published.

**Phase banner settings** (`PhaseBannerSettings`) — `enabled`, the phase, and
the three pieces of wording either side of the feedback link. The framework's
banner reads "Complete our 3 minute / feedback survey / to help us improve the
framework." with the link pointing at `/feedback`.

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

## 7. Switch DNS

Only once step 6 is clean. Lower the TTL on the old records in advance so the
change propagates quickly and so a reversal does too.

## 8. Afterwards

- [ ] Set `enable_execute_command` back to `false`.
- [ ] Confirm the old service's URLs redirect rather than 404, from a client
      that has never visited either host.
- [ ] Re-run `redirect_coverage.mjs` once more, after DNS, against the real
      domain.

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
