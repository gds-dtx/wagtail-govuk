# Decommissioning the old service

What has to happen to the Strapi service after the Wagtail one takes over. It
backs Jira CS32-3344, which had no comments and nothing started when this was
written.

This is a plan to agree, not a plan already agreed. CS32-3344's first acceptance
criterion is "decommission plan agreed with Shehzad", and that conversation has
not happened.

## Do not start this at cutover

The single most important thing here: **switching DNS is not the moment to turn
the old service off.** For as long as the old service is still running and still
holding its content, rollback is pointing DNS back. The moment it is off, the
only way back is a restore.

So the old service stays up, untouched and serving nothing, for an agreed
period after cutover. Decide the length of that period *before* cutover and
write it into the change record, because deciding it afterwards means deciding
it under pressure. Two weeks is a reasonable default for a service of this size:
long enough for a problem that only appears under real traffic to appear, short
enough that the two systems do not drift.

During that window the old service must not be edited. Content written in Strapi
after cutover would be invisible on the new site and would be lost when Strapi
goes. Withdraw editor access at cutover, not at decommission — see step 2.

## Sequence

### 1. Before cutover — capture the archive

Take the archive while the source system is running and healthy. An archive
taken from a system already being torn down is an archive taken in a hurry.

- [ ] Full database dump of the Strapi instance.
- [ ] The uploaded media directory, in full.
- [ ] The three published CSVs as the old service last emitted them. Note that
      `/download` currently returns 403, so these may have to come from the
      database rather than the download page.
- [ ] A crawl of the public site — HTML for every URL — so there is a record of
      exactly what was being served on the last day. `all_urls.json` from the
      migration working directory is the URL list to crawl.
- [ ] The Strapi configuration and any environment variables that are not
      secrets, so the shape of the old system is recoverable.

Store these where the service owner, not the delivery team, controls access.
Record what retention applies: this is the point at which "data archived where
required" either has an answer or does not.

### 2. At cutover — close the door

- [ ] Withdraw editor access to Strapi. Everyone who was editing there is now
      editing in Wagtail, and an edit in the wrong place is silently lost work.
- [ ] Leave the service running and reachable to the team, but not linked from
      anywhere.
- [ ] Confirm nothing else consumes it. The published CSVs were the obvious
      integration point; check for anything else pulling from the Strapi API
      before assuming there are no consumers.

### 3. After the agreed window — verify before switching off

- [ ] Confirm the Wagtail service has been serving the real domain, without
      incident, for the whole window.
- [ ] Confirm every old URL still resolves through the redirects, from a client
      that has never visited either host.
- [ ] Confirm the archive from step 1 is readable — actually open it, do not
      just confirm the file exists.
- [ ] Get explicit sign-off that rollback is no longer wanted.

### 4. Switch off

- [ ] Stop the Strapi application.
- [ ] Wait, with it stopped but not deleted, for a further short period. This
      catches anything that depended on it in a way nobody knew about.
- [ ] Destroy the infrastructure.
- [ ] Remove DNS records that pointed at it.
- [ ] Revoke credentials, API keys and any service accounts it used.
- [ ] Remove its entries from any monitoring or billing groupings.

### 5. Record it

- [ ] Write down what was switched off, when, by whom, and where the archive
      lives. CS32-3344 asks for "confirmation of decommission documented", and
      the useful version of that document is the one that tells somebody in two
      years where the old content went.

## Decisions needed

1. **How long is the rollback window?** Blocking, and needed before cutover, not
   after.
2. **Who owns the archive, and for how long?** Records retention on published
   government content is not a delivery-team decision.
3. **Are there any API consumers?** If something integrates with Strapi that
   nobody has mentioned, this is when it breaks.
4. **Who signs off the switch-off?** Named person, not a team.

## Outstanding

- [ ] Agree this plan with Shehzad. CS32-3344's first acceptance criterion.
- [ ] Set the rollback window and put it in the change record.
- [ ] Identify API consumers.
- [ ] Everything else above, in sequence, after cutover.
