"""--no-render must not claim it saved a file it never wrote.

_finish_without_rendering returns a path — it always did, for --dry-run — and
the CLI logged it unconditionally. Measured on a real run: zero encode
operations, nothing on disk, and "Video saved to: …/all_monthly_highlights_…"
in the output. Anyone using the flag goes looking for output that never existed.

These assert on the logger rather than stdout: print_success writes through
immich_memories.cli, so a capsys test passes alone and fails in the suite.
"""

import logging
from pathlib import Path

import pytest

from immich_memories.cli._generate_display import _print_generation_result
from immich_memories.cli._helpers import set_quiet_mode


@pytest.fixture(autouse=True)
def _quiet():
    """Pin the output branch.

    print_success writes to an active display, or the logger under quiet mode,
    or the console — so which stream carries the message depends on state other
    tests may have set. Choosing one makes these tests say what they mean.
    """
    set_quiet_mode(True)
    yield
    set_quiet_mode(False)


def _messages(caplog) -> str:
    return "\n".join(record.message for record in caplog.records)


def test_no_render_does_not_claim_a_file(caplog) -> None:
    with caplog.at_level(logging.INFO, logger="immich_memories.cli"):
        _print_generation_result(
            dry_run=False,
            no_render=True,
            result_path=Path("/tmp/x.mp4"),
            should_upload=False,
            album_name=None,
        )

    out = _messages(caplog)
    assert "Video saved to" not in out, f"claimed a file it never wrote: {out!r}"
    assert "no video" in out.lower()


def test_a_real_run_still_reports_its_file(caplog) -> None:
    with caplog.at_level(logging.INFO, logger="immich_memories.cli"):
        _print_generation_result(
            dry_run=False,
            no_render=False,
            result_path=Path("/tmp/x.mp4"),
            should_upload=False,
            album_name=None,
        )

    assert "Video saved to" in _messages(caplog)


def test_dry_run_is_unchanged(caplog) -> None:
    with caplog.at_level(logging.INFO, logger="immich_memories.cli"):
        _print_generation_result(
            dry_run=True,
            no_render=False,
            result_path=Path("/tmp/x.mp4"),
            should_upload=False,
            album_name=None,
        )

    assert "Dry-run planning complete" in _messages(caplog)
