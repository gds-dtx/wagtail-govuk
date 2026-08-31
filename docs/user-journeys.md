# User journeys

The journeys to test before and after cutover, written so somebody who has not
worked on the build can run them. It backs Jira CS32-3342, which asks for
"testing completed across user journeys" and has been waiting since 29 July 2026
on the question "do we have test scripts or journeys mapped out?".

The answer was no. This is them.

## How to use this

Run each journey end to end on the instance under test, then on the live service
for comparison where the journey exists there. Record pass, fail or difference
against the step, not against the journey — a journey that fails at step 4 has
still told you steps 1 to 3 work.

Run at least one full pass by keyboard only, and one at 320px width or 400%
zoom. Those two conditions find most of what a desktop mouse pass misses.

`<host>` below is the instance under test. On dev today that is
`https://gds-capframework-001.dev.wagtail.ukps.digital`.

## The journeys

### J1 — I want to know what a role does

The primary journey. Most traffic to this service is one person looking up one
role.

1. Land on `/`.
2. Find the role list, grouped by family.
3. Choose a role — say **Data scientist**.
4. Read the description and responsibilities.
5. See the skills for that role, with the level required at each grade.
6. Follow a skill link through to the skill.

**Expect:** the role page carries description, responsibilities, the skills
table with level indicators, the entry routes, and the related roles. Skill
links jump to that skill's entry on the A to Z. The side navigation lists every
role and marks the current one.

**Watch for:** the skills table at narrow widths — it scrolls inside its own
region rather than pushing the page sideways.

### J2 — I arrive from a bookmark or a search engine

The journey that only exists because of the migration, and the one that fails
silently if the redirects are wrong.

1. Go to `<host>/role/data-scientist` — the **old** URL scheme.
2. Go to `<host>/skill/communicating-information`.

**Expect:** each 301s to the equivalent page on the new site. Not a 404, and not
a 302.

**Watch for:** `/role/security-architect`. It currently redirects correctly to
`/security-architect/` and is then taken off-site by a platform rule — see
[security.md](security.md). This is a known blocker, not a new finding.

### J3 — I want to find a skill by name

1. Land on `/`.
2. Reach the Skills A to Z (`/skills/`).
3. Find a skill alphabetically.
4. Open it and read its levels.
5. See which roles require it, and follow one through.

**Expect:** every skill is present, each opens to show its four proficiency
levels, and the roles listed link to real role pages. Senior Civil Service
skills show leadership examples instead of a level ladder — that is correct, not
a defect.

**Watch for:** getting back out. The A to Z is long; the breadcrumb and the
service navigation are the routes back.

### J4 — I want to search

1. Use the search box in the header from any page.
2. Search a role name — `data scientist`.
3. Search a skill name — `user research`.
4. Search a phrase in body text — `entry routes`.
5. Search something the site does not hold — `zzzznothing`.
6. Page through a long result set.

**Expect:** results link straight to the matching content and carry enough
metadata to tell entries apart. A page named exactly as searched ranks above one
that merely mentions the phrase. The nonsense search returns nothing, with a
message — not everything on the site.

**Watch for:** search is reachable from every page, including 404s.

### J5 — I want the whole framework as data

1. Reach `/download/` from the navigation, not by typing the URL.
2. Download each of the three CSVs.
3. Open them in a spreadsheet.

**Expect:** three files — roles, skills, change notes — presented as GOV.UK
attachment components with file type and size. They are generated from published
content when asked for, so they cannot go stale. Column schemas are in
[downloads.md](downloads.md).

**Note:** the live service's `/download` currently returns **403**, so this
journey cannot be compared against live. It can only be checked against the
specification.

### J6 — I want to know what changed

1. On `/`, find the updates section and expand "show all updates".
2. Open a role page and find its Updates section.
3. Open the A to Z and find a skill with its own change note.

**Expect:** entries are newest first, with a date and a note. Site-wide entries
appear on the home page, role entries on that role, skill entries in that
skill's section of the A to Z. Bullets render as bullets, not dashes.

### J7 — I am senior and want to know where this leads

1. Open a Senior Civil Service role — **Chief technology officer**.
2. Read the skills, which have leadership examples rather than levels.
3. Find the indicative Civil Service grades.
4. Follow the link to the context and challenges page.
5. From a non-SCS role, find the section naming which roles lead to an SCS role.

**Expect:** SCS roles show no proficiency ladder, do show leadership examples,
and name their grades (SCS 1, SCS 2). The "roles that could lead here" section
matches the live service.

### J8 — I want to suggest a change

1. Reach `/propose-a-change/` from the navigation.
2. Follow the route it describes.

**Expect:** the page renders as a content page with working links, including any
`mailto:`.

### J9 — I use a keyboard, a screen reader or magnification

Run J1 and J4 again under each condition:

1. Keyboard only, no mouse. Tab from the top.
2. At 320px width, and at 400% zoom.
3. With a screen reader — VoiceOver, NVDA or JAWS.

**Expect:** the first tab stop is "Skip to main content" and it works. Every
interactive element is reachable and shows a visible focus indicator. Nothing
traps focus. At narrow widths the role side navigation gives way to the
breadcrumb and the in-page role list, and no page scrolls sideways.

**Already evidenced:** the keyboard and contrast halves of this were machine
checked across nine page types on 31 August 2026 — see
[accessibility.md](accessibility.md). **The screen reader pass has not been
done** and needs a person.

### J10 — I edit the content

Not a public journey, but it is a journey, and it is the one that decides
whether the content team can run the service after cutover.

1. Sign in to `/admin/` as an **Editor**, not as a superuser.
2. Edit a role's description; save as draft; preview; publish.
3. Add a changelog entry against a skill.
4. Create a content page, add a heading, a list and a table, publish it.
5. Unpublish something, then restore it from the revision history.
6. Find who changed what at `/admin/reports/site-history/`.

**Expect:** all of it without a developer. Steps 3 and 4 depend on migration
`0065` and the content-page table block, neither of which is deployed yet — see
[editorial-workflow.md](editorial-workflow.md).

**Watch for:** testing this as a superuser proves nothing. Five of the eight
accounts on dev are superusers, which is exactly how a permissions gap stays
hidden.

## Coverage against the tickets

| Journey | Tickets it exercises |
| --- | --- |
| J1 | CS32-3310 |
| J2 | CS32-1579, CS32-3338 |
| J3 | CS32-3311 |
| J4 | CS32-3458 |
| J5 | CS32-3313 |
| J6 | CS32-3312 |
| J7 | CS32-3316 |
| J8 | CS32-3527 |
| J9 | CS32-3308, CS32-3341 |
| J10 | CS32-3457 |

## Outstanding

- [ ] A full pass by User Research and the content team, recorded against these
      steps. CS32-3342 needs "sign-off from Product + UR".
- [ ] Screen reader pass (J9.3). CS32-3341.
- [ ] Re-run J10 once `0065` and the table block are deployed.
- [x] Journeys written down so the testing can start. Previously the blocker.
