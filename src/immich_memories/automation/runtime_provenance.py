"""Which code is actually running: version, commit, and how stale the checkout is.

A scheduled job stores a command once and re-runs it for months. When that command
resolves to a checkout nobody updates, every run silently executes old code and the logs
look completely normal (#573). Nothing here changes behaviour — it only makes the answer
to "which code just ran?" printable.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from immich_memories import __version__

_GIT_TIMEOUT_SECONDS = 5


def _git_free_environment() -> dict[str, str]:
    """Environment with git's own overrides removed.

    `GIT_DIR` and friends beat `-C` — inside a git hook (pre-commit, for one) every
    `git -C <path>` below would silently answer about the hook's repository instead.
    """
    return {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}


@dataclass(frozen=True)
class CheckoutDrift:
    """How far a git checkout has fallen behind the branch it tracks."""

    upstream: str
    commits_behind: int


@dataclass(frozen=True)
class RuntimeProvenance:
    """Identity of the code executing in this process."""

    version: str
    checkout: Path | None = None
    commit: str | None = None
    drift: CheckoutDrift | None = None

    @property
    def is_stale(self) -> bool:
        """True when this checkout is behind a tracking branch it already fetched."""
        return self.drift is not None

    def describe(self) -> str:
        """One log line naming exactly which code is running."""
        parts = [f"immich-memories {self.version}"]
        if self.commit:
            parts.append(f"commit {self.commit}")
        if self.checkout:
            parts.append(f"checkout {self.checkout}")
        if self.drift:
            parts.append(f"BEHIND {self.drift.upstream} by {self.drift.commits_behind} commit(s)")
        return ", ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        """Machine-facing provenance for `auto status --json`."""
        return {
            "version": self.version,
            "checkout": str(self.checkout) if self.checkout else None,
            "commit": self.commit,
            "upstream": self.drift.upstream if self.drift else None,
            "commits_behind": self.drift.commits_behind if self.drift else None,
            "stale": self.is_stale,
        }


def _git(checkout: Path, *args: str) -> str | None:
    """Run one read-only git command, returning None for any inconclusive result."""
    try:
        result = subprocess.run(
            ["git", "-C", str(checkout), *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=_GIT_TIMEOUT_SECONDS,
            env=_git_free_environment(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def git_checkout_root(start: Path) -> Path | None:
    """Return the checkout holding `start` — clone, linked worktree, or submodule alike."""
    for directory in (start, *start.parents):
        if (directory / ".git").exists():
            return directory
    return None


def checkout_drift(checkout: Path) -> CheckoutDrift | None:
    """Commits this checkout trails its tracking branch by, from already-fetched refs.

    Deliberately never fetches: neither `auto install` nor a 03:00 run should reach the
    network, so this reports only drift the user's own fetch or pull already recorded.
    """
    upstream = _git(checkout, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
    if not upstream:
        return None
    behind = _git(checkout, "rev-list", "--count", "HEAD..@{upstream}")
    if behind is None or not behind.isdigit() or int(behind) == 0:
        return None
    return CheckoutDrift(upstream=upstream, commits_behind=int(behind))


def runtime_provenance(package_file: Path | None = None) -> RuntimeProvenance:
    """Identify the code this process is executing, for logs and `auto status`."""
    if package_file is None:
        import immich_memories

        located = getattr(immich_memories, "__file__", None)
        package_file = Path(located).resolve() if located else None

    checkout = git_checkout_root(package_file) if package_file else None
    if checkout is None:
        return RuntimeProvenance(version=__version__)
    return RuntimeProvenance(
        version=__version__,
        checkout=checkout,
        commit=_git(checkout, "rev-parse", "--short", "HEAD"),
        drift=checkout_drift(checkout),
    )
