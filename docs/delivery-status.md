# Delivery status

Every Jira ticket on the Capability Framework migration, what the evidence for
it is, and what is genuinely left. Written 31 August 2026 after reading all 24
tickets and all 62 comments.

It exists because the tickets no longer describe the build accurately. Several
carry comments that were true when written and are not now, and six of them cite
a pull request that has since been closed. Where this document and a Jira
comment disagree, this document was written later.

## Where the work actually stands

Almost nothing is waiting on code. The build is on the `capability-framework`
branch, open as draft pull request #40, at 770 tests passing with `ruff` clean
and a single linear migration leaf.

What is left divides into three:

1. **Deploy what is already built.** Two migrations and seventeen commits are on
   the branch and on no running instance.
2. **Decisions and answers.** Nine questions are open on the tickets. Five are
   ours to chase; four are ours to answer.
3. **Things only other people can do.** A penetration test, a platform
   configuration change, an independent accessibility audit, sign-offs.

## The deployment gap

The dev instance runs build 110 (`bf51bb6`, 26 August 2026), whose migrations
stop at `0064`. The branch adds two more:

| Migration | What it does | Consequence of it not being deployed |
| --- | --- | --- |
| `0065_editor_snippet_permissions` | Grants Editors and Moderators the `govuk.*` snippet permissions | Real editors cannot manage roles, skills or changelog entries. Masked on dev because five of eight accounts are superusers. CS32-3457. |
| `0066_contentpage_body_blocks` | Adds the table block to content pages | Editors cannot add a table. CS32-3527. |

Also undeployed: Wagtail 7.4.3, which matters because 7.4.1 carries fifteen
published advisories — see [security.md](security.md).

Until an image bump lands, **anyone re-testing dev is testing something older
than the branch.** Two people have been asked to re-test.

## Ticket by ticket

Legend: **Built** — the code exists and is tested. **Deployed** — it is running
somewhere. **Closed** — the ticket's criteria are met including the non-code
ones.

### CS32-1579 — Put in place redirects

Built and deployed. `manage.py seed_live_service_redirects` seeds every
`/role/<slug>` and `/skill/<slug>` as a site-scoped 301; 237 redirects against
the real content, and every public URL the live service publishes resolves.
Runbook in [cutover.md](cutover.md).

**Left:** the GOV.UK-side redirects Antony listed on 3 April 2025 — the
Collections page (26 linking pages) and the `www.gov.uk/guidance/*` role URLs.
Those are GOV.UK publishing changes, not changes to this service, and no owner
has been named. Also note the ticket assumed "a final mapping of old URLs will
be provided before implementation"; it never was, so the mapping was derived
from the live service's own URLs.

### CS32-3307 — GDD Site Security

Built and deployed. Full posture, verified against the running instance on
31 August 2026, in [security.md](security.md).

**Left:** an independent penetration test or vulnerability scan. Not booked,
flagged on 27 August, no reply. It has commissioning lead time and then
remediation time, and it is the most likely single cause of a delayed launch.

### CS32-3308 — Accessibility Components

Built and deployed. Design System throughout, deviations recorded, automated
regression guard in `test_accessibility.py`, axe clean across 14 page types on
26 August, statement page published. Details in
[accessibility.md](accessibility.md).

**Left:** an independent audit, and replacing the statement's inherited wording
— it currently declares four non-compliances that do not exist in this build,
which is a misstatement in a document with legal weight under PSBAR 2018.

**Note:** this ticket sits in Needs Review with no comment recording any of the
August evidence. The evidence is on CS32-3526 instead. Anyone reviewing this
ticket will find its most recent comment says the accessibility statement is a
404, which stopped being true.

### CS32-3309 — Open Source Code

Repository is public. Licensing is now complete: `LICENCE` (MIT for code),
the Open Government Licence note for content in the README, and
`CONTRIBUTING.md`.

Closed this session: **`SECURITY.md`** now exists, and the **secret sweep of
history** has been run — all 289 commits and 1,767 text blobs, clean, with only
three literal test passwords in four test files. Recorded in
[security.md](security.md).

**Left:** none of it in code. The licence and these files are on the branch and
not on `main`, so the repository a member of the public sees does not carry them
until #40 merges.

### CS32-3310 — Role Page Template

Built and deployed. All six of Antony's round-1 items fixed and confirmed on dev
on 27 August.

**Left:** Antony's round 2, and the Product Lead and Content Lead approval the
ticket requires. Neither is a code task.

### CS32-3311 — Skill Page Template

