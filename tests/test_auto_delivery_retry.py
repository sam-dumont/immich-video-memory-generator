"""Daily automation retry contracts for durable Immich delivery."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from immich_memories.api.compatibility import ApiVersionPolicy
from immich_memories.automation.candidates import CandidateCategory, MemoryCandidate
from immich_memories.automation.models import AutoAction, AutoOutcome, ProcessResult
from immich_memories.automation.runner import AutoRunner
from immich_memories.config_loader import Config
from immich_memories.tracking import DeliveryStatus, RunMetadata


def _config(
    tmp_path: Path,
    *,
    api_version: ApiVersionPolicy | str = ApiVersionPolicy.AUTO,
) -> Config:
    return Config(
        immich={
            "url": "http://immich.test:2283",
            "api_key": "retry-api-key",
            "api_version": api_version,
        },
        cache={
            "database": str(tmp_path / "runs.db"),
            "directory": str(tmp_path / "cache"),
        },
    )


def _save_pending_auto_run(
    runner: AutoRunner,
    tmp_path: Path,
    *,
    run_id: str = "pending-run",
    delivery_album: str | None = "Original Album",
    source: str = "auto",
) -> RunMetadata:
    output_path = tmp_path / f"{run_id}.mp4"
    output_path.write_bytes(b"validated-memory")
    now = datetime.now(tz=UTC)
    run = RunMetadata(
        run_id=run_id,
        created_at=now - timedelta(minutes=2),
        completed_at=now - timedelta(minutes=1),
        status="completed",
        source=source,
        automation_attempt_id="original-generation-attempt",
        output_path=str(output_path),
        delivery_status=DeliveryStatus.PENDING,
        delivery_album=delivery_album,
    )
    runner.db.save_run(run)
    return run


@pytest.mark.parametrize(
    "api_version",
    [ApiVersionPolicy.AUTO, ApiVersionPolicy.V2, ApiVersionPolicy.V3],
)
def test_pending_delivery_succeeds_before_cooldown_or_candidate_selection(
    tmp_path: Path,
    api_version: ApiVersionPolicy,
) -> None:
    """Removing the retry branch would let cooldown hide already-rendered work."""
    config = _config(tmp_path, api_version=api_version)
    execute = MagicMock()
    runner = AutoRunner(config, execute=execute)
    pending = _save_pending_auto_run(runner, tmp_path)
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = False

    def upload_memory(*, video_path: Path, album_name: str | None) -> dict[str, str]:
        attempt = runner.state.get_last_attempt()
        assert attempt is not None
        assert attempt.outcome is AutoOutcome.RUNNING
        assert attempt.id != pending.automation_attempt_id
        assert video_path == Path(pending.output_path or "")
        assert album_name == "Original Album"
        return {"asset_id": "asset-retried", "album_id": "album-existing"}

    client.upload_memory.side_effect = upload_memory

    with (
        patch(
            "immich_memories.api.immich.SyncImmichClient",
            return_value=client,
        ) as client_factory,
        patch.object(runner, "suggest") as suggest,
        patch.object(runner.state, "finish_attempt", wraps=runner.state.finish_attempt) as finish,
    ):
        result = runner.run_one(cooldown_hours=24)

    assert result.outcome is AutoOutcome.COMPLETED
    assert result.action is AutoAction.DELIVERY_RETRY
    assert result.reason == "pending delivery completed"
    assert result.candidate is None
    assert result.run_id == pending.run_id
    assert result.output_path == Path(pending.output_path or "")
    client_factory.assert_called_once_with(
        base_url="http://immich.test:2283",
        api_key="retry-api-key",
        api_version=api_version,
    )
    client.upload_memory.assert_called_once_with(
        video_path=Path(pending.output_path or ""),
        album_name="Original Album",
    )
    suggest.assert_not_called()
    execute.assert_not_called()
    finish.assert_called_once()
    saved = runner.db.get_run(pending.run_id)
    assert saved is not None
    assert saved.delivery_status is DeliveryStatus.DELIVERED
    assert saved.delivery_attempts == 1
    assert saved.immich_asset_id == "asset-retried"
    assert saved.automation_attempt_id == "original-generation-attempt"
    attempt = runner.state.get_last_attempt()
    assert attempt is not None
    assert attempt.run_id == pending.run_id
    assert attempt.outcome is AutoOutcome.COMPLETED


def test_failed_pending_delivery_stays_pending_and_stops_the_invocation(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Losing the failure transition would rerender work and leak retry credentials."""
    config = _config(tmp_path)
    execute = MagicMock()
    runner = AutoRunner(config, execute=execute)
    pending = _save_pending_auto_run(runner, tmp_path)
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    client.upload_memory.side_effect = RuntimeError("server echoed retry-api-key")
    caplog.set_level(logging.WARNING)

    with (
        patch("immich_memories.api.immich.SyncImmichClient", return_value=client),
        patch.object(runner, "suggest") as suggest,
        patch.object(runner.state, "finish_attempt", wraps=runner.state.finish_attempt) as finish,
    ):
        result = runner.run_one(cooldown_hours=24)

    assert result.outcome is AutoOutcome.FAILED
    assert result.action is AutoAction.DELIVERY_RETRY
    assert result.reason == "pending delivery failed"
    assert result.run_id == pending.run_id
    assert result.output_path == Path(pending.output_path or "")
    assert result.error == "server echoed ***"
    assert "retry-api-key" not in caplog.text
    suggest.assert_not_called()
    execute.assert_not_called()
    finish.assert_called_once()
    saved = runner.db.get_run(pending.run_id)
    assert saved is not None
    assert saved.delivery_status is DeliveryStatus.PENDING
    assert saved.delivery_attempts == 1
    assert saved.delivery_error == "server echoed ***"
    assert saved.immich_asset_id is None
    assert saved.automation_attempt_id == "original-generation-attempt"
    attempt = runner.state.get_last_attempt()
    assert attempt is not None
    assert attempt.outcome is AutoOutcome.FAILED
    assert attempt.run_id == pending.run_id
    assert attempt.error == "server echoed ***"


