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


@pytest.fixture(autouse=True)
def _healthy_retry_preflight() -> None:
    """Keep retry tests hermetic while daily automation checks Immich first."""
    from immich_memories.preflight import CheckResult, CheckStatus

    with patch(
        "immich_memories.preflight.check_immich",
        return_value=CheckResult(name="Immich", status=CheckStatus.OK, message="Connected"),
    ):
        yield


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


def test_retry_retries_delivery_persistence_without_reuploading(
    tmp_path: Path,
) -> None:
    """A transient post-upload DB error cannot make the same invocation upload twice."""
    runner = AutoRunner(_config(tmp_path))
    pending = _save_pending_auto_run(runner, tmp_path)
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    client.upload_memory.return_value = {"asset_id": "asset-persisted"}
    mark_delivered = runner.db.mark_delivered
    calls = 0

    def fail_once(run_id: str, asset_id: str) -> RunMetadata:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("temporary durable write failure")
        return mark_delivered(run_id, asset_id)

    with (
        patch("immich_memories.api.immich.SyncImmichClient", return_value=client),
        patch.object(runner.db, "mark_delivered", side_effect=fail_once),
        patch.object(runner, "suggest") as suggest,
    ):
        result = runner.run_one(force=True)

    assert result.outcome is AutoOutcome.COMPLETED
    assert result.action is AutoAction.DELIVERY_RETRY
    assert result.run_id == pending.run_id
    assert result.output_path == Path(pending.output_path or "")
    assert calls == 2
    client.upload_memory.assert_called_once()
    suggest.assert_not_called()
    saved = runner.db.get_run(pending.run_id)
    assert saved is not None
    assert saved.delivery_status is DeliveryStatus.DELIVERED
    assert saved.immich_asset_id == "asset-persisted"


def test_retry_persistence_failure_keeps_retry_identity_without_reuploading(
    tmp_path: Path,
) -> None:
    """A permanent durable-write outage is reported as a retry, never a generation failure."""
    runner = AutoRunner(_config(tmp_path))
    pending = _save_pending_auto_run(runner, tmp_path)
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    client.upload_memory.return_value = {"asset_id": "asset-not-durable"}

    with (
        patch("immich_memories.api.immich.SyncImmichClient", return_value=client),
        patch.object(
            runner.db,
            "mark_delivered",
            side_effect=OSError("durable store rejected retry-api-key"),
        ) as mark_delivered,
        patch.object(runner, "suggest") as suggest,
    ):
        result = runner.run_one(force=True)

    assert result.outcome is AutoOutcome.FAILED
    assert result.action is AutoAction.DELIVERY_RETRY
    assert result.reason == "pending delivery persistence failed"
    assert result.run_id == pending.run_id
    assert result.output_path == Path(pending.output_path or "")
    assert result.error is not None
    assert "retry-api-key" not in result.error
    assert mark_delivered.call_count == 2
    client.upload_memory.assert_called_once()
    suggest.assert_not_called()
    saved = runner.db.get_run(pending.run_id)
    assert saved is not None
    assert saved.delivery_status is DeliveryStatus.PENDING
    assert runner.state.get_last_attempt().run_id == pending.run_id


def test_retry_pending_persistence_failure_keeps_retry_identity(
    tmp_path: Path,
) -> None:
    """A failed upload cannot turn a failed pending-state write into GENERATION."""
    runner = AutoRunner(_config(tmp_path))
    pending = _save_pending_auto_run(runner, tmp_path)
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    client.upload_memory.side_effect = RuntimeError("upload unavailable")

    with (
        patch("immich_memories.api.immich.SyncImmichClient", return_value=client),
        patch.object(
            runner.db,
            "mark_delivery_pending",
            side_effect=OSError("pending durable write failed"),
        ) as mark_pending,
        patch.object(runner, "suggest") as suggest,
    ):
        result = runner.run_one(force=True)

    assert result.outcome is AutoOutcome.FAILED
    assert result.action is AutoAction.DELIVERY_RETRY
    assert result.reason == "pending delivery persistence failed"
    assert result.run_id == pending.run_id
    assert result.output_path == Path(pending.output_path or "")
    assert mark_pending.call_count == 2
    client.upload_memory.assert_called_once()
    suggest.assert_not_called()
    assert runner.state.get_last_attempt().run_id == pending.run_id


def test_retry_does_not_double_count_a_pending_write_that_committed_then_raised(
    tmp_path: Path,
) -> None:
    """An ambiguous pending-state write is checked before retrying its increment."""
    runner = AutoRunner(_config(tmp_path))
    pending = _save_pending_auto_run(runner, tmp_path)
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    client.upload_memory.side_effect = RuntimeError("upload unavailable")
    mark_pending = runner.db.mark_delivery_pending
    calls = 0

    def commit_then_raise(run_id: str, error: str) -> RunMetadata:
        nonlocal calls
        calls += 1
        mark_pending(run_id, error)
        raise OSError("connection closed after durable write")

    with (
        patch("immich_memories.api.immich.SyncImmichClient", return_value=client),
        patch.object(runner.db, "mark_delivery_pending", side_effect=commit_then_raise),
        patch.object(runner, "suggest") as suggest,
    ):
        result = runner.run_one(force=True)

    assert result.outcome is AutoOutcome.FAILED
    assert result.action is AutoAction.DELIVERY_RETRY
    assert result.reason == "pending delivery failed"
    assert result.run_id == pending.run_id
    assert calls == 1
    client.upload_memory.assert_called_once()
    suggest.assert_not_called()
    saved = runner.db.get_run(pending.run_id)
    assert saved is not None
    assert saved.delivery_status is DeliveryStatus.PENDING
    assert saved.delivery_attempts == 1
    assert saved.delivery_error == "upload unavailable"


