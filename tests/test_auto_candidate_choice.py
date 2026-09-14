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


def test_explicit_candidate_leaves_the_queued_delivery_untouched(tmp_path):
    """An explicit choice must not spend the attempt's phase on a delivery it skips."""
    from datetime import UTC, datetime, timedelta

    from immich_memories.operations.phases import OperationalPhase, PhaseEvent
    from immich_memories.preflight import CheckResult, CheckStatus
    from immich_memories.tracking import DeliveryStatus, RunMetadata

    config = Config(
        immich={"url": "http://immich.test:2283", "api_key": "fixture-credential"},
        cache={"database": str(tmp_path / "runs.db"), "directory": str(tmp_path / "cache")},
    )
    runner = AutoRunner(config)
    queued_output = tmp_path / "queued.mp4"
    queued_output.write_bytes(b"fixture video")
    now = datetime.now(tz=UTC)
    runner.db.save_run(
        RunMetadata(
            run_id="queued-run",
            created_at=now - timedelta(minutes=2),
            completed_at=now - timedelta(minutes=1),
            status="completed",
            source="auto",
            output_path=str(queued_output),
            delivery_status=DeliveryStatus.PENDING,
        )
    )
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
    # WHY: replace live library discovery and the Immich reachability probe.
    with (
        patch.object(runner, "suggest", return_value=[candidate]),
        patch(
            "immich_memories.preflight.check_immich",
            return_value=CheckResult(name="Immich", status=CheckStatus.OK, message="Connected"),
        ),
    ):
        result = runner.run_one(candidate_key="june", dry_run=True, force=True)

    assert result.outcome is AutoOutcome.DRY_RUN
    assert runner.db.get_run("queued-run").delivery_status is DeliveryStatus.PENDING
    attempt = runner.state.get_last_attempt()
    assert attempt.last_phase is OperationalPhase.DISCOVERY
    # WHY NOT a mock: phase updates are forward-only, so a delivery phase written by a
    # skipped delivery would silently drop every phase the child later reports.
    accepted = runner.state.update_phase(
        attempt.id, PhaseEvent(OperationalPhase.DOWNLOAD, 0, 0, "Downloading", 0.0)
    )
    assert accepted is True


def test_stale_key_is_explained_once_in_the_terminal(tmp_path):
    """The operator reads one sentence, not the same sentence joined to itself."""
    from click.testing import CliRunner

    from immich_memories.cli.auto_cmd import auto

    config = Config(cache={"database": str(tmp_path / "runs.db")})
    # WHY: an empty live-discovery result models a suggestion that is no longer eligible.
    with patch.object(AutoRunner, "suggest", return_value=[]):
        result = CliRunner().invoke(
            auto,
            ["run", "--candidate", "stale-key", "--dry-run"],
            obj={"config": config, "config_path": None},
        )
    assert result.exit_code == 1
    assert result.output.count("Candidate is no longer eligible: stale-key") == 1


def test_timed_out_child_output_is_retained(tmp_path):
    """The longest failure is the one whose transcript is worth the most."""
    import subprocess

    from immich_memories.operations.auto_output import output_log_path

    config = Config(
        immich={"api_key": "fixture-credential"},
        cache={"database": str(tmp_path / "runs.db"), "directory": str(tmp_path / "cache")},
    )
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
        raise subprocess.TimeoutExpired(
            command,
            1.0,
            output="rendered 40 clips\nfixture-credential\n" + "y" * 20_000,
            stderr="killed after the wall clock ran out",
        )

    # WHY: replace the generation child with the timeout the bounded runner raises.
    runner = AutoRunner(config, execute=execute)
    with patch.object(runner, "suggest", return_value=[candidate]):
        result = runner.run_one()
    assert result.outcome is AutoOutcome.FAILED
    text = output_log_path(config.cache.cache_path, runner.state.get_last_attempt().id).read_text()
    assert "rendered 40 clips" in text
    assert "y" * 20_000 in text
    assert "killed after the wall clock ran out" in text
    assert "fixture-credential" not in text
