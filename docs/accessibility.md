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
declares five WCAG 2.2 non-compliances (independently tested May 2026). Their
status in this Wagtail rebuild:

| Live issue | WCAG | Status in Wagtail rebuild |
| --- | --- | --- |
| "Back to top" link has no visible keyboard focus indicator on large screens | 1.3.2, 2.4.3, 2.4.7 | No "back to top" component exists in this codebase — not applicable. Re-confirm during audit. |
| iPadOS VoiceOver closes the search dropdown when interacting with suggestions | 2.1.1 | Search is rebuilt on Design System components — needs re-test on iPadOS VoiceOver. |
| macOS Safari focus after search suggestions only reaches "Skip to main content" | 2.1.1 | Needs re-test on macOS Safari. |
| Side navigation missing at small sizes / 200% zoom, no reflow equivalent | 1.4.10 Reflow | Role/skill side navigation rebuilt — needs reflow re-test at 200% zoom. |
| Feedback buttons announced "Yes yes" / "No no", not linked to the question | 1.3.1, 4.1.2 | The inline yes/no widget is not present; `/feedback` uses labelled form controls — not applicable. |

Two of the five do not apply to the rebuild; three are search/navigation
behaviours that need re-testing against the Design System implementation
during the audit below.

## Outstanding before launch (CS32-3308)

- [ ] Full WCAG 2.2 AA audit — automated (e.g. axe/pa11y) across public
      templates, plus manual testing of the custom role / skill / changelog /
      search views with a keyboard and a screen reader.
- [ ] Re-test the three carried-over search/navigation issues above against
      the Design System implementation and record outcomes.
- [ ] Capture and store audit evidence.
- [x] Publish the accessibility statement page (seeded into the CMS; travels
      via the admin page export).
- [x] Automated regression guard for the base-template invariants
      (`test_accessibility.py`).
