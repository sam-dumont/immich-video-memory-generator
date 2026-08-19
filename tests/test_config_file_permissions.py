"""config.yaml holds the Immich API key, so it must never exist world-readable.

`open("w")` creates a file with the process umask -- 0644 on a normal system --
and only a later `chmod` narrows it. Between those two calls the API key is on
disk readable by every account on the machine. On a NAS or a shared box that is
the whole exposure, and it is invisible in testing because the file looks
correct by the time anyone checks.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from immich_memories.config_loader import Config


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_saved_config_is_owner_only(tmp_path: Path):
    path = tmp_path / "config.yaml"

    Config().save_yaml(path)

    assert _mode(path) == 0o600


def test_the_file_is_never_created_wider_than_0600(tmp_path: Path, monkeypatch):
    """Proves the permissions come from creation, not from a later chmod.

    With chmod disabled, a file created via `open("w")` keeps the umask bits and
    this fails; a file created at 0600 passes.
    """
    path = tmp_path / "config.yaml"
    monkeypatch.setattr(Path, "chmod", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(os, "chmod", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(os, "umask", lambda _mask: 0)

    Config().save_yaml(path)

    assert _mode(path) == 0o600, "config.yaml was created with umask permissions"


def test_replacing_an_existing_config_does_not_widen_it(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text("old: value\n")
    path.chmod(0o644)

    Config().save_yaml(path)

    assert _mode(path) == 0o600


def test_the_saved_file_is_still_readable_config(tmp_path: Path):
    path = tmp_path / "config.yaml"
    config = Config()
    config.immich.url = "http://example.invalid:2283"

    config.save_yaml(path)

    assert Config.from_yaml(path).immich.url == "http://example.invalid:2283"


class TestStorageSecretFile:
    """`.storage_secret` signs session cookies. A local account that reads it
    during the window between creation and chmod can forge a session.
    """

    def test_generated_secret_file_is_owner_only(self, tmp_path: Path, monkeypatch):
        monkeypatch.delenv("IMMICH_MEMORIES_STORAGE_SECRET", raising=False)
        monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))
        from immich_memories.ui.app import _get_storage_secret

        secret = _get_storage_secret()

        path = tmp_path / ".immich-memories" / ".storage_secret"
        assert path.read_text() == secret
        assert _mode(path) == 0o600

    def test_the_secret_file_is_never_created_wider_than_0600(self, tmp_path: Path, monkeypatch):
        monkeypatch.delenv("IMMICH_MEMORIES_STORAGE_SECRET", raising=False)
        monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))
        monkeypatch.setattr(Path, "chmod", lambda *_a, **_k: None)
        monkeypatch.setattr(os, "chmod", lambda *_a, **_k: None)
        from immich_memories.ui.app import _get_storage_secret

        _get_storage_secret()

        path = tmp_path / ".immich-memories" / ".storage_secret"
        assert _mode(path) == 0o600

    def test_an_existing_secret_is_reused_untouched(self, tmp_path: Path, monkeypatch):
        monkeypatch.delenv("IMMICH_MEMORIES_STORAGE_SECRET", raising=False)
        monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))
        path = tmp_path / ".immich-memories" / ".storage_secret"
        path.parent.mkdir(parents=True)
        path.write_text("existing-secret")
        from immich_memories.ui.app import _get_storage_secret

        assert _get_storage_secret() == "existing-secret"


class TestFailedWriteLeavesNoDebris:
    def test_a_failure_removes_the_temp_file_and_propagates(self, tmp_path: Path, monkeypatch):
        """A half-written secret must not survive, and the caller must be told."""
        from immich_memories import security

        def boom(*_args, **_kwargs):
            raise OSError("disk full")

        # WHY: stands in for the filesystem failing mid-save.
        monkeypatch.setattr(security.os, "replace", boom)
        target = tmp_path / "config.yaml"

        with pytest.raises(OSError, match="disk full"):
            security.write_secret_file(target, "secret")

        assert not target.exists()
        assert list(tmp_path.iterdir()) == [], "a temp file was left behind"

    def test_an_existing_file_survives_a_failed_rewrite(self, tmp_path: Path, monkeypatch):
        from immich_memories import security

        target = tmp_path / "config.yaml"
        security.write_secret_file(target, "original")

        def boom(*_args, **_kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(security.os, "replace", boom)
        with pytest.raises(OSError):
            security.write_secret_file(target, "replacement")

        assert target.read_text() == "original"
