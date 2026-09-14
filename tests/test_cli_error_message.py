"""A run's last line must never be `Error: ` with nothing after it.

Measured 2026-09-14: a local model server restarted mid-request and the CLI printed
exactly that. httpx raises a read error whose own message is empty, so the one place
that could still say something is the type and the stage the run had reached.
"""

from __future__ import annotations

import httpx

from immich_memories.cli._helpers import described_error
from immich_memories.operations.cut_progress import StageUpdate, announce_stage


def test_an_exception_with_a_message_is_printed_as_written():
    assert described_error(RuntimeError("story-episodes-3 answer could not be read")) == (
        "story-episodes-3 answer could not be read"
    )


def test_an_empty_message_falls_back_to_the_type_and_the_last_stage():
    announce_stage(StageUpdate("Reading the period account: page 3"))

    described = described_error(httpx.ReadError(""))

    assert described.strip() != ""
    assert "httpx.ReadError" in described
    assert "Reading the period account: page 3" in described
