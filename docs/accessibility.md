# Accessibility

This service aims to meet [WCAG 2.2 AA](https://www.w3.org/TR/WCAG22/) and
GOV.UK Service Standard Point 9. This document records the component baseline,
the page-level guarantees under automated test, known Design System
deviations, and the audit status. It backs Jira CS32-3308.

## Component baseline

The service is built on the [GOV.UK Design System](https://design-system.service.gov.uk/),
so the bulk of WCAG 2.2 AA behaviour (focus states, colour contrast, keyboard
operation, form error handling, screen-reader semantics) comes from Design
System components rather than bespoke code. Custom views — role, skill,
changelog, search and the framework navigation — compose Design System
components and macros rather than replacing them.

## Page-level guarantees under automated test

`govuk/tests/test_accessibility.py` renders a page through the base template
and asserts the invariants the base template owns. These fail the build if a
template change regresses them:

| Guarantee | WCAG criterion |
| --- | --- |
| `<html lang="en">` is declared | 3.1.1 Language of Page |
| A skip link is present and targets the main landmark (`#main-content`) | 2.4.1 Bypass Blocks |
| The main region is marked with `role="main"` | 1.3.1 / 4.1.2 |
| There is exactly one `<h1>` | 1.3.1 Info and Relationships |
| The document has a non-empty `<title>` | 2.4.2 Page Titled |

These are a regression guard, not a substitute for a full audit.

## Design System deviations

None currently. All interactive components are used as documented by the
Design System. Any future custom component that is not a Design System
component must be added here with its accessibility rationale and test
evidence before launch.

## Known issues carried from the live service

The live service's [accessibility statement](https://ddat-capability-framework.service.gov.uk/)
declares five WCAG 2.2 non-compliances (independently tested May 2026). All five
were re-tested against a running build of this rebuild on 27 August 2026. Four
do not reproduce, and the fifth reproduces only in a much narrower form than the
statement describes.

| Live issue | WCAG | Status in this rebuild |
| --- | --- | --- |
| "Back to top" link has no visible keyboard focus indicator on large screens | 1.3.2, 2.4.3, 2.4.7 | **Does not reproduce.** The control exists (`base.html`, revealed by `main.js` on scroll) but is a `<button>` with accessible name "Back to top", and on focus takes the Design System indicator — `#ffdd00` background with a 4px `#0b0c0c` underline. Measured from the focused element's computed style. |
| iPadOS VoiceOver closes the search dropdown when interacting with suggestions | 2.1.1 | **Not applicable.** No search suggestions component exists in this codebase. `/search/` is a plain `GET` form returning a results page. |
| macOS Safari focus after search suggestions only reaches "Skip to main content" | 2.1.1 | **Not applicable.** Same absent component. |
| Side navigation missing at small sizes / 200% zoom, no reflow equivalent | 1.4.10 Reflow | **Partly reproduces, narrowly.** `.role-nav` is `display:none` below 40.0625em (`main.css`), so the role side-navigation is unavailable on a narrow screen. Alternative routes remain: the breadcrumb, the related-role links in the main column (11 on a role page), the full list of 67 roles on the home page, and search. Separately on reflow itself, seven page types were measured at 320px, 427px (300% zoom) and 640px (200% zoom) with no horizontal scroll (`scrollWidth == clientWidth` throughout) — but see the note below on the one page that is not yet clean. |
| Feedback buttons announced "Yes yes" / "No no", not linked to the question | 1.3.1, 4.1.2 | **Not applicable.** The inline yes/no widget does not exist in this codebase. |

The published accessibility statement must describe this site, not the one it
was inherited from — it is a legal document under PSBAR 2018, and carrying over
four non-compliances that do not exist is as much a misstatement as omitting one
that does.

**One page is not yet clean on the deployed instances.** The senior civil
service contextual-challenges page carries six wide comparison tables. Four
columns of sentences cannot reflow into 320px, so each table sits in a
focusable `.table-scroll` region — WCAG 1.4.10 exempts two-dimensional content,
but only if the *page* stops overflowing. The markup and the CSS rule are both
in this repository, and the page is clean when built locally. The rule ships in
the stylesheet rather than in the content, so it only reaches a running site
with the container image: an instance whose image predates it renders the
wrapper `div`s unstyled, with `overflow-x: visible`, and the page overflows by
251px at 320px. This is the general rule for this codebase — content moves
through the admin export, but anything in CSS, templates or Python needs a
deploy.

The remaining reflow point is a deliberate deviation rather than an oversight:
below 641px `framework_welcome.html` renders all 67 role links on the home page
(`.mobile-homepage-roles`, hidden at ≥641px by `main.css`), so the content the
side navigation leads to is still reachable. It is recorded here as a deviation
under CS32-3308 and stated plainly in the statement itself.

## Outstanding before launch (CS32-3308)

- [ ] Full WCAG 2.2 AA audit by an independent auditor. The May 2026 test was of
      the Strapi service, not this one, so this build has never been audited
      independently and the statement must not imply that it has.
- [ ] Capture and store audit evidence.
- [ ] Replace the statement's content with the version that describes this
      build. The page exists and is published, but still carries the inherited
      wording, including four non-compliances that do not exist here. Drafted
      and awaiting review by whoever owns the statement — it has legal weight
      under PSBAR 2018.
- [x] Re-test the five carried-over issues against a running build and record
      the outcomes (table above, 27 August 2026).
- [x] Automated accessibility sweep with axe across the public templates —
      clean on dev, 26 August 2026.
- [x] Publish the accessibility statement page (seeded into the CMS; travels
      via the admin page export).
- [x] Automated regression guard for the base-template invariants
      (`test_accessibility.py`).
- [x] Remove the empty "Menu" toggle below 641px, which reported
      `aria-expanded="true"` over an empty list
      (`base.html`, `ServiceNavigationTests`). Fixed in the codebase; reaches
      the deployed instances with the next build.