def test_retry_does_not_repeat_pending_increment_when_its_probe_is_unavailable(
    tmp_path: Path,
) -> None:
    """An unreadable authoritative row is not evidence that an increment did not commit."""
    runner = AutoRunner(_config(tmp_path))
    pending = _save_pending_auto_run(runner, tmp_path)
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    client.upload_memory.side_effect = RuntimeError("upload unavailable")
    get_run = runner.db.get_run
    reads = 0

    def initial_select_then_unavailable(run_id: str) -> RunMetadata | None:
        nonlocal reads
        reads += 1
        if reads == 1:
            return get_run(run_id)
        raise OSError("read replica unavailable")

    with (
        patch("immich_memories.api.immich.SyncImmichClient", return_value=client),
        patch.object(
            runner.db,
            "mark_delivery_pending",
            side_effect=OSError("connection closed during durable write"),
        ) as mark_pending,
        patch.object(runner.db, "get_run", side_effect=initial_select_then_unavailable),
        patch.object(runner, "suggest") as suggest,
    ):
        result = runner.run_one(force=True)

    assert result.outcome is AutoOutcome.FAILED
    assert result.action is AutoAction.DELIVERY_RETRY
    assert result.reason == "pending delivery persistence failed"
    assert result.run_id == pending.run_id
    assert result.output_path == Path(pending.output_path or "")
    assert mark_pending.call_count == 1
    client.upload_memory.assert_called_once()
    suggest.assert_not_called()
    saved = runner.db.get_run(pending.run_id)
    assert saved is not None
    assert saved.delivery_attempts == 0


def test_retry_accepts_whitespace_asset_id_after_committed_delivery_write(
    tmp_path: Path,
) -> None:
    """The durable probe uses the normalized asset identity returned by Immich."""
    runner = AutoRunner(_config(tmp_path))
    pending = _save_pending_auto_run(runner, tmp_path)
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    client.upload_memory.return_value = {"asset_id": "  asset-whitespace  "}
    mark_delivered = runner.db.mark_delivered
    calls = 0

    def commit_then_raise(run_id: str, asset_id: str) -> RunMetadata:
        nonlocal calls
        calls += 1
        mark_delivered(run_id, asset_id)
        raise OSError("connection closed after durable write")

    with (
        patch("immich_memories.api.immich.SyncImmichClient", return_value=client),
        patch.object(runner.db, "mark_delivered", side_effect=commit_then_raise),
        patch.object(runner, "suggest") as suggest,
    ):
        result = runner.run_one(force=True)

    assert result.outcome is AutoOutcome.COMPLETED
    assert result.action is AutoAction.DELIVERY_RETRY
    assert result.run_id == pending.run_id
    assert calls == 1
    client.upload_memory.assert_called_once()
    suggest.assert_not_called()
    saved = runner.db.get_run(pending.run_id)
    assert saved is not None
    assert saved.delivery_status is DeliveryStatus.DELIVERED
    assert saved.immich_asset_id == "asset-whitespace"


def test_retry_attempt_finish_is_retried_without_reuploading(tmp_path: Path) -> None:
    """Retry-terminal attempt persistence stays in the retry path after a transient error."""
    runner = AutoRunner(_config(tmp_path))
    pending = _save_pending_auto_run(runner, tmp_path)
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    client.upload_memory.return_value = {"asset_id": "asset-finished"}
    finish_attempt = runner.state.finish_attempt
    calls = 0

    def fail_once(*args: object, **kwargs: object):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("attempt durable write failed")
        return finish_attempt(*args, **kwargs)

    with (
        patch("immich_memories.api.immich.SyncImmichClient", return_value=client),
        patch.object(runner.state, "finish_attempt", side_effect=fail_once),
        patch.object(runner, "suggest") as suggest,
    ):
        result = runner.run_one(force=True)

    assert result.outcome is AutoOutcome.COMPLETED
    assert result.action is AutoAction.DELIVERY_RETRY
    assert result.run_id == pending.run_id
    assert calls == 2
    client.upload_memory.assert_called_once()
    suggest.assert_not_called()
    assert runner.state.get_last_attempt().outcome is AutoOutcome.COMPLETED


def test_pending_retry_preflight_failure_stops_before_upload_or_suggest(
    tmp_path: Path,
) -> None:
    """The standalone preflight gates retries without re-entering discovery."""
    from immich_memories.preflight import CheckResult, CheckStatus

    runner = AutoRunner(_config(tmp_path))
    pending = _save_pending_auto_run(runner, tmp_path)
    failed = CheckResult(
        name="Immich",
        status=CheckStatus.ERROR,
        message="Authentication failed",
        details="retry-api-key rejected",
    )

    with (
        patch("immich_memories.preflight.check_immich", return_value=failed) as preflight,
        patch("immich_memories.api.immich.SyncImmichClient") as client_factory,
        patch.object(runner, "suggest") as suggest,
    ):
        result = runner.run_one(force=True)

    assert result.outcome is AutoOutcome.FAILED
    assert result.action is AutoAction.DELIVERY_RETRY
    assert result.reason == "Immich preflight failed"
    assert result.run_id == pending.run_id
    assert result.output_path == Path(pending.output_path or "")
    assert result.error is not None
    assert "retry-api-key" not in result.error
    preflight.assert_called_once_with(runner.config)
    client_factory.assert_not_called()
    suggest.assert_not_called()
    attempt = runner.state.get_last_attempt()
    assert attempt is not None
    assert attempt.outcome is AutoOutcome.FAILED
    assert attempt.run_id == pending.run_id


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
