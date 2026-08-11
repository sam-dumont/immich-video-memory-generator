"""Tests for single-instance file lock."""

from __future__ import annotations

import fcntl
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _file_state(path: Path) -> tuple[bool, int | None, int | None]:
    """Return existence, modification time, and size without creating the path."""
    try:
        stat = path.stat()
    except FileNotFoundError:
        return False, None, None
    return True, stat.st_mtime_ns, stat.st_size


class TestPipelineLockWiredInPipeline:
    """PipelineLock is used in generate_memory()."""

    def test_generate_memory_uses_pipeline_lock(self):
        """generate_memory must use PipelineLock."""
        import inspect

        import immich_memories.generate as gen_mod

        assert hasattr(gen_mod, "PipelineLock")
        source = inspect.getsource(gen_mod.generate_memory)
        assert "PipelineLock" in source

    def test_generate_memory_locks_configured_application_state(
        self,
        isolated_user_paths: Path,
        tmp_path: Path,
    ) -> None:
        """Generation must not lock the real user state during isolated runs."""
        from immich_memories.config_loader import Config
        from immich_memories.generate import GenerationParams, generate_memory

        config = Config()
        configured_lock = config.cache.database_path.parent / ".lock"
        real_lock = Path.home() / ".immich-memories" / ".lock"
        real_lock_before = _file_state(real_lock)
        fake_home = tmp_path / "fake-home"
        legacy_lock = fake_home / ".immich-memories" / ".lock"
        result_path = tmp_path / "result.mp4"
        params = GenerationParams(
            clips=[MagicMock()],
            output_path=tmp_path / "output.mp4",
            config=config,
        )

        assert configured_lock.parent == config.cache.database_path.parent
        assert configured_lock.is_relative_to(isolated_user_paths)

        with (
            patch("immich_memories.generate.Path.home", return_value=fake_home),
            patch(
                "immich_memories.generate._generate_memory_inner",
                return_value=result_path,
            ) as mock_inner,
        ):
            result = generate_memory(params)

        assert _file_state(real_lock) == real_lock_before
        assert result == result_path
        mock_inner.assert_called_once_with(params)
        assert configured_lock.exists()
        assert not legacy_lock.exists()

    def test_default_database_keeps_production_lock_path(self, tmp_path: Path) -> None:
        """The default database path must retain the existing production lock location."""
        from immich_memories.config_loader import Config
        from immich_memories.generate import GenerationParams, generate_memory

        config = Config(cache={"database": "~/.immich-memories/cache.db"})
        params = GenerationParams(
            clips=[MagicMock()],
            output_path=tmp_path / "output.mp4",
            config=config,
        )
        expected_lock = Path.home() / ".immich-memories" / ".lock"

        with (
            patch("immich_memories.generate.PipelineLock") as mock_lock,
            patch(
                "immich_memories.generate._generate_memory_inner",
                return_value=tmp_path / "result.mp4",
            ),
        ):
            generate_memory(params)

        mock_lock.assert_called_once_with(expected_lock)


class TestPipelineLock:
    """PipelineLock should prevent concurrent pipeline runs."""

    def test_acquires_lock_on_enter(self, tmp_path: Path):
        from immich_memories.generate import PipelineLock

        lock_path = tmp_path / ".lock"
        with PipelineLock(lock_path):
            assert lock_path.exists()

    def test_releases_lock_on_exit(self, tmp_path: Path):
        from immich_memories.generate import PipelineLock

        lock_path = tmp_path / ".lock"
        with PipelineLock(lock_path):
            pass
        # After exit, another process should be able to acquire the lock
        fd = lock_path.open("w")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()

    def test_raises_when_already_locked(self, tmp_path: Path):
        from immich_memories.generate import GenerationError, PipelineLock

        lock_path = tmp_path / ".lock"

        # Acquire lock from "another process"
        fd = lock_path.open("w")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

        try:
            with (
                pytest.raises(GenerationError, match="Another instance"),
                PipelineLock(lock_path),
            ):
                pass
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            fd.close()

    def test_creates_parent_directory(self, tmp_path: Path):
        from immich_memories.generate import PipelineLock

        lock_path = tmp_path / "subdir" / ".lock"
        with PipelineLock(lock_path):
            assert lock_path.parent.exists()
