"""A small content catalogue for the content catalogue tests, built in memory.

``catalogue_files()`` is a catalogue repo with an entry for every layout and
source type, plus the entries the site leaves out (a blog, a draft, a broken
one). ``guide_repo_files()`` is the repo its ``repo`` entry points at.
``fake_fetch`` stands in for GitHub, so the tests never touch the network.
"""

import textwrap

CATALOGUE_REPOSITORY = "example/catalogue"
COMMIT = "a" * 40
OTHER_COMMIT = "b" * 40
GUIDE_COMMIT = "c" * 40


def _dedent(text: str) -> bytes:
    return textwrap.dedent(text).lstrip("\n").encode()


def _entry(entry_id: str, **fields) -> bytes:
    body = {
        "title": entry_id.replace("-", " ").capitalize(),
        "short_description": f"Short description of {entry_id}",
        "long_description": f"Long description of {entry_id}.",
        "scope": "public-sector",
        "tags": "[cloud]",
        "professions": "[engineering]",
        **fields,
    }
    lines = [f"id: {entry_id}"]
    for key, value in body.items():
        if isinstance(value, str) and "\n" in value:
            lines.append(f"{key}:")
            lines.extend(f"  {line}" for line in value.strip("\n").splitlines())
        else:
            lines.append(f"{key}: {value}")
    if "owner" not in body:
        lines.append("owner:\n  organisation: Example Department")
    return ("\n".join(lines) + "\n").encode()


def catalogue_files() -> dict[str, bytes | None]:
    return {
        "catalogue.yml": _dedent(
            """
            schema: guidance-catalogue/v0
            title: Example catalogue
            short_description: Example
            long_description: An example catalogue.
            vocabularies: vocabularies.yml
            entries: entries/
            """
        ),
        "vocabularies.yml": _dedent(
            """
            groups:
              standards-and-policies:
                label: Standards and policies
                description: What is expected.
              guidance:
                label: Guidance
              resources-and-tools:
                label: Resources and tools
            types:
              standard: {label: Standard, plural: Standards, layout: requirements, group: standards-and-policies}
              policy: {label: Policy, plural: Policies, layout: policy, group: standards-and-policies}
              guidance: {label: Guidance, plural: Guidance, layout: guide, group: guidance}
              method: {label: Method, plural: Methods, layout: steps, group: guidance}
              product: {label: Product, plural: Products, layout: tool, group: resources-and-tools}
              blog: {label: Blog, layout: media}
            scope:
              public-sector: {label: Public sector}
              government: {label: Central government}
            professions:
              engineering: {label: Engineering}
              architecture: {label: Architecture}
            tags:
              cloud: {label: Cloud}
              security: {label: Security}
            """
        ),
        # A standard held here, with numbered requirements.
        "entries/hosted-standard.yml": _entry(
            "hosted-standard",
            type="standard",
            primary="true",
            tags="[cloud, security]",
            source="\n  type: hosted\n  name: tech.gov.uk\n  path: content/hosted-standard",
            related="[external-policy, no-such-entry]",
        ),
        "content/hosted-standard/guidance.yml": _dedent(
            """
            schema: guidance-as-code/v0
            title: Hosted standard
            content_dir: .
            index: index.md
            requirements:
              - first-point.md
              - second-point.md
            """
        ),
        "content/hosted-standard/index.md": _dedent(
            """
            ---
            title: Hosted standard
            ---

            # Hosted standard

            The introduction.

            ## Contacts

            Who to ask. See the [first point](first-point.md).
            """
        ),
        "content/hosted-standard/first-point.md": _dedent(
            """
            ---
            title: The first point
            summary: Do the first thing.
            ---

            # The first point

            ## Why

            ![Diagram](https://assets.example.gov.uk/diagram.png)
            """
        ),
        "content/hosted-standard/second-point.md": _dedent(
            """
            ---
            title: The second point
            summary: Then the second.
            ---

            # The second point
            """
        ),
        # A policy held here, split into sections.
        "entries/hosted-policy.yml": _entry(
            "hosted-policy",
            type="policy",
            tags="[security]",
            source="\n  type: hosted\n  name: tech.gov.uk\n  path: content/hosted-policy",
        ),
        "content/hosted-policy/guidance.yml": _dedent(
            """
            schema: guidance-as-code/v0
            title: Hosted policy
            content_dir: .
            index: index.md
            """
        ),
        "content/hosted-policy/index.md": _dedent(
            """
            ---
            title: Hosted policy
            version: "2.1"
            effective_from: 2026-04-01
            last_reviewed_on: 2026-01-01
            review_in: 12 months
            ---

            # Hosted policy

            Read this first.

            ## Policy statement

            1.1 Organisations **must** do this.

            ## Scope

            ### Devices

            2.1 It covers phones.
            """
        ),
        # Guidance in another repo.
        "entries/repo-guide.yml": _entry(
            "repo-guide",
            type="guidance",
            source="\n  type: repo\n  name: GitHub\n  path: example/guide-repo",
        ),
        # A method held here, with numbered steps and a reference page.
        "entries/hosted-method.yml": _entry(
            "hosted-method",
            type="method",
            source="\n  type: hosted\n  name: tech.gov.uk\n  path: content/hosted-method",
        ),
        "content/hosted-method/guidance.yml": _dedent(
            """
            schema: guidance-as-code/v0
            title: Hosted method
            content_dir: .
            index: index.md
            steps:
              - prepare.md
              - run.md
            navigation:
              - title: Reference
                pages: [glossary.md]
            """
        ),
        "content/hosted-method/index.md": b"---\ntitle: Hosted method\n---\n\n# Hosted method\n\nStart here.\n",
        "content/hosted-method/prepare.md": b"---\ntitle: Prepare\n---\n\n# Prepare\n\n## Gather\n",
        "content/hosted-method/run.md": b"---\ntitle: Run it\n---\n\n# Run it\n",
        "content/hosted-method/glossary.md": b"---\ntitle: Glossary\n---\n\n# Glossary\n",
        # A product with a page of its own, from its links.
        "entries/linked-tool.yml": _entry(
            "linked-tool",
            type="product",
            version="v3",
            links=(
                "\n  - title: Use it\n    url: https://tool.example.gov.uk/\n"
                "    description: Sign in.\n  - title: Not https\n    url: http://tool.example.gov.uk/"
            ),
            source="\n  type: external\n  name: Example\n  path: https://tool.example.gov.uk/",
        ),
        # External entries: their cards link out, with no pages here.
        "entries/external-policy.yml": _entry(
            "external-policy",
            type="policy",
            primary="true",
            source="\n  type: external\n  name: GOV.UK\n  path: https://www.gov.uk/guidance/external-policy",
        ),
        "entries/external-standard.yml": _entry(
            "external-standard",
            title="Another standard",
            type="standard",
            scope="government",
            professions="[architecture]",
            source="\n  type: external\n  name: GOV.UK\n  path: https://www.gov.uk/guidance/external-standard",
        ),
        # Left out: a blog (no group), a draft, and one with mistakes.
        "entries/a-blog.yml": _entry(
            "a-blog",
            type="blog",
            source="\n  type: external\n  name: Blog\n  path: https://blog.example.gov.uk/",
        ),
        "entries/a-draft.yml": _entry(
            "a-draft",
            type="standard",
            status="draft",
            source="\n  type: external\n  name: GOV.UK\n  path: https://www.gov.uk/draft",
        ),
        "entries/broken-entry.yml": b"id: broken-entry\ntitle: Broken\n",
    }


