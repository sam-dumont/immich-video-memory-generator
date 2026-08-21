"""Scratch files belong somewhere only this user can reach.

`tempfile.gettempdir()` is shared and world-writable. A fixed name under it --
`immich_notif_<stem>.jpg`, `immich_memories/clips/` -- is fully predictable, so
another user on the same host can pre-place a symlink there and have ffmpeg
`-y` write through it, or read what lands there. Only matters on a multi-user
box, which is why the review rated it low rather than none.
"""

from __future__ import annotations

import os
import stat
import tempfile

import pytest

from immich_memories.security import private_temp_dir


def _use(tmp_path, monkeypatch) -> None:
    """WHY: gettempdir() caches its answer on first call, so setting TMPDIR
    after the fact changes nothing -- the tests would silently run against the
    real temp dir. `tempfile.tempdir` is the documented override."""
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))


class TestPrivateTempDir:
    def test_it_creates_the_directory_owner_only(self, tmp_path, monkeypatch):
        _use(tmp_path, monkeypatch)

        created = private_temp_dir("clips")

        assert created.is_dir()
        assert stat.S_IMODE(created.stat().st_mode) == 0o700

    def test_the_parent_it_creates_is_also_owner_only(self, tmp_path, monkeypatch):
        """A 0700 leaf under a 0777 parent still lets others rename the leaf."""
        _use(tmp_path, monkeypatch)

        created = private_temp_dir("clips")

        assert stat.S_IMODE(created.parent.stat().st_mode) == 0o700

    def test_it_is_scoped_so_two_users_cannot_collide(self, tmp_path, monkeypatch):
        _use(tmp_path, monkeypatch)

        assert str(os.getuid()) in str(private_temp_dir("clips"))

    def test_calling_it_twice_returns_the_same_usable_directory(self, tmp_path, monkeypatch):
        _use(tmp_path, monkeypatch)

        assert private_temp_dir("clips") == private_temp_dir("clips")

    def test_it_refuses_a_pre_placed_symlink(self, tmp_path, monkeypatch):
        """The attack: plant the path first and have us write through it."""
        _use(tmp_path, monkeypatch)
        root = tmp_path / f"immich-memories-{os.getuid()}"
        root.mkdir(mode=0o700)
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        (root / "clips").symlink_to(elsewhere)

        with pytest.raises(RuntimeError, match="not a private directory"):
            private_temp_dir("clips")

    def test_it_refuses_a_directory_others_can_write(self, tmp_path, monkeypatch):
        _use(tmp_path, monkeypatch)
        root = tmp_path / f"immich-memories-{os.getuid()}"
        (root / "clips").mkdir(parents=True)
        (root / "clips").chmod(0o777)

        with pytest.raises(RuntimeError, match="not a private directory"):
            private_temp_dir("clips")
