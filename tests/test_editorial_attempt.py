"""Attempt isolation, restart truth and atomic private progress records."""

import json
import stat
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest

from immich_memories.operations.cancellation import PipelineCancelled
from immich_memories.operations.editorial_attempt import EditorialAttempt, read_editorial_attempt
from immich_memories.security import write_secret_file


def test_identical_stage_updates_do_not_rewrite_status_but_terminal_state_is_saved(tmp_path):
    # WHY: count the private-file writes; the real writer still runs so the record on disk is real
    with patch(
        "immich_memories.operations.editorial_attempt.write_secret_file", wraps=write_secret_file
    ) as write:
        with EditorialAttempt(tmp_path, request={}) as attempt:
            attempt.stage("Preparing cached previews")
            initial_writes = write.call_count
            for _ in range(1500):
                attempt.stage("Preparing cached previews")
            assert write.call_count == initial_writes
            attempt.stage("Reading the period")
            assert write.call_count == initial_writes + 1
            attempt.complete(selected=3)
        assert write.call_count == initial_writes + 2
    assert read_editorial_attempt(attempt.directory)["status"] == "complete"


def test_concurrent_attempts_have_separate_artifacts_and_truthful_status(tmp_path):
    request = {"product": "monthly_highlights", "target_seconds": 60, "audience": "family"}
    with EditorialAttempt(tmp_path, request=request) as first:
        first.stage("Reading the period")
        with EditorialAttempt(tmp_path, request=request) as second:
            assert first.directory != second.directory
            assert read_editorial_attempt(first.directory)["status"] == "running"
            assert read_editorial_attempt(second.directory)["status"] == "running"
            second.complete(selected=12)
        assert read_editorial_attempt(second.directory)["status"] == "complete"
        first.complete(selected=13)
    first_record = read_editorial_attempt(first.directory)
    assert first_record["selected_carriers"] == 13
    assert first_record["status"] == "complete"
    # An older run finishing must not replace the pointer to the latest started run.
    assert (
        json.loads((tmp_path / "latest-attempt.private.json").read_text())["attempt_id"]
        == second.attempt_id
    )


@pytest.mark.parametrize(
    "error,status", [(PipelineCancelled("stop"), "cancelled"), (ValueError("bad data"), "failed")]
)
def test_cancel_and_failure_are_durable_without_becoming_completed(tmp_path, error, status):
    attempt = EditorialAttempt(tmp_path, request={})
    with pytest.raises(type(error)), attempt:
        raise error
    record = read_editorial_attempt(attempt.directory)
    assert record["status"] == status
    assert record["error_type"] == type(error).__name__
    assert record["finished_at"]


def test_process_crash_is_interrupted_without_a_time_based_guess(tmp_path):
    code = """
import os,sys
from pathlib import Path
from immich_memories.operations.editorial_attempt import EditorialAttempt
with EditorialAttempt(Path(sys.argv[1]), request={}) as attempt:
    attempt.stage('Reading events')
    os._exit(9)
"""
    process = subprocess.run([sys.executable, "-c", code, str(tmp_path)], check=False)
    assert process.returncode == 9
    directory = next((tmp_path / "attempts").iterdir())
    record = read_editorial_attempt(directory)
    assert record["status"] == "interrupted"
    assert record["stage"] == "Reading events"
    assert json.loads((directory / "status.private.json").read_text())["status"] == "running"


def test_private_record_writers_do_not_share_a_temporary_filename(tmp_path):
    path = tmp_path / "record.json"

    def write(number):
        write_secret_file(path, json.dumps({"number": number, "body": str(number) * 10000}))

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(write, range(40)))
    record = json.loads(path.read_text())
    assert record["body"] == str(record["number"]) * 10000
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert list(tmp_path.iterdir()) == [path]