def test_pending_delivery_dry_run_is_read_only_and_stops_the_invocation(
    tmp_path: Path,
) -> None:
    """Crossing the client or DB boundary would make a retry dry-run destructive."""
    config = _config(tmp_path)
    execute = MagicMock()
    runner = AutoRunner(config, execute=execute)
    pending = _save_pending_auto_run(runner, tmp_path)

    with (
        patch("immich_memories.api.immich.SyncImmichClient") as client_factory,
        patch.object(runner, "suggest") as suggest,
        patch.object(runner.state, "finish_attempt", wraps=runner.state.finish_attempt) as finish,
    ):
        result = runner.run_one(force=True, dry_run=True)

    assert result.outcome is AutoOutcome.DRY_RUN
    assert result.action is AutoAction.DELIVERY_RETRY
    assert result.reason == "pending delivery dry run"
    assert result.run_id == pending.run_id
    assert result.output_path == Path(pending.output_path or "")
    client_factory.assert_not_called()
    suggest.assert_not_called()
    execute.assert_not_called()
    finish.assert_called_once()
    saved = runner.db.get_run(pending.run_id)
    assert saved is not None
    assert saved.delivery_status is DeliveryStatus.PENDING
    assert saved.delivery_attempts == 0
    assert saved.delivery_error is None
    assert saved.immich_asset_id is None


@pytest.mark.parametrize("upload_result", [{}, {"asset_id": "   "}])
def test_pending_delivery_rejects_missing_or_blank_asset_identity(
    tmp_path: Path,
    upload_result: dict[str, str],
) -> None:
    """A transport response is not delivery proof without an Immich asset ID."""
    runner = AutoRunner(_config(tmp_path))
    pending = _save_pending_auto_run(runner, tmp_path)
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    client.upload_memory.return_value = upload_result

    with (
        patch("immich_memories.api.immich.SyncImmichClient", return_value=client),
        patch.object(runner, "suggest") as suggest,
    ):
        result = runner.run_one(force=True)

    assert result.outcome is AutoOutcome.FAILED
    assert result.action is AutoAction.DELIVERY_RETRY
    assert result.error == "Immich upload returned no asset ID"
    suggest.assert_not_called()
    saved = runner.db.get_run(pending.run_id)
    assert saved is not None
    assert saved.delivery_status is DeliveryStatus.PENDING
    assert saved.delivery_attempts == 1
    assert saved.immich_asset_id is None


def test_no_pending_delivery_preserves_candidate_flow_and_generation_action(
    tmp_path: Path,
) -> None:
    """Treating an empty delivery queue as terminal would suppress normal discovery."""
    runner = AutoRunner(_config(tmp_path))

    with (
        patch("immich_memories.api.immich.SyncImmichClient") as client_factory,
        patch.object(runner, "suggest", return_value=[]) as suggest,
    ):
        result = runner.run_one(force=True)

    assert result.outcome is AutoOutcome.SKIPPED
    assert result.action is AutoAction.GENERATION
    assert result.reason == "no eligible candidates"
    client_factory.assert_not_called()
    suggest.assert_called_once_with(limit=1)


def test_pending_delivery_count_is_source_scoped_and_keeps_missing_artifacts(
    tmp_path: Path,
) -> None:
    """Queue health requires a path but counts it even when the file is missing."""
    runner = AutoRunner(_config(tmp_path))
    existing = _save_pending_auto_run(runner, tmp_path, run_id="auto-existing")
    missing = _save_pending_auto_run(runner, tmp_path, run_id="auto-missing")
    Path(missing.output_path or "").unlink()
    _save_pending_auto_run(
        runner,
        tmp_path,
        run_id="manual-pending",
        source="manual",
    )
    now = datetime.now(tz=UTC)
    runner.db.save_run(
        RunMetadata(
            run_id="auto-pathless",
            created_at=now - timedelta(minutes=2),
            completed_at=now - timedelta(minutes=1),
            status="completed",
            source="auto",
            output_path=None,
            delivery_status=DeliveryStatus.PENDING,
        )
    )

    assert Path(existing.output_path or "").is_file()
    assert runner.db.count_pending_deliveries(source="auto") == 2
    assert runner.db.count_pending_deliveries(source="manual") == 1


def test_initial_automation_upload_uses_the_retry_album_provenance(tmp_path: Path) -> None:
    """Initial generation and a future retry must address the same Immich album."""
    config = _config(tmp_path)
    config.automation.upload_to_immich = True
    config.automation.album_name = "Daily Auto Memories"
    candidate = MemoryCandidate(
        memory_type="monthly_highlights",
        category=CandidateCategory.MONTHLY_REVIEW,
        date_range_start=date(2026, 7, 1),
        date_range_end=date(2026, 7, 31),
        person_names=[],
        memory_key="monthly:2026-07",
        score=0.9,
        reason="July",
        asset_count=90,
    )
    execute = MagicMock(return_value=ProcessResult(7, "", "expected child failure"))
    runner = AutoRunner(config, execute=execute)

    with patch.object(runner, "suggest", return_value=[candidate]):
        result = runner.run_one(force=True)

    assert result.outcome is AutoOutcome.FAILED
    command = execute.call_args.args[0]
    assert "--upload-to-immich" in command
    album_index = command.index("--album")
    assert command[album_index + 1] == "Daily Auto Memories"
