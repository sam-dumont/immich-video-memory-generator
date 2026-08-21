"""`immich-memories config` must not print the API key it already has.

`hide_input=True` hides what the user types, not the default, so Click rendered:

    API key [SECRET-KEY-12345]:

The key has full library access, and that line goes to the terminal, to
scrollback, and to any recording or screen share.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from immich_memories.cli import main

_KEY = "sk-live-full-library-access"


def _run(tmp_path: Path, answers: str):
    config = tmp_path / "config.yaml"
    config.write_text(f"immich:\n  url: http://immich.test:2283\n  api_key: {_KEY}\n")
    # WHY: the real home and the real Immich server
    with (
        # WHY: this command writes config. Pinning home as well as --config means
        # a regression in the save path cannot reach a real one -- an earlier
        # version of this test overwrote mine.
        patch.object(Path, "home", classmethod(lambda _cls: tmp_path / "home")),
        # WHY: the command offers to contact Immich after saving.
        patch("immich_memories.api.immich.SyncImmichClient"),
    ):
        result = CliRunner().invoke(main, ["--config", str(config), "config"], input=answers)
    return result, config


def test_the_existing_key_is_never_printed(tmp_path: Path):
    result, _ = _run(tmp_path, answers="\n\nn\n")

    assert _KEY not in result.output


def test_pressing_enter_keeps_the_existing_key(tmp_path: Path):
    """Not showing it must not mean losing it."""
    _, config = _run(tmp_path, answers="\n\nn\n")

    assert _KEY in config.read_text()


def test_the_user_is_told_a_key_is_already_set(tmp_path: Path):
    """Otherwise an empty prompt looks like there is nothing configured."""
    result, _ = _run(tmp_path, answers="\n\nn\n")

    assert "configured" in result.output.lower()


def test_a_new_key_replaces_the_old_one(tmp_path: Path):
    _, config = _run(tmp_path, answers="\nsk-replacement\nn\n")

    saved = config.read_text()
    assert "sk-replacement" in saved
    assert _KEY not in saved
