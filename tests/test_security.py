"""Tests for path validation and sanitization."""

from __future__ import annotations

from pathlib import Path

import pytest

from immich_memories import security
from immich_memories.config_loader import Config
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


def test_configured_secret_values_include_only_current_secret_fields() -> None:
    """Credential discovery must cover the config model without sweeping in ordinary values."""
    expected = {
        "immich-credential",
        "primary-llm-credential",
        "title-llm-credential",
        "musicgen-credential",
        "ace-step-credential",
        "basic-auth-password",
        "oidc-client-secret",
        "https://notify.test/embedded-credential",
    }
    config = Config(
        immich={"url": "http://immich.test", "api_key": "immich-credential"},
        llm={
            "base_url": "http://llm.test/v1",
            "model": "ordinary-model-name",
            "api_key": "primary-llm-credential",
        },
        title_llm={"api_key": "title-llm-credential"},
        musicgen={"base_url": "http://music.test", "api_key": "musicgen-credential"},
        ace_step={"api_url": "http://ace.test", "api_key": "ace-step-credential"},
        auth={
            "username": "ordinary-user-name",
            "password": "basic-auth-password",
            "client_id": "ordinary-client-id",
            "client_secret": "oidc-client-secret",
        },
        notifications={"urls": ["https://notify.test/embedded-credential"]},
    )

    actual = security.configured_secret_values(config)

    assert set(actual) == expected
    assert actual == tuple(sorted(actual, key=len, reverse=True))
    assert "ordinary-model-name" not in actual
    assert "ordinary-user-name" not in actual
    assert "ordinary-client-id" not in actual
