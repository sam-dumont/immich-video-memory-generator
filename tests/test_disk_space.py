"""Tests for disk space preflight check before assembly."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from immich_memories.generate import GenerationError
from tests.conftest import make_clip


class TestCheckDiskSpace:
    """check_disk_space should raise GenerationError when free space < 1GB."""

    def test_raises_when_disk_full(self, tmp_path: Path):
        from immich_memories.generate import check_disk_space

        # Simulate <1GB free (500MB)
        fake_usage = type("Usage", (), {"free": 500 * 1024 * 1024})()
        with (
            patch("shutil.disk_usage", return_value=fake_usage),
            pytest.raises(GenerationError, match="Insufficient disk space"),
        ):
            check_disk_space(tmp_path)

    def test_passes_when_enough_space(self, tmp_path: Path):
        from immich_memories.generate import check_disk_space

        # 5GB free — should not raise
        fake_usage = type("Usage", (), {"free": 5 * 1024 * 1024 * 1024})()
        with patch("shutil.disk_usage", return_value=fake_usage):
            check_disk_space(tmp_path)  # Should not raise

    def test_passes_at_exactly_1gb(self, tmp_path: Path):
        from immich_memories.generate import check_disk_space

        fake_usage = type("Usage", (), {"free": 1024 * 1024 * 1024})()
        with patch("shutil.disk_usage", return_value=fake_usage):
            check_disk_space(tmp_path)  # Should not raise (>= threshold)

    def test_error_message_includes_free_space(self, tmp_path: Path):
        from immich_memories.generate import check_disk_space

        fake_usage = type("Usage", (), {"free": 200 * 1024 * 1024})()
        with (
            patch("shutil.disk_usage", return_value=fake_usage),
            pytest.raises(GenerationError, match="0.2 GB free"),
        ):
            check_disk_space(tmp_path)


def test_a_run_on_a_full_disk_stops_before_it_downloads_anything(tmp_path: Path):
    from immich_memories.config_loader import Config
    from immich_memories.generate import GenerationParams, generate_memory

    client = MagicMock()  # WHY: Immich must not be asked for a single clip
    params = GenerationParams(
        clips=[make_clip("c1")], output_path=tmp_path / "out.mp4", config=Config(), client=client
    )

    # WHY: a disk with 500 MB left, below the 1 GB floor
    with (
        # WHY: shutil.disk_usage reads the real volume
        patch("immich_memories.generate.shutil.disk_usage", return_value=MagicMock(free=500 << 20)),
        pytest.raises(GenerationError, match="Insufficient disk space"),
    ):
        generate_memory(params)

    assert client.mock_calls == []
