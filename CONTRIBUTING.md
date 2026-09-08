# Contributing

Thanks for your interest in this project. It is a Wagtail content management
system with a GOV.UK Design System front end, run by the Government Digital
Service and used by more than one government service. Anyone may use it, fork
it, or send a change back.

Because several services run from this one codebase, a change that suits one of
them can break another. The notes below are mostly about how to tell the
difference.

## Raising an issue

Open a GitHub issue for bugs, questions and ideas. Please say which version you
are on — the `/api/` endpoint of any running instance reports the build tag it
was made from.

Do not report a security vulnerability in a GitHub issue. Report it through the
[GOV.UK vulnerability reporting service](https://vulnerability-reporting.service.security.gov.uk/),
which is where `/.well-known/security.txt` points.

## Before you start writing code

For anything beyond a small fix, open an issue first and say what you plan to
do. It is quicker to agree an approach in an issue than to unpick a finished
branch.

Two things are worth checking before you begin:

- **Is it site-specific?** Templates, settings models and CSS are shared by
  every instance. If a change only suits one service, put it behind a feature
  flag (`FEATURE_FLAGS` in `govuk/settings/base.py`) or a site setting
  (`BaseSiteSetting` in `govuk/models.py`) rather than changing the default.
- **Does it change published content?** Page models, rich text features and the
  import and export format all affect content that editors have already
  written. Migrations that drop or rename a field need a plan for the content
  already in it.

## Making a change

1. Fork the repository, or branch from `main` if you have write access.
2. Make your change, with tests.
3. Run the same checks CI runs:

   ```bash
   ruff check .
   python manage.py check
   python manage.py test
   ```

4. Open a pull request describing what changed and why.

### Tests

Tests live in `govuk/tests/`, one module per area, and run with Django's test
runner. New behaviour needs a test; a bug fix needs a test that fails without
the fix.

Prefer tests that go through a rendered page or a management command over tests
that assert on internals, so a refactor does not have to be accompanied by a
rewrite of the test suite.

### Style

`ruff` is the only linter, configured in `pyproject.toml`. It runs Pyflakes,
the pycodestyle error rules and import sorting — the rules that find defects,
rather than the ones that express a preference.

Comments should explain why something is the way it is, especially where the
obvious approach was tried and did not work. Comments that restate the code are
noise.

### Migrations

Migrations are generated with `python manage.py makemigrations` and committed.
Do not hand-edit generated migrations to satisfy a linter — `govuk/migrations`
is excluded from `ruff` for that reason.

If two branches add a migration at the same number, merge `main` in and
regenerate rather than renumbering by hand.

## Pull requests

Every pull request runs `ruff`, `manage.py check` and `manage.py test` on
Python 3.13. Dependabot and CodeQL run on the repository.

A pull request is easier to review when it does one thing. If you find a second
problem while fixing the first, a separate pull request is usually faster for
everyone than one that does both.

## Code of conduct

This project follows the
[gds-dtx code of conduct](https://github.com/gds-dtx/.github/blob/main/CODE_OF_CONDUCT.md),
which is the Contributor Covenant. By taking part you are expected to uphold
it.

## Licensing

The code in this repository is published under the [MIT Licence](LICENCE).
Documentation and content are © Crown copyright and available under the
[Open Government Licence v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/).

By contributing, you agree that your contribution is licensed on those terms.
