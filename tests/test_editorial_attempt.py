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
    from immich_memories.analysis.llm_metrics import active, record_reply
    from immich_memories.analysis.llm_usage_record import USAGE_FILE

    attempt = EditorialAttempt(tmp_path, request={})
    with pytest.raises(type(error)), attempt:
        record_reply(prompt_tokens=101, completion_tokens=7, model="reader")
        raise error
    usage = json.loads((attempt.directory / USAGE_FILE).read_text())
    assert usage["calls"] == 1
    assert usage["prompt_tokens"] == 101
    assert usage["completion_tokens"] == 7
    assert active() is None
    record = read_editorial_attempt(attempt.directory)
    assert record["status"] == status
    assert record["error_type"] == type(error).__name__
    assert record["finished_at"]


def test_process_crash_is_interrupted_without_a_time_based_guess(tmp_path):
    code = """
import os,sys
from pathlib import Path
from immich_memories.operations.editorial_attempt import EditorialAttempt
from immich_memories.analysis.llm_metrics import record_reply
with EditorialAttempt(Path(sys.argv[1]), request={}) as attempt:
    record_reply(prompt_tokens=91, completion_tokens=9, model='reader')
    attempt.stage('Reading events')
    os._exit(9)
"""
    process = subprocess.run([sys.executable, "-c", code, str(tmp_path)], check=False)
    assert process.returncode == 9
    directory = next((tmp_path / "attempts").iterdir())
    record = read_editorial_attempt(directory)
    assert record["status"] == "interrupted"
    assert record["stage"] == "Reading events"
    usage = json.loads((directory / "llm-usage.json").read_text())
    assert usage["prompt_tokens"] == 91
    assert usage["completion_tokens"] == 9
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


def test_completed_attempt_records_the_model_calls_it_made_per_stage_family(tmp_path):
    families = {
        "worthy": {"asked": 2, "cache_hits": 1, "wall_seconds": 4.5},
        "story-pick": {"asked": 4, "cache_hits": 0, "wall_seconds": 12.25},
    }
    with EditorialAttempt(tmp_path, request={}) as attempt:
        attempt.complete(selected=4, calls_by_stage=families)

    record = json.loads((attempt.directory / "status.private.json").read_text())
    assert record["calls_by_stage"] == families


def test_an_attempt_that_made_no_recorded_calls_omits_the_stage_families(tmp_path):
    with EditorialAttempt(tmp_path, request={}) as attempt:
        attempt.complete(selected=1)

    assert "calls_by_stage" not in read_editorial_attempt(attempt.directory)


def test_selection_spend_is_saved_at_progress_and_completion_without_rendering(tmp_path):
    from immich_memories.analysis.llm_metrics import record_reply
    from immich_memories.analysis.llm_usage_record import USAGE_FILE

    with EditorialAttempt(tmp_path, request={}) as attempt:
        record_reply(prompt_tokens=120, completion_tokens=14, model="reader")
        attempt.stage("Editing the memory")
        checkpoint = json.loads((attempt.directory / USAGE_FILE).read_text())
        assert checkpoint["calls"] == 1
        assert checkpoint["prompt_tokens"] == 120
        record_reply(prompt_tokens=30, completion_tokens=6, model="reader")
        attempt.complete(selected=2)

    final = json.loads((attempt.directory / USAGE_FILE).read_text())
    assert final["calls"] == 2
    assert final["prompt_tokens"] == 150
    assert final["completion_tokens"] == 20
    assert final["by_model"]["reader"]["calls"] == 2


def test_selection_attempts_have_separate_spend_without_erasing_the_run_total(tmp_path):
    from immich_memories.analysis.llm_metrics import collecting, record_reply
    from immich_memories.analysis.llm_usage_record import USAGE_FILE

    with collecting() as run:
        record_reply(prompt_tokens=100, model="before-selection")
        directories = []
        for tokens in (10, 20):
            with EditorialAttempt(tmp_path, request={}) as attempt:
                record_reply(prompt_tokens=tokens, model="reader")
                attempt.complete(selected=1)
                directories.append(attempt.directory)
        record_reply(prompt_tokens=40, model="after-selection")

    assert run.prompt_tokens == 170
    assert [json.loads((d / USAGE_FILE).read_text())["prompt_tokens"] for d in directories] == [
        10,
        20,
    ]