Built and deployed. Both of Tim's issues fixed.

**Left:** Product and Content Lead approval, and **Tim still has no admin
access** — asked for on 13 August, promised as a separate ticket on 27 August,
and no such ticket exists. That promise was dropped and needs picking up.

### CS32-3312 — Changelog

Built and deployed. All four of Tim's points fixed: skill entries render on the
A to Z, imported bullets repaired on import, the home page has the collapsible
"show all updates", and entry and date type sizes match.

**Left:** Tim's re-test and the required approvals.

### CS32-3313 — Framework Download

Built and deployed, and **further along than the ticket says**. The 27 August
comment states the GOV.UK attachment component still needs a design call; it is
done — `/download/` serves all three CSVs as `gem-c-attachment` components.
Column schemas are now written up in [downloads.md](downloads.md) so they can be
compared against the specification without reading Python.

**Left:** the column comparison itself, and a decision on the four source data
quality issues raised on 10 August — 13 duplicate rows, 3 empty change notes,
35 skills with no description, 2 contradictory Business relationship manager
levels. Unanswered for three weeks.

**Note:** the live service's own `/download` returns 403, so this cannot be
compared against live.

### CS32-3314 — Cookies

Closed as not needed, because analytics is server-side. That is correct **while
it stays server-side**. See [analytics.md](analytics.md): choosing a client-side
tool on CS32-3315 reopens this ticket and adds a consent banner to the build.
The dependency was not written down anywhere until now.

### CS32-3315 — Analytics (AWS server side)

Not started, and correctly so — the approach has never been decided. Options,
a recommendation and the decisions needed are in [analytics.md](analytics.md).

**Left:** agree the KPIs. That is the blocking item; without them the choice
cannot be made on any principled basis.

### CS32-3316 — SCS role content object

Built and deployed. Both of Antony's items fixed. The "indicative SCS grades are
modelled but empty" caveat from 10 August **is now resolved** — the chief
technology officer page shows SCS 1 and SCS 2.

**Left:** a design question asked on 10 August and never answered — SCS uses the
role content type with a flag rather than a separate content type. It ships as
built unless someone says otherwise.

### CS32-3334 — Set up GDD instance on Wagtail

Dev is up through bootstrap step 3.

**Left:** staging and production do not exist. This blocks CS32-3338's "migrate
to staging and verify" and all of CS32-3343.

### CS32-3335 — Embed accessibility and analytics

Closed as a duplicate of CS32-3308. Nothing to do.

### CS32-3338 — Execute migration

Scripts built, idempotent, and verified. The "20 links point at pages that do
not exist" concern from 10 August **is resolved**: `/job-grades`, `/roadmap` and
`/download` all resolve, and where a page is genuinely absent the template falls
back to plain text rather than emitting a dead link
(`_page_url_by_slug`, `govuk/models.py`).

**Left, and this one is new:** the migrated content is now **stale against the
live service**. Two roles the live service publishes have no page on dev at all
— `agile-coach` and `data-and-artificial-intelligence-ai-ethicist`. Cutting over
without a content refresh would ship a framework missing a role and two live
URLs answering 404. The refresh procedure is now written up as a section of
[cutover.md](cutover.md).

Also left: hosting to migrate into, Content Lead sign-off on no content loss,
and UR/Product sign-off on journeys.

### CS32-3341 — Accessibility testing

Automated scanning done. **Keyboard-only, colour contrast and accessible-name
testing were run on 31 August 2026** across nine page types — 743 tab stops, no
traps, zero stops without a visible focus indicator, zero contrast violations,
zero unnamed controls. Recorded in [accessibility.md](accessibility.md).

**Left:** the screen reader pass and other assistive technology, which need a
person; the independent audit; and an answer to Honor's question from 29 July
and 6 August about whether the audit must precede migration. That question
decides whether this ticket blocks cutover, and it has been open for five weeks.

### CS32-3342 — User journey testing

Nothing was mapped, which is what Honor asked about on 29 July. **Ten journeys
are now written up** in [user-journeys.md](user-journeys.md), each mapped to the
ticket it exercises, including the editor journey and the accessibility
conditions.

**Left:** somebody running them, and sign-off from Product and UR.

### CS32-3343 — Cutover to Wagtail

The plan is [cutover.md](cutover.md), which the ticket does not mention. It now
covers the content refresh as well.

**Left:** a rehearsal, which the acceptance criteria require and which cannot
happen without staging; the DNS change; and the communications.

### CS32-3344 — Decommission Strapi

