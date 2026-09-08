# Analytics

There is **no analytics in this codebase**. No tags, no beacons, no tracking
cookies, no server-side collection. That is the current state, not an oversight
being hidden: CS32-3315 is in To Do because the approach has never been decided,
and nothing should be built until it is.

This document exists so the decision can be made. It backs CS32-3315, and it
records the assumption CS32-3314 was closed on.

## The dependency nobody has written down

CS32-3314 (Cookies) was closed on 29 July 2026 as "not needed for MVP as we are
using server side analytics". That is only true while analytics stays server
side.

Client-side analytics — Google Analytics, or anything else that runs in the
reader's browser — sets non-essential storage. Under PECR that needs consent
before it is set, which means a cookie banner using the GOV.UK cookie pattern, a
cookies page listing every cookie with its purpose and duration, a remembered
choice, and the guarantee that nothing non-essential fires before consent. All
of that is CS32-3314's acceptance criteria, and all of it is currently unbuilt.

**So: choosing a client-side tool reopens CS32-3314 and adds a cookie banner to
the build.** Whoever decides CS32-3315 should know they are also deciding that.
The site currently sets no analytics cookies at all, and `/cookies` returns 404
on dev — there is a `/cookie-statement/` page, which describes essential cookies
only.

## Options

### 1. ALB and CloudFront access logs, queried with Athena

Logs the platform already produces, delivered to S3, queried with SQL, charted
in QuickSight or a notebook.

- **Privacy:** strongest. Nothing runs in the browser, no consent needed, no
  cookie banner, CS32-3314 stays closed. IP addresses in the logs are personal
  data and want a short retention or a truncation step, which is a
  configuration decision rather than a build.
- **Answers:** page views, which roles and skills are read, search terms (they
  are in the query string), download counts, referrers, device class from the
  user agent, response times.
- **Cannot answer:** anything requiring the browser — scroll depth, time on
  page, outbound link clicks, whether someone abandoned mid-page. Sessions are
  approximate: no cookie means stitching requests by IP and user agent, which
  is a heuristic and undercounts shared connections.
- **Effort:** no application change at all. Athena table definitions and a
  dashboard. Mostly infrastructure work.

### 2. Server-side event logging from the application

Emit a structured event per meaningful action in the Django view layer, into the
existing JSON log stream, and query it the same way.

- **Privacy:** as strong as option 1, and better controlled — the application
  decides exactly what is recorded, so it can log "a search happened and
  returned 0 results" without logging who searched.
- **Answers:** everything in option 1, plus things only the application knows —
  which search produced no results, which role a search result led to, whether
  a download came from the download page or a deep link.
- **Cannot answer:** the same browser-side behaviours as option 1.
- **Effort:** small but real, and it is application code that needs tests and a
  deploy. It is the option that would delay the migration if started now.

### 3. Client-side tag (Google Analytics 4 or similar)

- **Privacy:** requires consent, a banner, a cookies page and the CS32-3314
  work. Also an accessibility and performance cost on every page.
- **Answers:** the full behavioural set, including everything the other options
  cannot reach.
- **Effort:** the largest, because most of it is the consent machinery rather
  than the tag.

## Recommendation

**Option 1 for launch, option 2 afterwards if the questions need it.**

Option 1 needs no application change, cannot delay the migration, and keeps
CS32-3314 closed. It answers the KPIs a content framework actually has — which
roles are read, what people search for, whether the downloads are used — and it
answers them from the day DNS moves, with no consent burden on the reader.

Option 2 is the natural follow-on once someone asks a question option 1 cannot
answer. The most likely such question is "what are people searching for and
failing to find", which is worth instrumenting properly because it directly
drives content work.

Option 3 should only be chosen if there is a specific behavioural question that
justifies putting a consent banner in front of every reader. On a reference site
like this one, there probably is not.

## What is needed to decide

1. **The KPIs.** CS32-3315's acceptance criteria say "KPIs agreed with product
   team" and none are recorded anywhere. Without them, any option is
   unfalsifiable. Two or three questions the product team actually wants
   answered would settle the choice immediately.
2. **Who needs dashboard access**, and whether they have AWS accounts. "Product
   team has dashboard access" is an acceptance criterion, and QuickSight access
   is a per-person cost.
3. **Log retention and IP handling.** Needed for the privacy notice whichever
   option wins.
4. **Whether the privacy page needs updating.** Options 1 and 2 still process
   personal data even without cookies, so the privacy notice should say so.

## Outstanding

- [ ] Agree KPIs with the product team. **Blocking everything else here.**
- [ ] Choose an option. Note the CS32-3314 consequence if it is option 3.
- [ ] Confirm dashboard audience and access route.
- [ ] Set log retention and decide on IP truncation.
- [ ] Update the privacy page to describe whatever is chosen.
- [x] Confirmed that the site currently sets no analytics or marketing cookies.
