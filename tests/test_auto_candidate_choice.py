"""An explicit suggestion key selects that eligible candidate, never a replacement."""

from dataclasses import replace
from datetime import date
from unittest.mock import patch

from immich_memories.automation.candidates import CandidateCategory, MemoryCandidate
from immich_memories.automation.models import AutoOutcome
from immich_memories.automation.runner import AutoRunner
from immich_memories.config_loader import Config


def test_explicit_candidate_can_choose_the_second_suggestion(tmp_path):
    config = Config(cache={"database": str(tmp_path / "runs.db")})
    first = MemoryCandidate(
        memory_type="monthly_highlights",
        category=CandidateCategory.MONTHLY_REVIEW,
        date_range_start=date(2024, 6, 1),
        date_range_end=date(2024, 6, 30),
        person_names=[],
        memory_key="june",
        score=0.8,
        reason="June",
        asset_count=133,
    )
    chosen = replace(first, memory_key="may", reason="May")
    runner = AutoRunner(config)
    # WHY: replace live library discovery; exercise the real lease, decision and attempt store.
    with patch.object(runner, "suggest", return_value=[first, chosen]):
        result = runner.run_one(candidate_key="may", dry_run=True)
    assert result.outcome is AutoOutcome.DRY_RUN
    assert result.candidate == chosen
    assert runner.state.get_last_attempt().memory_key == "may"


def test_unknown_candidate_fails_without_generating_a_substitute(tmp_path):
    config = Config(cache={"database": str(tmp_path / "runs.db")})
    runner = AutoRunner(config)
    # WHY: an empty live-discovery result models a suggestion that is no longer eligible.
    with patch.object(runner, "suggest", return_value=[]):
        result = runner.run_one(candidate_key="stale", dry_run=True)
    assert result.outcome is AutoOutcome.FAILED
    assert "no longer eligible: stale" in result.reason
    assert result.candidate is None


def test_cli_accepts_the_key_printed_by_suggest(tmp_path):
    from click.testing import CliRunner

    from immich_memories.automation.models import AutoRunResult
    from immich_memories.cli.auto_cmd import auto

    config = Config(cache={"database": str(tmp_path / "runs.db")})
    # WHY: do not launch a generation subprocess while testing the public Click interface.
    with patch.object(
        AutoRunner, "run_one", return_value=AutoRunResult(AutoOutcome.DRY_RUN, "dry run")
    ) as execute:
        result = CliRunner().invoke(
            auto,
            ["run", "--candidate", "june", "--dry-run"],
            obj={"config": config, "config_path": None},
        )
    assert result.exit_code == 0, result.output
    assert execute.call_args.kwargs["candidate_key"] == "june"


def test_successful_child_output_is_retained_in_full_and_redacted(tmp_path):
    from datetime import datetime

    from immich_memories.automation.models import ProcessResult
    from immich_memories.operations.auto_output import output_log_path
    from immich_memories.tracking.models import RunMetadata

    config = Config(
        immich={"api_key": "fixture-credential"},
        cache={"database": str(tmp_path / "runs.db"), "directory": str(tmp_path / "cache")},
    )
    output = tmp_path / "memory.mp4"
    output.write_bytes(b"fixture video")
    candidate = MemoryCandidate(
        memory_type="monthly_highlights",
        category=CandidateCategory.MONTHLY_REVIEW,
        date_range_start=date(2024, 6, 1),
        date_range_end=date(2024, 6, 30),
        person_names=[],
        memory_key="june",
        score=0.8,
        reason="June",
        asset_count=133,
    )

    def execute(command):
        attempt_id = next(
            arg.split("=", 1)[1] for arg in command if arg.startswith("--automation-attempt-id=")
        )
        runner.db.save_run(
            RunMetadata(
                run_id="fixture-run",
                created_at=datetime.now(),
                status="completed",
                source="auto",
                memory_key="june",
                automation_attempt_id=attempt_id,
                output_path=str(output),
            )
        )
        return ProcessResult(
            0, "beginning\n" + "x" * 20_000 + "\nfixture-credential\nend", "warning"
        )

    # WHY: replace generation writes with a child completion; use the real database and log files.
    runner = AutoRunner(config, execute=execute)
    with patch.object(runner, "suggest", return_value=[candidate]):
        result = runner.run_one()
    assert result.outcome is AutoOutcome.COMPLETED
    path = output_log_path(config.cache.cache_path, runner.state.get_last_attempt().id)
    text = path.read_text()
    assert "beginning" in text and "end" in text and "warning" in text
    assert "x" * 20_000 in text and "fixture-credential" not in text
    assert path.stat().st_mode & 0o777 == 0o600
