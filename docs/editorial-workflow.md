# Editorial workflow

How content gets from a draft to a published page, who is allowed to do what,
and where the record of it is kept. This backs Jira CS32-3457.

## Getting access

The admin is behind single sign-on. `AdminOIDCLoginMiddleware` redirects any
unauthenticated request to `/admin/` or `/django-admin/` straight to the OIDC
provider, so there is no local password form to reach — a request to `/admin/`
answers `302` to the provider, not `200` with a sign-in page.

Signing in successfully creates the account, but **it does not grant any
permissions**. A new account belongs to no group and can do nothing until
someone with administrator rights adds it to one at `/admin/users/`. This is
two steps, and the second is easy to forget: an editor who says "I can sign in
but there is nothing there" is almost always an account that has never been put
in a group.

Accounts are per instance. Access granted on one instance says nothing about
another, so a new production instance starts with an empty user list and every
editor has to be added again.

## Who can do what

Two groups, which is Wagtail's standard split:

| Group | Can | Cannot |
| --- | --- | --- |
| **Editors** | Create pages, edit pages, save drafts, submit for moderation | Publish, unpublish, delete |
| **Moderators** | Everything Editors can, plus approve, publish, unpublish and delete | Manage users, groups or settings |

Administrator (superuser) is a third level and sits outside the groups. It
carries everything, including user management, group permissions and the Django
admin. Give it to the people who administer the site, not to the people who
write for it — an account that only needs to publish should be in Moderators,
not an administrator. Being an administrator also makes the two groups
redundant: a superuser passes every permission check whether or not they are a
member.

Review the user list before launch and again periodically. The things to look
for are accounts with more rights than the person needs, accounts belonging to
people who have moved on, and the same person holding two accounts because they
signed in through two different identities.

## The things you will actually do

The common jobs, and where each one is done. Everything here assumes you are
signed in and in a group; see **Getting access** above if the admin looks empty.

**Change the words on a role.** Snippets → Roles → the role. Edit, **Save
draft**, then **Submit for moderation**. A moderator approves it and that
publishes it. Nothing reaches the public until that approval.

**Change a skill, or what a skill says at a level.** Snippets → Skills → the
skill. Same submit-and-approve route. A skill appears on every role that
requires it, so a wording change lands in several places at once — worth
previewing before you submit.

**Change which skills a role requires.** Edit the role, not the skill. The
requirements are chooser references held on the role.

**Add a change note.** Snippets → Change notes. Attach it to a role or a skill
to have it appear in that page's **Updates** section, or leave both blank for a
site-wide note on the framework home page. These publish as soon as you save —
there is no review step — so re-read before saving.

**Edit one of the supporting pages** (Job grades, Propose a change, Roadmap,
Context and challenges, the privacy or accessibility statements). Pages → find
it in the tree → edit → **Submit for moderation**.

**Add a new page.** Choose the type deliberately, because it decides whether
the page joins the side menu and it cannot be changed afterwards:
- **Framework content page** — appears in **Further resources** in the side
  menu. Use it for anything about the framework itself.
- **Content page** — does not. Use it for the privacy notice, cookie
  statement, accessibility statement and anything else that sits outside the
  framework.

**Reorder Further resources, or hide a page from it.** Capability framework →
Sidebar settings. Add pages in the order you want; untick to hide. A framework
page you do not list still appears, after the ones you did — so nothing vanishes
because it was forgotten.

**Change a footer link.** Settings → Footer. Takes effect immediately, with no
review.

**Change the phase banner.** Settings → Phase banner.

**Change what a visitor sees when a page is missing.** Settings → Error pages.

**Close the site for planned work.** Settings → Maintenance mode. Editors still
get through; the public sees the 503 page. Remember to turn it off.

**See what visitors think.** Reports → Page usefulness, which counts the yes and
no answers to "Is this page useful?" by page, with a date filter and a CSV or
Excel export.

**See what changed and who changed it.** Reports → Site history, or the History
tab on the item itself, which is usually faster.

