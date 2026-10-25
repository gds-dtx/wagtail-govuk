"""Which repo, branch and directory a guidance collection reads from.

GitHub only, for the prototype: it is where the guidance the site starts with
lives, and its tarball and raw-file URLs are stable enough to build by hand.
"""

import re
from dataclasses import dataclass, replace
from urllib.parse import quote, urlencode

_GITHUB_REPOSITORY = re.compile(
    r"^https://github\.com/(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/"
    r"(?P<name>[A-Za-z0-9._-]{1,100}?)(?:\.git)?/?$"
)
_BRANCH = re.compile(r"^(?!/)(?!.*\.\.)[A-Za-z0-9._/-]{1,200}(?<!/)$")


def parse_repository_url(url: str) -> tuple[str, str]:
    """``(owner, name)`` from ``https://github.com/<owner>/<name>``.

    Raises ValueError for anything else, so a typo or a non-GitHub host is
    caught when the page is saved rather than on the first sync.
    """
    match = _GITHUB_REPOSITORY.match((url or "").strip())
    if not match or match.group("name") in (".", ".."):
        raise ValueError("Enter a GitHub repository URL, like https://github.com/alphagov/gds-way")
    return match.group("owner"), match.group("name")


def validate_branch(branch: str) -> str:
    branch = (branch or "").strip()
    if not _BRANCH.match(branch):
        raise ValueError("Enter a branch name, like main")
    return branch


@dataclass(frozen=True)
class RepositoryRef:
    owner: str
    name: str
    branch: str = "main"
    subdirectory: str = ""
    commit: str = ""

    @classmethod
    def from_page(cls, page) -> "RepositoryRef":
        owner, name = parse_repository_url(page.repository_url)
        return cls(
            owner=owner,
            name=name,
            branch=validate_branch(page.branch or "main"),
            subdirectory=(page.subdirectory or "").strip().strip("/"),
        )

    def at(self, commit: str) -> "RepositoryRef":
        return replace(self, commit=commit)

    @property
    def source_key(self) -> str:
        """Identifies the source settings, so changing them forces a re-sync."""
        return f"{self.owner}/{self.name}@{self.branch}:{self.subdirectory}"

    @property
    def html_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.name}"

    @property
    def ref(self) -> str:
        return self.commit or self.branch

    def _repo_path(self, path: str) -> str:
        full = f"{self.subdirectory}/{path}" if self.subdirectory else path
        return quote(full.lstrip("/"))

    def blob_url(self, path: str) -> str:
        return f"{self.html_url}/blob/{quote(self.ref)}/{self._repo_path(path)}"

    def raw_url(self, path: str) -> str:
        return (
            f"https://raw.githubusercontent.com/{self.owner}/{self.name}/"
            f"{quote(self.ref)}/{self._repo_path(path)}"
        )

    def commit_url(self) -> str:
        return f"{self.html_url}/commit/{self.commit}" if self.commit else self.html_url

    def new_issue_url(self, title: str) -> str:
        return f"{self.html_url}/issues/new?{urlencode({'title': title})}"

    @property
    def commit_api_url(self) -> str:
        return (
            f"https://api.github.com/repos/{self.owner}/{self.name}/commits/"
            f"{quote(self.branch, safe='')}"
        )

    def tarball_url(self, commit: str) -> str:
        return f"https://codeload.github.com/{self.owner}/{self.name}/tar.gz/{commit}"