Nothing had been started and there were no comments. A plan now exists at
[decommission.md](decommission.md), including the point that matters most —
**do not switch Strapi off at cutover**, because while it runs, rollback is a
DNS change.

**Left:** agreeing it with Shehzad, and setting the rollback window before
cutover rather than after.

### CS32-3457 — Editorial workflow

Built. `0065_editor_snippet_permissions` grants the snippet permissions;
[editorial-workflow.md](editorial-workflow.md) documents the process.

**Left:** deploy it, then test as a real Editor rather than a superuser
(journey J10). And a decision on whether to enable a submit-for-review
moderation workflow.

### CS32-3458 — Implement search

Built and deployed. Pagination, skills as results, exact-title ranking, dates
from each skill's changelog, and a ranking floor so a search for words the site
does not hold returns nothing.

**Left:** the point-by-point reply to Antony's search specification, promised on
27 August and never posted. The ticket also still says the header search box is
"waiting on a code review" — that was PR #34, which is now closed and whose work
is in the branch. As written, the ticket describes a blocker that no longer
exists.

### CS32-3459 — Create landing page template

Built and deployed. Home is a `ContentPage` with an editor-managed
`framework_welcome_body` StreamField, the roles grouped by family, and the
collapsible updates section.

**Left:** nothing identified in code. The ticket has had no comment since
10 August and should be moved on.

### CS32-3484 — Alerting and monitoring

The ticket had no description and no comments. A proposal now exists at
[monitoring.md](monitoring.md): nine candidate alerts with thresholds, and the
four decisions needed first.

**Left:** the blocking one is that there is **no alert destination** — no topic,
no list, no channel, nobody on the end. That is an organisational decision, and
until it is made, building alarms would create the appearance of monitoring
without the substance.

### CS32-3526 — Header, footer and navigation

Four of Antony's six items confirmed working on dev: the GOV.UK header link,
the site name in the service navigation, breadcrumbs on small screens, and
"Back to top".

**Left:**

- **T24** — the focus state is done; how much larger the side-menu text should
  be is an open question to Antony from 27 August.
- **T25** — the `/security*` platform redirect. Fully diagnosed and reproducible
  (see [security.md](security.md)); not fixable from this repository; needs an
  owner for `wagtail-iac`. **Go-live blocker.**
- The unchecked "left-hand navigation — menu items" box. Worth being straight
  about: that menu is derived from role content, so adding a role adds an item,
  but there is no menu editor and one is not planned. It should be agreed as
  built or raised as new work rather than left ticked-looking.

### CS32-3527 — 'simple' content page template

Every box except tables was already met. **Tables are now built too** —
`ContentPage.body_blocks` with `GovukTableBlock`, migration `0066`.

The 27 August comment on this ticket says tables are "a bigger job than it
appears" and proposes deferring them until after go-live. That is now wrong and
is steering Antony away from something he can have. It needs correcting.

**Left:** deploy, then test.

## The open questions, in one place

Ours to chase:

| Ticket | Since | Question |
| --- | --- | --- |
| CS32-3313 | 10 Aug | Reproduce or correct the four source data quality issues? |
| CS32-3316 | 10 Aug | SCS as a flag on the role type, or a separate content type? |
| CS32-3526 | 27 Aug | How much larger should the side-menu text be? |
| CS32-3526 | 27 Aug | Who owns `wagtail-iac` so the `/security*` rule can be narrowed? |
| CS32-3315 | — | What are the KPIs? |

Ours to answer:

| Ticket | Since | Question |
| --- | --- | --- |
| CS32-3341 | 29 Jul | What is the accessibility audit process, and must it precede migration? |
| CS32-3342 | 29 Jul | Are there user journeys mapped? — **now answered by [user-journeys.md](user-journeys.md)** |
| CS32-3311 | 13 Aug | Tim's admin access on dev. Promised as a separate ticket; never raised. |
| CS32-3458 | 27 Aug | The point-by-point reply to the search specification. |

## Jira hygiene

Six tickets — CS32-3310, 3311, 3312, 3313, 3316 and 3338 — say "Implemented in
PR #35". **#35 is closed.** So are #34 (cited on CS32-3458), #38 and #39 (cited
on CS32-3307). The work from all of them is in the branch behind #40, but a
reviewer following those links sees a closed pull request and no merge.

Three comments are now factually wrong and would mislead: CS32-3527 on tables,
CS32-3313 on the attachment component, CS32-3458 on the header search review.

CS32-3308 needs an evidence comment; its most recent one describes a state from
three weeks ago.
