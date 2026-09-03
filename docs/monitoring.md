# Logging, monitoring and alerting

What the application emits, what is watched today, and the alerts that still
need creating. It backs Jira CS32-3484, and the "logging and monitoring are in
place for security events" criterion that CS32-3307 defers to it.

CS32-3484 had no description and no comments when this was written. This
document is the proposal it was missing: the thresholds below are a starting
position to be agreed, not a decision already taken.

## What the application emits today

Everything goes to stdout as **one JSON object per line**
(`govuk.logging_utils.LoggingJSONFormatter`, wired up in `LOGGING` in
`govuk/settings/base.py`). The container runs under ECS, so stdout lands in
CloudWatch Logs without the application knowing anything about CloudWatch.

Four loggers are configured explicitly — `django`, `django.server`,
`gunicorn.error` and `gunicorn.access` — all at `LOG_LEVEL`, which is
environment-driven.

Two switches add volume deliberately and should stay off in production unless
something is being diagnosed:

| Setting | Effect |
| --- | --- |
| `INCOMING_REQUEST_INFO_LOGGING` | Logs incoming requests, including headers, at INFO |
| `CONTENT_DISCOVERY_REQUEST_INFO_LOGGING` | Logs outbound content-discovery requests, including headers, at INFO |

Headers can carry cookies and authorisation values. Neither should be on
routinely in an environment holding real editor sessions.

Separately from the log stream, Wagtail keeps its own audit trail of every
publish, unpublish, move and delete in the database, readable at
`/admin/reports/site-history/`. That is the record for "who changed this page",
and it is described in [editorial-workflow.md](editorial-workflow.md).

## What is not in place

There are no alarms. Nothing currently pages, emails or posts to a channel when
the service breaks. Log data is being collected and nobody is being told
anything about it, which is the gap CS32-3484 exists to close.

There is also **no ECS deployment circuit breaker and no minimum healthy
percent** set (`wagtail-iac/ecs.tf`), so a task that fails to start sits
failing rather than rolling itself back. Until that changes, deployment health
has to be watched by a person — see the note at the end of
[cutover.md](cutover.md).

## Proposed alerts

Thresholds to agree rather than thresholds already agreed. Each is expressed as
a CloudWatch metric filter over the JSON log stream, or an existing ALB or ECS
metric where one already answers the question.

| # | Condition | Source | Suggested threshold | Why this one |
| --- | --- | --- | --- | --- |
| 1 | 5xx responses | ALB `HTTPCode_Target_5XX_Count` | > 5 in 5 minutes | The service is erroring for real users. Below this, a single unlucky request should not wake anybody. |
| 2 | Sustained 5xx | ALB, same metric | > 0 for 15 minutes | Catches a slow steady failure that never crosses the burst threshold. |
| 3 | Failed admin sign-ins | metric filter on the auth log lines | > 10 in 5 minutes from one source | Credential stuffing against `/admin/`. The site is small enough that this is a genuinely quiet signal. |
| 4 | Any successful sign-in outside working hours | metric filter | any | Cheap, low-volume, and the sort of thing worth knowing about on a service with a handful of editors. Agree the window before enabling. |
| 5 | Healthy task count below desired | ECS `RunningTaskCount` vs desired | < desired for 10 minutes | The deployment failure mode described above, made visible. |
| 6 | Target group unhealthy hosts | ALB `UnHealthyHostCount` | > 0 for 5 minutes | Distinguishes "the app is up but failing health checks" from "the app is down". |
| 7 | Response time | ALB `TargetResponseTime` p95 | > 2s for 10 minutes | The search page is the heaviest thing here; this is where slow database growth would show first. |
| 8 | Certificate expiry | ACM `DaysToExpiry` | < 21 days | Renewal is automatic until it is not. |
| 9 | Import ran in production | metric filter on the import command's output | any | An import rewrites content in bulk. It should never be a surprise. |

Alerts 1, 5 and 6 are the minimum viable set: they cover "users are seeing
errors" and "the service is not running". The rest are worth having and can
follow.

## Decisions needed before this can be built

1. **Where does an alert go?** There is no destination today — no SNS topic, no
   email list, no channel. This is the blocking question, and it is an
   organisational one rather than a technical one.
2. **Who is on the end of it, and when?** In hours only, or out of hours too?
   The answer changes which of the alerts above are worth having at all: an
   alert nobody reads is worse than no alert, because it implies coverage that
   does not exist.
3. **Which environments?** Production certainly. Alerting on dev will mostly
   report that somebody is working on dev.
4. **Log retention.** CloudWatch retention is currently whatever the module
   defaults to. Security event logs generally want longer than application
   debug logs, and that is a per-log-group setting.

## Outstanding

- [ ] Agree the alert destination and the people behind it. **Blocking.**
- [ ] Agree thresholds, starting from the table above.
- [ ] Create the alarms in `wagtail-iac` or per instance in `wagtail-instances`,
      whichever the platform owner prefers.
- [ ] Set an explicit CloudWatch log retention per environment.
- [ ] Add an ECS deployment circuit breaker and a minimum healthy percent.
- [ ] Confirm `INCOMING_REQUEST_INFO_LOGGING` and
      `CONTENT_DISCOVERY_REQUEST_INFO_LOGGING` are off in production.
- [x] Structured JSON application and access logging reaching CloudWatch.
- [x] Wagtail audit trail of publishing actions.
