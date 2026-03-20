"""Tests for single-instance file lock."""

from __future__ import annotations

import fcntl
from pathlib import Path

import pytest


class TestPipelineLockWiredInPipeline:
    """PipelineLock is used in generate_memory()."""

    def test_generate_memory_uses_pipeline_lock(self):
        """generate_memory must use PipelineLock."""
        import inspect

        import immich_memories.generate as gen_mod

        assert hasattr(gen_mod, "PipelineLock")
        source = inspect.getsource(gen_mod.generate_memory)
        assert "PipelineLock" in source


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
