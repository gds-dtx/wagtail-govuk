# The published downloads

The three CSVs the framework publishes, their column schemas, and how they are
produced. It backs Jira CS32-3313.

This document exists so the "check the columns against the specification" step
on CS32-3313 can actually be done. The specification lives in a SharePoint
document that this repository cannot reach; the schemas below are what the site
emits, taken from `govuk/framework_csv.py`, so the two can be compared side by
side without anyone having to read Python.

## How they are produced

Built from published content **at the moment of asking**. There is no scheduled
job, no stored file and no object in S3, so the download cannot drift from the
site and cannot break independently of it. The same row builders serve both the
`/download/` views and `manage.py export_capability_framework`, and a test holds
the two byte-identical — so a file exported for a migration check is exactly
what a reader downloads.

This is a deliberate change from the previous arrangement. The live service's
copies had drifted and now answer `AccessDenied`: at the time of writing,
`https://ddat-capability-framework.service.gov.uk/download` returns **403**. The
old download page is broken, which is worth knowing when comparing.

Served at:

| Path | Attachment title |
| --- | --- |
| `/download/roles.csv` | Role content - Capability Framework (CSV) |
| `/download/skills.csv` | Skill description content - Capability Framework (CSV) |
| `/download/changelog.csv` | Change notes - Changelog - Capability Framework (CSV) |

Presented on `/download/` as GOV.UK attachment components (`gem-c-attachment`
from Publishing Components), with file type and size.

## roles.csv

One row per role, level and skill combination — so a role with four levels and
ten skills each contributes forty rows. Roles are ordered by title.

| # | Column | Contents |
| --- | --- | --- |
| 1 | `Role Family` | The family the role is grouped under |
| 2 | `Role` | Role title |
| 3 | `Role Description` | The role's body, as plain text |
| 4 | `Role Level` | The level's title |
| 5 | `Role Level Description` | The level's description, as plain text |
| 6 | `Skill Name` | Skill title |
| 7 | `Skill Description` | The skill's body, as plain text |
| 8 | `Skill Level` | The proficiency level this role needs at this level |
| 9 | `Skill Level Description` | The level's bullet points, as text |
| 10 | `Role Type` | `Senior Civil Service` for SCS roles; empty otherwise |

Two special cases, both matching the published export rather than being invented
here:

- **Senior Civil Service roles** have no proficiency ladder. Columns 4, 5, 8 and
  9 carry the literal `NOT IN USE`, column 10 carries `Senior Civil Service`,
  and the skill description has the leadership examples appended under their
  heading.
- **A role with no levels** emits a single row carrying only columns 1 to 3, the
  rest empty.

## skills.csv

One row per skill, ordered by title.

| # | Column | Contents |
| --- | --- | --- |
| 1 | `Skill Name` | Skill title |
| 2 | `Skill Description` | The skill's body as plain text, with leadership examples appended where the skill has them |
| 3 | `Awareness` | The bullet points for that proficiency level |
| 4 | `Working` | As above |
| 5 | `Practitioner` | As above |
| 6 | `Expert` | As above |
| 7 | `Roles that require Skill` | Comma-separated role titles |

Column 7 is derived from the content model rather than stored, so it cannot
fall out of step with the role pages.

## changelog.csv

One row per **live** changelog entry. Unpublished entries are excluded.

| # | Column | Contents |
| --- | --- | --- |
| 1 | `Timestamp` | The entry's date, ISO 8601, or empty |
| 2 | `Page` | The linked role or skill title; `Homepage` for a site-wide entry |
| 3 | `Change note` | The note as plain text |

## Known data quality issues in the source

Raised on CS32-3313 on 10 August 2026 and **still undecided**. The import
reproduces the published exports faithfully, which means it reproduces these
too. Correcting them is a content decision, not a code one:

| Issue | Count |
| --- | --- |
| Duplicate rows | 13 |
| Empty change notes | 3 |
| Skills with no description | 35 |
| Business relationship manager skill levels where the CSV contradicts the live website | 2 |

Someone needs to say whether the Wagtail download should reproduce these or
correct them. Until then it reproduces them, because silently differing from the
published data is the worse failure.

## Outstanding

- [ ] Compare the schemas above against Antony's specification document and
      confirm or correct. CS32-3313.
- [ ] Decide what to do about the four data quality issues above. Open since
      10 August 2026.
- [x] Full framework downloadable as CSV, comparable to the previous download.
- [x] Accessible from a signposted page (`/download/`).
- [x] Attachments follow the GOV.UK attachment component.
- [x] Content kept in sync with published framework content — by construction.
