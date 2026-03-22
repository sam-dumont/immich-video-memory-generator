"""Tests for _get_storage_secret — env var > file > auto-generate."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from immich_memories.ui.app import _get_storage_secret


@pytest.fixture()
def _clean_env():
    """Remove storage secret env var if present."""
    old = os.environ.pop("IMMICH_MEMORIES_STORAGE_SECRET", None)
    yield
    if old is not None:
        os.environ["IMMICH_MEMORIES_STORAGE_SECRET"] = old


def test_returns_env_var_when_set():
    with patch.dict(os.environ, {"IMMICH_MEMORIES_STORAGE_SECRET": "env-secret-42"}):
        assert _get_storage_secret() == "env-secret-42"


@pytest.mark.usefixtures("_clean_env")
def test_reads_from_file_when_exists(tmp_path: Path):
    secret_file = tmp_path / ".storage_secret"
    secret_file.write_text("file-secret-abc\n")

    with patch("immich_memories.ui.app.Path.home", return_value=tmp_path / "fake-home"):
        # WHY: patching Path.home so it uses tmp_path, avoiding real filesystem
        (tmp_path / "fake-home" / ".immich-memories").mkdir(parents=True)
        (tmp_path / "fake-home" / ".immich-memories" / ".storage_secret").write_text(
            "file-secret-abc\n"
        )
        result = _get_storage_secret()

    assert result == "file-secret-abc"


@pytest.mark.usefixtures("_clean_env")
def test_generates_and_persists_when_no_file(tmp_path: Path):
    fake_home = tmp_path / "fresh-home"
    fake_home.mkdir()

    with patch("immich_memories.ui.app.Path.home", return_value=fake_home):
        secret = _get_storage_secret()

    assert len(secret) == 64  # token_hex(32) = 64 chars
    secret_path = fake_home / ".immich-memories" / ".storage_secret"
    assert secret_path.exists()
    assert secret_path.read_text() == secret
    assert oct(secret_path.stat().st_mode)[-3:] == "600"


def test_env_var_takes_priority_over_file(tmp_path: Path):
    fake_home = tmp_path / "has-file"
    (fake_home / ".immich-memories").mkdir(parents=True)
    (fake_home / ".immich-memories" / ".storage_secret").write_text("from-file")

    with (
        patch.dict(os.environ, {"IMMICH_MEMORIES_STORAGE_SECRET": "from-env"}),
        patch("immich_memories.ui.app.Path.home", return_value=fake_home),
    ):
        assert _get_storage_secret() == "from-env"
