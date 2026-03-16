"""Tests for path validation and sanitization."""

from __future__ import annotations

from pathlib import Path

import pytest

from immich_memories.security import sanitize_filename, validate_path


class TestValidatePath:
    def test_resolves_relative_path(self, tmp_path):
        f = tmp_path / "test.mp4"
        f.touch()
        result = validate_path(str(f), must_exist=True)
        assert result.is_absolute()

    def test_rejects_nonexistent_when_must_exist(self):
        with pytest.raises(ValueError, match="does not exist"):
            validate_path("/nonexistent/path.mp4", must_exist=True)

    def test_allows_nonexistent_when_must_exist_false(self):
        result = validate_path("/nonexistent/path.mp4", must_exist=False)
        assert result == Path("/nonexistent/path.mp4")

    def test_rejects_wrong_extension(self, tmp_path):
        f = tmp_path / "test.txt"
        f.touch()
        with pytest.raises(ValueError, match="not in allowed list"):
            validate_path(f, allowed_extensions={".mp4", ".mov"}, must_exist=True)

    def test_accepts_valid_extension(self, tmp_path):
        f = tmp_path / "test.mp4"
        f.touch()
        result = validate_path(f, allowed_extensions={".mp4"}, must_exist=True)
        assert result.suffix == ".mp4"


class TestSanitizeFilename:
    def test_strips_control_chars(self):
        assert sanitize_filename("test\x00file.mp4") == "testfile.mp4"

    def test_replaces_slashes(self):
        assert sanitize_filename("path/to/file.mp4") == "path_to_file.mp4"
