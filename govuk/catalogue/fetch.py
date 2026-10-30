"""Download a catalogue or guidance repo from GitHub, read only into memory.

Only https requests to GitHub's API and archive hosts are made. On top of that
they go through the content discovery fetcher's guards (no private or loopback
addresses, re-checked on every redirect), with a cap on how much is read. The tarball is never extracted to disk: its regular files are
read straight out of it, so a hostile archive's ``../`` names, links and
devices have nothing to write to.
"""

import io
import posixpath
import tarfile
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request

from django.conf import settings

from govuk.content_discovery import (
    USER_AGENT,
    ContentDiscoveryError,
    _open_source_request,
    _validate_source_fetch_dns_resolution,
    _validate_source_fetch_url,
)

from .bundle import GuidanceSyncError
from .repository import RepositoryRef

MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024
MAX_UNPACKED_BYTES = 50 * 1024 * 1024
# Only these are read; every other file is recorded by name, so a link to it
# can still be sent to GitHub.
TEXT_SUFFIXES = (".md", ".erb", ".yml", ".yaml", ".rb")
MAX_TEXT_FILE_BYTES = 2 * 1024 * 1024
# The only hosts a sync talks to. The content discovery guard does not check
# the scheme, so this does: nothing but https to these.
ALLOWED_HOSTS = frozenset({"api.github.com", "codeload.github.com"})


def _get(url: str, *, accept: str, timeout: float, max_bytes: int) -> bytes:
    parts = urlsplit(url)
    if parts.scheme != "https" or (parts.hostname or "").lower() not in ALLOWED_HOSTS:
        raise GuidanceSyncError(f"Refusing to fetch '{url}': only GitHub over https is allowed.")
    headers = {"User-Agent": USER_AGENT, "Accept": accept}
    token = getattr(settings, "GUIDANCE_GITHUB_TOKEN", "")
    if token and url.startswith("https://api.github.com/"):
        headers["Authorization"] = f"Bearer {token}"
    try:
        _validate_source_fetch_url(url)
        _validate_source_fetch_dns_resolution(url)
        with _open_source_request(
            Request(url, headers=headers), timeout=timeout, disable_tls_verification=False
        ) as response:
            body = response.read(max_bytes + 1)
    except ContentDiscoveryError as exc:
        raise GuidanceSyncError(str(exc)) from exc
    except HTTPError as exc:
        raise GuidanceSyncError(f"Could not fetch '{url}': HTTP {exc.code}") from exc
    except (URLError, OSError) as exc:
        raise GuidanceSyncError(f"Could not fetch '{url}': {exc}") from exc
    if len(body) > max_bytes:
        raise GuidanceSyncError(f"'{url}' is larger than {max_bytes // (1024 * 1024)} MB.")
    if not body:
        raise GuidanceSyncError(f"'{url}' returned an empty response.")
    return body


def resolve_commit(repository: RepositoryRef, *, timeout: float = 30.0) -> str:
    """The commit the pinned tag or commit, or else the branch, points at now."""
    body = _get(
        repository.commit_api_url,
        accept="application/vnd.github.sha",
        timeout=timeout,
        max_bytes=1024,
    )
    sha = body.decode("ascii", errors="replace").strip()
    if len(sha) != 40 or any(c not in "0123456789abcdef" for c in sha):
        raise GuidanceSyncError(
            f"GitHub did not return a commit for '{repository.pin or repository.branch}'."
        )
    return sha


def fetch_files(
    repository: RepositoryRef, commit: str, *, timeout: float = 30.0
) -> dict[str, bytes | None]:
    """The repo's files at ``commit``, relative to the configured directory."""
    archive = _get(
        repository.tarball_url(commit),
        accept="application/x-gzip, application/octet-stream",
        timeout=timeout,
        max_bytes=MAX_DOWNLOAD_BYTES,
    )
    return read_tarball(archive, subdirectory=repository.subdirectory)


def read_tarball(archive: bytes, *, subdirectory: str = "") -> dict[str, bytes | None]:
    """Regular files from a GitHub tarball, without its top-level folder."""
    prefix = f"{subdirectory.strip('/')}/" if subdirectory.strip("/") else ""
    files: dict[str, bytes | None] = {}
    unpacked = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
            for member in tar:
                if not member.isreg():
                    continue
                # GitHub wraps everything in "<repo>-<sha>/".
                _, _, name = member.name.partition("/")
                name = _safe_relative_path(name)
                if not name or not name.startswith(prefix):
                    continue
                name = name[len(prefix) :]
                if not name.endswith(TEXT_SUFFIXES) or member.size > MAX_TEXT_FILE_BYTES:
                    files[name] = None
                    continue
                unpacked += member.size
                if unpacked > MAX_UNPACKED_BYTES:
                    raise GuidanceSyncError("The repository's text files are too large to sync.")
                extracted = tar.extractfile(member)
                files[name] = extracted.read() if extracted else None
    except (tarfile.TarError, EOFError, OSError) as exc:
        raise GuidanceSyncError(f"Could not read the repository archive: {exc}") from exc
    if not files:
        where = f"directory '{subdirectory}'" if prefix else "repository"
        raise GuidanceSyncError(f"The {where} has no files.")
    return files


def _safe_relative_path(name: str) -> str:
    """``name`` if it is a plain relative path, else the empty string."""
    if not name or name.startswith("/") or "\\" in name:
        return ""
    parts = name.split("/")
    if any(part in ("..", "") for part in parts[:-1]) or parts[-1] in ("", "..", "."):
        return ""
    return posixpath.normpath(name)