**Undo a bad edit.** History tab → preview an earlier revision → restore it.
Every save is a revision, so this is the normal way to fix a mistake, not
retyping.

**Get the content out as a spreadsheet.** The CSV downloads on the Download page
are generated from the CMS when they are requested, so they are never out of
date and there is nothing to regenerate after publishing.

Two things to be careful with, both covered in full below: **deleting a skill**,
which silently removes it from every role that requires it, and the **JSON
import**, which replaces a role's or skill's change notes rather than adding to
them.

## Publishing a page

The site has one moderation workflow, **Moderators approval**, applied to the
whole page tree. It has a single step: a group approval task assigned to
Moderators.

1. An editor makes a change and saves it. Saving creates a revision; the live
   page is untouched.
2. The editor selects **Submit for moderation**. The page enters the workflow
   and shows as awaiting approval.
3. A moderator reviews it — the preview and the comparison against the current
   live version are both on the page's edit screen — and either approves it,
   which publishes it, or requests changes with a comment, which sends it back.

A moderator editing a page can publish directly without going through the
workflow. That is intended, and it is what makes an urgent correction possible,
but it means the workflow records what was reviewed rather than everything that
was published. The audit log below records both.

**Scheduling is not offered.** There is no go-live or expiry date on a page's
edit screen, and no "Edit schedule" toggle in the status side panel. That is
deliberate. Wagtail only acts on those dates when `manage.py publish_scheduled`
runs on a timer, and nothing on the deployed instances runs it — the container's
command is `migrate` followed by `gunicorn` and there is no scheduler alongside
it. Offering the field would mean accepting a date the service cannot honour:
the page would sit as an approved draft, never go live, and tell nobody. So
`page_settings_panels()` removes the publishing panel instead.

Publish at the time you want the change to appear. The day a scheduled task
exists, `SCHEDULED_PUBLISHING=true` puts the panel back and nothing else has to
change.

**Reverting.** Every save is a revision. The page's History tab lists them and
any one can be previewed and restored, so a bad edit is undone by republishing
an earlier revision rather than by retyping.

## Content that is not a page

Some of what the site publishes lives in snippets rather than in the page tree,
under **Snippets** in the admin. Roles, skills and change notes are the main
ones.

**Roles and skills now go through the same review as pages.** They carry
drafts, revisions, locking and the **Moderators approval** workflow, so editing
a skill is submit-and-approve, not save-and-live. A save creates a revision and
leaves the published version alone; **Submit for moderation** sends it for
review; a moderator approves it and that publishes it. A moderator can publish
directly, as on a page.

This changed recently. If you were told that a change to a skill is live the
moment you save it, that is no longer true, and it is the difference most
likely to catch someone out: an edit that looks finished may simply be waiting
for approval. The snippet listing shows the state, and the History tab lists
every revision so a bad edit is undone by restoring an earlier one.

Scheduling is not offered on roles and skills, for the same reason it is not
offered on pages: nothing in the deployment runs `publish_scheduled`, so a
go-live date would be a date the service cannot honour. The fields exist on the
model but no panel exposes them.

**Change notes are not in the workflow.** They have no draft state; a change
note is live as soon as it is saved, and its `live` tick is how you keep one out
of the published output while you work on it.

Deleting a snippet is the one genuinely destructive action in the CMS, because
of what else refers to it. A role stores its skill requirements as chooser
references to skill snippets, so deleting a skill removes it from every role
that requires it, silently, in one action. Change notes attached to that skill
are deleted with it. Before deleting a skill, open its usage listing and check
what refers to it; if the intention is to withdraw it rather than erase it,
change the content instead of deleting the record.

## The role side menu

Every framework page carries the side menu: the roles grouped by family, then
**Further resources**, the handful of pages about the framework itself. What
goes in that last group is decided by **page type**, not by a tick box:

- A **Framework content page** is listed. Create one under the framework home
  page for anything that belongs beside the roles: the live service lists
  Skills A to Z, Propose a change, Download, Job grades, Context and challenges
  and Roadmap.
