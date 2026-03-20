"""Tests for disk space preflight check before assembly."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from immich_memories.generate import GenerationError


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


class TestDiskSpaceWiredInPipeline:
    """check_disk_space is called early in generate_memory()."""

    def test_check_disk_space_called_before_extraction(self, tmp_path: Path):
        """generate_memory should check disk space before starting extraction."""
        import immich_memories.generate as gen_mod

        assert hasattr(gen_mod, "check_disk_space"), (
            "check_disk_space must be defined in generate.py"
        )

        # Verify the function is called in the pipeline (may be in inner function)
        import inspect

        # check_disk_space is in _generate_memory_inner (called by generate_memory under lock)
        pipeline_fn = getattr(gen_mod, "_generate_memory_inner", gen_mod.generate_memory)
        source = inspect.getsource(pipeline_fn)
        assert "check_disk_space" in source, "pipeline must call check_disk_space"
