"""Cross-surface contract for the application build version."""

from __future__ import annotations

import tomllib
from pathlib import Path

import immich_memories
from immich_memories._version import __version__ as generated_version


def test_package_exports_generated_version() -> None:
    """The public package version must be the version Hatch VCS generated."""
    assert immich_memories.__version__ == generated_version


def test_hatch_vcs_is_the_only_configured_version_source() -> None:
    """Packaging and release tooling must not maintain a second static version."""
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())

    assert pyproject["tool"]["hatch"]["version"]["source"] == "vcs"
    semantic_release = pyproject["tool"]["semantic_release"]
    assert "version" not in semantic_release
    assert "version_toml" not in semantic_release