- A **Content page** is not. Use it for the pages that are not about the
  framework — the privacy notice, the cookie statement, the accessibility
  statement — which live under the same home page but stay out of the menu.

A page's type is fixed when it is created, so choose it then. To change the
**order** of the group, or to hide a framework page without unpublishing it,
use **Capability framework → Sidebar settings**: add the pages in the order you
want and untick any to hide. A framework page not listed there is still shown,
after the listed ones, so nothing disappears just because it was not added.

## Settings, which are neither

Some of what the site shows is neither a page nor a snippet but a site
setting, held under `/admin/settings/`. They are per site, they take effect
immediately, they are not versioned, and they are not covered by moderation —
one person, one save, live everywhere on the site. Treat them accordingly.

| Setting | What it holds |
| --- | --- |
| **Customise** | Header logo, where the service name, sign-in and search box sit, the search placeholder, whether search offers a free-text results page, the "Is this page useful?" prompt, the back-to-top link, maximum content width, extra CSS |
| **Footer** | The footer support links |
| **Phase banner** | Whether the banner shows, and its wording |
| **Error pages** | The wording of the page not found, forbidden and problem pages, and the contact they offer |
| **Maintenance mode** | The switch that closes the site behind the 503 page, and that page's wording |
| **Capability framework → Sidebar settings** | The order of Further resources, and which framework pages are hidden |
| **Capability framework → Wording** | The framework's repeated headings and labels |

**Maintenance mode deserves care.** Turning it on closes the whole site to the
public behind the "Sorry, the service is unavailable" page. Signed-in people
with editor access still get through, which is what makes it useful for planned
work — but it means the person who turned it on may not notice it is still on.
The health check stays open either way. There is also a `MAINTENANCE_MODE`
environment variable, which is a harder close that shuts out everyone including
editors; that one is for incidents and is changed by the platform team, not from
the CMS.

They also do not travel in the content export, so they have to be re-entered by
hand on a new instance. See [cutover.md](cutover.md).

## The published downloads

The CSV downloads are generated from the CMS at the moment they are requested,
not from files uploaded alongside the content. Publishing a content change is
therefore the whole job — there is no second step to regenerate a download, and
no way for the download to fall behind what the pages say.

## Bulk changes

For anything too large to do by hand there is the JSON import and export at
`/admin/pages/import-export/`. It is the same mechanism used to move content
between instances, described in [cutover.md](cutover.md).

Two things to know before using it on a live site: it runs outside the
moderation workflow, and for pages it **adds and updates but never deletes**.
An import cannot be used to remove a page, and a file that omits a page leaves
that page exactly as it was.

Changelog entries are the exception, and the difference matters. An entry has
no identifier of its own, so the importer cannot match one to another: it
deletes the entries belonging to a role, a skill or the framework and writes
the file's set in their place. A file carrying one new entry for a role
therefore leaves that role with one entry, not with its previous entries plus
one. It also needs delete permission on the changelog, and is reported and
skipped rather than refused if you do not have it.

Skipping the workflow does not mean skipping permissions. The import publishes
only what the person running it could have published by hand: an editor's
import lands as drafts, and the pages stay invisible to the public until a
moderator publishes them. The audit log records it either way — an editor's
import shows as `wagtail.create`, a moderator's adds `wagtail.publish`, both
against the account that uploaded the file.

## The audit trail

Every publish, unpublish, edit, move, delete, workflow submission and workflow
approval is logged. The record is at **Reports → Site history**
(`/admin/reports/site-history/`) and shows the action, the page or snippet, the
user who did it and the timestamp. It can be filtered by action, by date and by
user.

Individual pages carry their own slice of the same log on their History tab,
which is usually the faster route when the question is "what happened to this
page".

The log is written by Wagtail itself rather than by anything in this codebase,
so it cannot be bypassed by editing through a different screen. It records the
CMS account that acted; it does not record anything that changed the database
directly, such as a management command run against the container.
