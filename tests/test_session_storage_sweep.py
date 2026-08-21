from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from immich_memories.ui.session_storage import sweep_expired_user_storage

_HOUR = 3600


def _aged(path: Path, *, hours: float) -> Path:
    path.write_text("{}")
    stamp = time.time() - hours * _HOUR
    os.utime(path, (stamp, stamp))
    return path


class TestSweepExpiredUserStorage:
    def test_it_removes_user_storage_nothing_came_back_for(self, tmp_path):
        stale = _aged(tmp_path / "storage-user-abandoned.json", hours=30)
        fresh = _aged(tmp_path / "storage-user-active.json", hours=1)

        removed = sweep_expired_user_storage(tmp_path, ttl_hours=24)

        assert removed == 1
        assert not stale.exists()
        assert fresh.exists()

    def test_it_leaves_the_storage_nicegui_manages_itself(self, tmp_path):
        """General storage is app-wide state; tab storage has its own TTL."""
        general = _aged(tmp_path / "storage-general.json", hours=500)
        tab = _aged(tmp_path / "storage-tab-abc.json", hours=500)

        assert sweep_expired_user_storage(tmp_path, ttl_hours=24) == 0
        assert general.exists()
        assert tab.exists()

    def test_a_missing_directory_is_not_an_error(self, tmp_path):
        assert sweep_expired_user_storage(tmp_path / "nope", ttl_hours=24) == 0

    @pytest.mark.parametrize("ttl_hours", [0, -1])
    def test_a_nonsensical_ttl_sweeps_nothing(self, tmp_path, ttl_hours):
        """Config bounds ttl to >=1; a bad value must not wipe every session."""
        kept = _aged(tmp_path / "storage-user-kept.json", hours=500)

        assert sweep_expired_user_storage(tmp_path, ttl_hours=ttl_hours) == 0
        assert kept.exists()


class TestAppWiring:
    def test_the_app_expires_session_files_using_the_configured_ttl(self, tmp_path):
        """A wrong TTL here silently either hoards files or logs everyone out."""
        from unittest.mock import patch

        from immich_memories.config import Config
        from immich_memories.ui.app import _expire_session_storage

        stale = _aged(tmp_path / "storage-user-old.json", hours=5)
        kept = _aged(tmp_path / "storage-user-new.json", hours=1)

        # WHY: both replace process-wide state rather than collaborators.
        with (
            # WHY: NiceGUI's real on-disk storage directory.
            patch("nicegui.storage.Storage.path", tmp_path),
            # WHY: the config file on disk, proving the TTL is read not hardcoded.
            patch(
                "immich_memories.ui.app.get_config",
                return_value=Config(auth={"session_ttl_hours": 3}),
            ),
        ):
            _expire_session_storage()

        assert not stale.exists()
        assert kept.exists()
