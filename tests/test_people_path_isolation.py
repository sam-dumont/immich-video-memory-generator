"""The people roster follows HOME, so a test run never reads a real family file."""

from __future__ import annotations

from pathlib import Path

from immich_memories.people.companion import default_people_path


def test_default_people_path_follows_a_redirected_home(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    assert default_people_path() == tmp_path / ".immich-memories" / "people.yaml"


def test_launch_environment_redirects_home_at_the_workspace(tmp_path: Path) -> None:
    from tests.e2e.conftest import _build_launch_environment

    environment = _build_launch_environment(tmp_path)

    assert environment["HOME"] == str(tmp_path)
    assert environment["USERPROFILE"] == str(tmp_path)
