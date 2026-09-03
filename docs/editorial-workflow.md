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

Snippets are **not covered by the moderation workflow**. A change to a skill is
live as soon as it is saved. Where a snippet has a `live` flag — change notes do
— unticking it is how you keep something out of the published output while you
work on it.

Deleting a snippet is the one genuinely destructive action in the CMS, because
of what else refers to it. A role stores its skill requirements as chooser
references to skill snippets, so deleting a skill removes it from every role
that requires it, silently, in one action. Change notes attached to that skill
are deleted with it. Before deleting a skill, open its usage listing and check
what refers to it; if the intention is to withdraw it rather than erase it,
change the content instead of deleting the record.

## Settings, which are neither

The footer links, the phase banner, the search placeholder and the error-page
contact are site settings, held under `/admin/settings/`. They are per site,
they take effect immediately, they are not versioned, and they are not covered
by moderation — one person, one save, live everywhere on the site.

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
moderation workflow, and it **adds and updates but never deletes**. An import
cannot be used to remove a page, and a file that omits a page leaves that page
exactly as it was.

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