def guide_repo_files() -> dict[str, bytes | None]:
    return {
        "guidance.yml": _dedent(
            """
            schema: guidance-as-code/v0
            title: Repo guide
            content_dir: docs
            navigation:
              - title: Topics
                pages:
                  - path: topics/alerting.md
                    pages: [topics/alerting/escalation.md]
            """
        ),
        "docs/index.md": b"---\ntitle: Repo guide\n---\n\n# Repo guide\n\nSee [alerting](topics/alerting.md).\n",
        "docs/topics/alerting.md": b"---\ntitle: Alerting\nlast_reviewed_on: 2026-01-01\nreview_in: 6 months\n---\n\n# Alerting\n\n## Sending alerts\n",
        "docs/topics/alerting/escalation.md": b"---\ntitle: Escalation\n---\n\n# Escalation\n",
    }


class FakeGitHub:
    """Answers for the catalogue repo and the guide repo, and counts the calls."""

    def __init__(self, catalogue=None, guide=None, *, commit=COMMIT, guide_commit=GUIDE_COMMIT):
        self.catalogue = catalogue if catalogue is not None else catalogue_files()
        self.guide = guide if guide is not None else guide_repo_files()
        self.commit = commit
        self.guide_commit = guide_commit
        self.calls: list[str] = []
        self.failing: set[str] = set()

    def __call__(self, repository):
        from govuk.catalogue.bundle import GuidanceSyncError

        key = f"{repository.owner}/{repository.name}"
        self.calls.append(key)
        if key in self.failing:
            raise GuidanceSyncError(f"{key} is unavailable")
        if key == CATALOGUE_REPOSITORY:
            return self.commit, self.catalogue
        if key == "example/guide-repo":
            return self.guide_commit, self.guide
        raise GuidanceSyncError(f"no such repository {key}")


def guidance_flags(*, guidance_enabled: bool = True) -> dict[str, bool]:
    return {"SKILLS": False, "FEEDBACK": False, "GUIDANCE": guidance_enabled}
