"""Copyright (c) 2026 Ahmad Bilal (AhmadBilalDSA). All Rights Reserved.

Self-updater checking GitHub Releases for new sqlean-lint versions.

Compares the local ``__version__`` against the latest tag published on
GitHub and returns a human-readable report.  No automatic installation
is performed -- the user is informed of the available update and can
choose to upgrade manually.
"""
from __future__ import annotations

import json
from typing import Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

from ._version import __version__

# ── Constants ──────────────────────────────────────────────────────────

_GITHUB_REPO: str = "ahmadbilaldsa/sqlean-lint"
_RELEASES_URL: str = f"https://api.github.com/repos/{_GITHUB_REPO}/releases/latest"


# ── Exceptions ─────────────────────────────────────────────────────────

class UpdaterError(RuntimeError):
    """Raised when the self-update check fails."""


# ── Internal helpers ───────────────────────────────────────────────────

def _parse_version(version_str: str) -> tuple[int, ...]:
    """Parse a semver-like string into a comparable tuple.

    Strips leading ``v`` if present and ignores any pre-release suffix.
    Examples::

        "0.1.0"   -> (0, 1, 0)
        "v1.2.3"  -> (1, 2, 3)
        "1.0.0b1" -> (1, 0, 0)
    """
    cleaned = version_str.lstrip("v").split("+")[0].split("-")[0].split("b")[0].split("rc")[0]
    parts: list[int] = []
    for part in cleaned.split("."):
        try:
            parts.append(int(part))
        except ValueError:
            break
    return tuple(parts) if parts else (0, 0, 0)


def _fetch_latest_release() -> Optional[dict]:
    """Fetch the latest release metadata from GitHub."""
    req = Request(
        _RELEASES_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"sqlean-lint/{__version__}",
        },
        method="GET",
    )
    with urlopen(req, timeout=15) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


# ── Public API ─────────────────────────────────────────────────────────

def self_update(yes: bool = False) -> str:
    """Check GitHub Releases for a newer version of sqlean-lint.

    Returns a human-readable report string.  When *yes* is ``True`` the
    report suggests automatic installation commands; otherwise it simply
    informs the user.
    """
    try:
        release = _fetch_latest_release()
    except (URLError, OSError, json.JSONDecodeError, KeyError) as exc:
        raise UpdaterError(
            f"Failed to check for updates: {exc}"
        ) from exc

    if release is None:
        return "No release information available from GitHub."

    latest_tag = release.get("tag_name", "")
    latest_name = release.get("name", latest_tag)
    release_url = release.get("html_url", "")
    local_version = __version__

    local_tuple = _parse_version(local_version)
    latest_tuple = _parse_version(latest_tag)

    if latest_tuple <= local_tuple:
        return (
            f"sqlean-lint is up to date.\n"
            f"  Installed: {local_version}\n"
            f"  Latest:    {latest_tag}"
        )

    lines = [
        f"Update available: {latest_tag} (latest)",
        f"  Installed version: {local_version}",
        f"  Release: {latest_name}",
    ]
    if release_url:
        lines.append(f"  URL: {release_url}")

    if yes:
        lines.append(
            "\nTo upgrade, run:\n"
            f"  pip install --upgrade sqlean-lint=={latest_tag}"
        )
    else:
        lines.append(
            "\nRun `sqlean-lint --self-update --yes` or visit the release page "
            "to upgrade."
        )

    return "\n".join(lines)
