"""Artifact completion and Immich delivery-state contracts."""

from __future__ import annotations

import json
import logging
import sqlite3
import subprocess
import traceback
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from immich_memories.cache.database import VideoAnalysisCache
from immich_memories.processing.output_contract import OutputProbe
from immich_memories.tracking import DeliveryStatus, RunDatabase, RunMetadata, RunTracker
from tests.conftest import make_clip


def test_run_metadata_delivery_state_round_trips_through_json() -> None:
    """Sidecars retain delivery state, attempts, errors, asset identity, and warnings."""
    run = RunMetadata(
        run_id="delivery-round-trip",
        created_at=datetime(2026, 8, 11, 10, 0, tzinfo=UTC),
        status="completed",
        delivery_status=DeliveryStatus.PENDING,
        delivery_attempts=2,
        delivery_error="Immich timed out",
        immich_asset_id="asset-123",
        delivery_album="Family Memories",
        warnings=["Optional music failed: backend unavailable"],
    )

    loaded = RunMetadata.from_json(run.to_json())

    assert loaded.delivery_status is DeliveryStatus.PENDING
    assert loaded.delivery_attempts == 2
    assert loaded.delivery_error == "Immich timed out"
    assert loaded.immich_asset_id == "asset-123"
    assert loaded.delivery_album == "Family Memories"
    assert loaded.warnings == ["Optional music failed: backend unavailable"]


def test_legacy_run_metadata_treats_null_delivery_fields_as_defaults() -> None:
    """Old or hand-edited sidecars cannot turn nullable JSON into invalid lifecycle state."""
    loaded = RunMetadata.from_dict(
        {
            "run_id": "legacy-sidecar",
            "created_at": "2026-08-11T10:00:00+00:00",
            "delivery_status": None,
            "delivery_attempts": None,
            "warnings": None,
        }
    )

    assert loaded.delivery_status is DeliveryStatus.NOT_REQUESTED
    assert loaded.delivery_attempts == 0
    assert loaded.delivery_error is None
    assert loaded.immich_asset_id is None
    assert loaded.delivery_album is None
    assert loaded.warnings == []


def test_fresh_database_has_delivery_state_defaults(tmp_path: Path) -> None:
    """Fresh runs begin not requested and retain empty delivery diagnostics."""
    db_path = tmp_path / "fresh-v13.db"
    VideoAnalysisCache(db_path)

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO pipeline_runs (run_id, created_at, status)
            VALUES ('fresh-run', '2026-08-11T10:00:00+00:00', 'running')
            """
        )
        row = conn.execute(
            """
            SELECT delivery_status, delivery_attempts, delivery_error,
                   immich_asset_id, delivery_album, warnings_json
            FROM pipeline_runs WHERE run_id = 'fresh-run'
            """
        ).fetchone()
        schema_version = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]

    assert row == ("not_requested", 0, None, None, None, "[]")
    assert schema_version == 16


def test_database_round_trip_preserves_delivery_and_automation_identity(tmp_path: Path) -> None:
    """Saving delivery fields never erases the exact v12 automation attempt identity."""
    database = RunDatabase(tmp_path / "round-trip.db")
    run = RunMetadata(
        run_id="saved-delivery",
        created_at=datetime(2026, 8, 11, 10, 0, tzinfo=UTC),
        completed_at=datetime(2026, 8, 11, 10, 5, tzinfo=UTC),
        status="completed",
        source="auto",
        automation_attempt_id="attempt-v12-preserved",
        output_path=str(tmp_path / "memory.mp4"),
        delivery_status=DeliveryStatus.PENDING,
        delivery_attempts=3,
        delivery_error="Immich unavailable",
        immich_asset_id=None,
        delivery_album="Launch Album",
        warnings=["Optional music failed: timeout"],
    )

    database.save_run(run)
    loaded = database.get_run(run.run_id)

    assert loaded is not None
    assert loaded.delivery_status is DeliveryStatus.PENDING
    assert loaded.delivery_attempts == 3
    assert loaded.delivery_error == "Immich unavailable"
    assert loaded.immich_asset_id is None
    assert loaded.delivery_album == "Launch Album"
    assert loaded.warnings == ["Optional music failed: timeout"]
    assert loaded.automation_attempt_id == "attempt-v12-preserved"


def test_resaving_stale_run_cannot_replace_authoritative_state_or_delete_phases(
    tmp_path: Path,
) -> None:
    """A duplicate save cannot cascade-delete children or erase completed delivery facts."""
    from immich_memories.tracking import DuplicateRunError, PhaseStats

    database = RunDatabase(tmp_path / "non-destructive-save.db")
    created_at = datetime(2026, 8, 11, 10, 0, tzinfo=UTC)
    database.save_run(
        RunMetadata(
            run_id="authoritative-run",
            created_at=created_at,
            status="running",
            source="auto",
            automation_attempt_id="attempt-authoritative",
        )
    )
    database.save_phase_stats(
        "authoritative-run",
        PhaseStats(
            phase_name="assembly",
            started_at=created_at,
            completed_at=created_at + timedelta(minutes=1),
            duration_seconds=60.0,
            items_processed=4,
            items_total=4,
        ),
    )
    output_path = tmp_path / "authoritative.mp4"
    output_path.write_bytes(b"validated")
    database.complete_artifact(
        "authoritative-run",
        completed_at=created_at + timedelta(minutes=2),
        output_path=str(output_path),
        output_size_bytes=4096,
        output_duration_seconds=42.5,
        delivery_requested=True,
        delivery_album="Original Album",
        warnings=["music fallback"],
        clips_analyzed=8,
        clips_selected=4,
        errors_count=1,
    )
    before = database.mark_delivered("authoritative-run", "asset-authoritative")

    with pytest.raises(DuplicateRunError, match="already exists"):
        database.save_run(
            RunMetadata(
                run_id="authoritative-run",
                created_at=created_at + timedelta(hours=1),
                status="running",
                source="manual",
                automation_attempt_id=None,
            )
        )
    after = database.get_run("authoritative-run")

    assert after is not None
    assert after.to_dict() == before.to_dict()
    assert len(after.phases) == 1
    assert after.phases[0].phase_name == "assembly"


@pytest.mark.parametrize(
    "operation",
    [
        "start_phase",
        "complete_phase",
        "update_phase_progress",
        "complete_run",
        "complete_artifact",
        "mark_delivery_pending",
        "mark_delivered",
        "fail_run",
        "cancel_run",
    ],
)
def test_duplicate_tracker_cannot_claim_or_mutate_existing_run(
    tmp_path: Path,
    operation: str,
) -> None:
    """A rejected tracker never owns enough state to overwrite the original run."""
    from immich_memories.tracking import DuplicateRunError

    db_path = tmp_path / "duplicate-tracker.db"
    original_output = tmp_path / "original.mp4"
    original_output.write_bytes(b"original")
    original = RunTracker("shared-run-id", db_path=db_path, capture_system=False)
    original.start_run(source="auto", automation_attempt_id="original-attempt")
    original.start_phase("assembly", total_items=1)
    original.complete_phase(items_processed=1)
    original.complete_artifact(
        original_output,
        _authoritative_probe(),
        warnings=["original warning"],
        delivery_requested=True,
        delivery_album="Original Album",
    )
    before = original.mark_delivered("original-asset")

    duplicate_output = tmp_path / "duplicate.mp4"
    duplicate_output.write_bytes(b"duplicate")
    duplicate = RunTracker("shared-run-id", db_path=db_path, capture_system=False)
    with pytest.raises(DuplicateRunError, match="already exists"):
        duplicate.start_run(source="manual", automation_attempt_id="replacement-attempt")

    assert duplicate.current_run is None
    with pytest.raises(RuntimeError, match="not started"):
        if operation == "start_phase":
            duplicate.start_phase("intruder")
        elif operation == "complete_phase":
            duplicate.complete_phase()
        elif operation == "update_phase_progress":
            duplicate.update_phase_progress(1)
        elif operation == "complete_run":
            duplicate.complete_run(duplicate_output)
        elif operation == "complete_artifact":
            duplicate.complete_artifact(
                duplicate_output,
                _authoritative_probe(),
                warnings=[],
                delivery_requested=True,
                delivery_album="Replacement Album",
            )
        elif operation == "mark_delivery_pending":
            duplicate.mark_delivery_pending("intruder error")
        elif operation == "mark_delivered":
            duplicate.mark_delivered("intruder-asset")
        elif operation == "fail_run":
            duplicate.fail_run("intruder error")
        else:
            duplicate.cancel_run()

    after = RunDatabase(db_path).get_run("shared-run-id")
    assert after is not None
    assert after.to_dict() == before.to_dict()
    assert [phase.phase_name for phase in after.phases] == ["assembly"]


def test_populated_v12_migrates_additively_without_changing_attempt_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The v13 migration preserves populated v12 rows and their parent attempt IDs."""
    from immich_memories.cache import database as cache_database

    db_path = tmp_path / "populated-v12.db"
    monkeypatch.setattr(cache_database, "SCHEMA_VERSION", 12)
    VideoAnalysisCache(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO pipeline_runs (
                run_id, created_at, completed_at, status, source,
                automation_attempt_id, output_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "existing-v12",
                "2026-08-11T09:00:00+00:00",
                "2026-08-11T09:05:00+00:00",
                "completed",
                "auto",
                "attempt-existing-v12",
                str(tmp_path / "memory.mp4"),
            ),
        )
        conn.commit()

    monkeypatch.setattr(cache_database, "SCHEMA_VERSION", 13)
    migrated = RunDatabase(db_path).get_run("existing-v12")

    assert migrated is not None
    assert migrated.status == "completed"
    assert migrated.automation_attempt_id == "attempt-existing-v12"
    assert migrated.delivery_status is DeliveryStatus.NOT_REQUESTED
    assert migrated.delivery_attempts == 0
    assert migrated.delivery_error is None
    assert migrated.immich_asset_id is None
    assert migrated.delivery_album is None
    assert migrated.warnings == []


def test_v13_delivery_migration_keeps_v12_analysis_cache_current(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A delivery-only schema migration cannot force expensive video reanalysis."""
    from immich_memories.cache import database as cache_database
    from tests.conftest import make_asset

    db_path = tmp_path / "populated-analysis-v12.db"
    asset = make_asset("analysis-v12")
    monkeypatch.setattr(cache_database, "SCHEMA_VERSION", 12)
    v12_cache = VideoAnalysisCache(db_path)
    v12_cache.save_analysis(asset, segments=[])

    monkeypatch.setattr(cache_database, "SCHEMA_VERSION", 13)
    migrated_cache = VideoAnalysisCache(db_path)

    with sqlite3.connect(db_path) as conn:
        schema_version = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        analysis_version = conn.execute(
            "SELECT analysis_version FROM video_analysis WHERE asset_id = ?",
            (asset.id,),
        ).fetchone()[0]
    assert schema_version == 13
    assert cache_database.ANALYSIS_VERSION == 12
    assert analysis_version == 12
    assert migrated_cache.needs_reanalysis(asset, max_age_days=365) is False


def test_database_marks_delivery_pending_atomically_without_changing_artifact(
    tmp_path: Path,
) -> None:
    """One failed API call increments once and leaves the completed artifact intact."""
    database = RunDatabase(tmp_path / "pending.db")
    output_path = tmp_path / "memory.mp4"
    output_path.write_bytes(b"validated-memory")
    completed_at = datetime(2026, 8, 11, 10, 5, tzinfo=UTC)
    database.save_run(
        RunMetadata(
            run_id="pending-run",
            created_at=datetime(2026, 8, 11, 10, 0, tzinfo=UTC),
            completed_at=completed_at,
            status="completed",
            output_path=str(output_path),
            output_size_bytes=16,
            delivery_status=DeliveryStatus.DELIVERED,
            delivery_attempts=2,
            immich_asset_id="stale-asset",
            delivery_album="Original Album",
            warnings=["music fallback"],
            automation_attempt_id="attempt-preserved",
        )
    )

    updated = database.mark_delivery_pending("pending-run", "Immich timed out")

    assert updated.delivery_status is DeliveryStatus.PENDING
    assert updated.delivery_attempts == 3
    assert updated.delivery_error == "Immich timed out"
    assert updated.immich_asset_id is None
    assert updated.delivery_album == "Original Album"
    assert updated.status == "completed"
    assert updated.completed_at == completed_at
    assert updated.output_path == str(output_path)
    assert updated.warnings == ["music fallback"]
    assert updated.automation_attempt_id == "attempt-preserved"


def test_database_marks_delivery_success_and_clears_previous_error(tmp_path: Path) -> None:
    """One successful API call increments once, stores its asset ID, and clears failure text."""
    database = RunDatabase(tmp_path / "delivered.db")
    database.save_run(
        RunMetadata(
            run_id="delivered-run",
            created_at=datetime(2026, 8, 11, 10, 0, tzinfo=UTC),
            completed_at=datetime(2026, 8, 11, 10, 5, tzinfo=UTC),
            status="completed",
            delivery_status=DeliveryStatus.PENDING,
            delivery_attempts=1,
            delivery_error="previous timeout",
            delivery_album="Original Album",
        )
    )

    updated = database.mark_delivered("delivered-run", "asset-new")

    assert updated.delivery_status is DeliveryStatus.DELIVERED
    assert updated.delivery_attempts == 2
    assert updated.delivery_error is None
    assert updated.immich_asset_id == "asset-new"
    assert updated.delivery_album == "Original Album"
    assert updated.status == "completed"


def test_database_rejects_delivery_for_completed_run_without_requested_delivery(
    tmp_path: Path,
) -> None:
    """A completed no-upload artifact cannot be retroactively marked delivered."""
    from immich_memories.tracking import InvalidRunLifecycleError

    database = RunDatabase(tmp_path / "not-requested.db")
    database.save_run(
        RunMetadata(
            run_id="not-requested-run",
            created_at=datetime(2026, 8, 11, 10, 0, tzinfo=UTC),
            completed_at=datetime(2026, 8, 11, 10, 5, tzinfo=UTC),
            status="completed",
            delivery_status=DeliveryStatus.NOT_REQUESTED,
        )
    )
    before = database.get_run("not-requested-run")
    assert before is not None

    with pytest.raises(InvalidRunLifecycleError, match="requested delivery"):
        database.mark_delivered("not-requested-run", "asset-must-not-persist")

    after = database.get_run("not-requested-run")
    assert after is not None
    assert after.to_dict() == before.to_dict()


def test_database_rejects_repeated_artifact_completion_after_delivery(tmp_path: Path) -> None:
    """Artifact facts become immutable once the delivery transition is committed."""
    from immich_memories.tracking import InvalidRunLifecycleError

    database = RunDatabase(tmp_path / "completed-twice.db")
    output_path = tmp_path / "memory.mp4"
    output_path.write_bytes(b"validated")
    created_at = datetime(2026, 8, 11, 10, 0, tzinfo=UTC)
    database.save_run(RunMetadata(run_id="delivered-run", created_at=created_at, status="running"))
    database.complete_artifact(
        "delivered-run",
        completed_at=created_at + timedelta(minutes=1),
        output_path=str(output_path),
        output_size_bytes=4096,
        output_duration_seconds=42.5,
        delivery_requested=True,
        delivery_album="Original Album",
        warnings=[],
        clips_analyzed=4,
        clips_selected=2,
        errors_count=0,
    )
    before = database.mark_delivered("delivered-run", "asset-authoritative")

    with pytest.raises(InvalidRunLifecycleError, match="running run"):
        database.complete_artifact(
            "delivered-run",
            completed_at=created_at + timedelta(minutes=2),
            output_path=str(output_path),
            output_size_bytes=1,
            output_duration_seconds=1.0,
            delivery_requested=False,
            delivery_album=None,
            warnings=["stale"],
            clips_analyzed=0,
            clips_selected=0,
            errors_count=1,
        )

    after = database.get_run("delivered-run")
    assert after is not None
    assert after.to_dict() == before.to_dict()


@pytest.mark.parametrize("asset_id", ["", "   "])
def test_database_rejects_empty_delivered_asset_identity(
    tmp_path: Path,
    asset_id: str,
) -> None:
    """A response without a usable Immich asset ID is not a successful delivery call."""
    database = RunDatabase(tmp_path / "invalid-asset.db")
    database.save_run(
        RunMetadata(
            run_id="invalid-asset-run",
            created_at=datetime(2026, 8, 11, 10, 0, tzinfo=UTC),
            status="completed",
            delivery_status=DeliveryStatus.PENDING,
        )
    )

    with pytest.raises(ValueError, match="asset ID"):
        database.mark_delivered("invalid-asset-run", asset_id)

    unchanged = database.get_run("invalid-asset-run")
    assert unchanged is not None
    assert unchanged.delivery_status is DeliveryStatus.PENDING
    assert unchanged.delivery_attempts == 0
    assert unchanged.immich_asset_id is None


@pytest.mark.parametrize("status", ["running", "failed", "interrupted", "cancelled"])
@pytest.mark.parametrize("transition", ["pending", "delivered"])
def test_delivery_transitions_reject_noncompleted_runs_without_mutation(
    tmp_path: Path,
    status: str,
    transition: str,
) -> None:
    """Only a completed artifact may enter either delivery transition."""
    from immich_memories.tracking import InvalidRunLifecycleError

    database = RunDatabase(tmp_path / f"{status}-{transition}.db")
    database.save_run(
        RunMetadata(
            run_id="noncompleted-run",
            created_at=datetime(2026, 8, 11, 10, 0, tzinfo=UTC),
            status=status,  # type: ignore[arg-type]
            source="auto",
            automation_attempt_id="attempt-must-stay",
            output_path=str(tmp_path / "partial.mp4"),
            output_size_bytes=123,
            delivery_status=DeliveryStatus.NOT_REQUESTED,
            delivery_album="Album Must Stay",
            warnings=["warning must stay"],
        )
    )
    before = database.get_run("noncompleted-run")
    assert before is not None

    with pytest.raises(InvalidRunLifecycleError, match="requires a completed run"):
        if transition == "pending":
            database.mark_delivery_pending("noncompleted-run", "must not persist")
        else:
            database.mark_delivered("noncompleted-run", "asset-must-not-persist")

    after = database.get_run("noncompleted-run")
    assert after is not None
    assert after.to_dict() == before.to_dict()


@pytest.mark.parametrize("transition", ["pending", "delivered"])
def test_delivery_transitions_distinguish_missing_runs(
    tmp_path: Path,
    transition: str,
) -> None:
    """A missing identity remains distinct from an invalid lifecycle state."""
    database = RunDatabase(tmp_path / f"missing-{transition}.db")

    with pytest.raises(KeyError, match="Unknown pipeline run"):
        if transition == "pending":
            database.mark_delivery_pending("absent-run", "not found")
        else:
            database.mark_delivered("absent-run", "asset-not-found")


def _save_delivery_candidate(
    database: RunDatabase,
    tmp_path: Path,
    *,
    run_id: str,
    created_at: datetime,
    completed_at: datetime | None,
    status: str = "completed",
    delivery_status: DeliveryStatus = DeliveryStatus.PENDING,
    source: str = "auto",
    with_file: bool = True,
) -> RunMetadata:
    output_path = tmp_path / f"{run_id}.mp4" if with_file else None
    if output_path is not None:
        output_path.write_bytes(run_id.encode())
    run = RunMetadata(
        run_id=run_id,
        created_at=created_at,
        completed_at=completed_at,
        status=status,  # type: ignore[arg-type]
        source=source,
        output_path=str(output_path) if output_path is not None else None,
        delivery_status=delivery_status,
    )
    database.save_run(run)
    return run


def test_oldest_pending_delivery_excludes_ineligible_runs(tmp_path: Path) -> None:
    """Retry selection requires completed, pending, source-matched runs with output paths."""
    database = RunDatabase(tmp_path / "oldest.db")
    base = datetime(2026, 8, 11, 10, 0, tzinfo=UTC)
    _save_delivery_candidate(
        database,
        tmp_path,
        run_id="running-old",
        created_at=base,
        completed_at=None,
        status="running",
    )
    _save_delivery_candidate(
        database,
        tmp_path,
        run_id="delivered-old",
        created_at=base,
        completed_at=base + timedelta(minutes=1),
        delivery_status=DeliveryStatus.DELIVERED,
    )
    _save_delivery_candidate(
        database,
        tmp_path,
        run_id="manual-old",
        created_at=base,
        completed_at=base + timedelta(minutes=2),
        source="manual",
    )
    _save_delivery_candidate(
        database,
        tmp_path,
        run_id="pathless-old",
        created_at=base,
        completed_at=base + timedelta(minutes=3),
        with_file=False,
    )
    expected = _save_delivery_candidate(
        database,
        tmp_path,
        run_id="pending-first",
        created_at=base,
        completed_at=base + timedelta(minutes=4),
    )
    _save_delivery_candidate(
        database,
        tmp_path,
        run_id="pending-later",
        created_at=base,
        completed_at=base + timedelta(minutes=5),
    )

    selected = database.get_oldest_pending_delivery(source="auto")

    assert selected is not None
    assert selected.run_id == expected.run_id


def test_oldest_pending_delivery_warns_and_continues_past_missing_files(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A missing oldest artifact is actionable but cannot hide the next retryable run."""
    database = RunDatabase(tmp_path / "missing.db")
    base = datetime(2026, 8, 11, 10, 0, tzinfo=UTC)
    database.save_run(
        RunMetadata(
            run_id="missing-oldest",
            created_at=base,
            completed_at=base + timedelta(minutes=1),
            status="completed",
            source="auto",
            output_path=str(tmp_path / "missing-memory.mp4"),
            delivery_status=DeliveryStatus.PENDING,
        )
    )
    expected = _save_delivery_candidate(
        database,
        tmp_path,
        run_id="existing-next",
        created_at=base,
        completed_at=base + timedelta(minutes=2),
    )
    caplog.set_level("WARNING")

    selected = database.get_oldest_pending_delivery(source="auto")

    assert selected is not None
    assert selected.run_id == expected.run_id
    assert "missing-oldest" in caplog.text
    assert "cannot be retried because its output file is missing" in caplog.text


def test_oldest_pending_delivery_breaks_timestamp_ties_deterministically(
    tmp_path: Path,
) -> None:
    """Equal completion and creation timestamps use run ID as the final stable key."""
    database = RunDatabase(tmp_path / "ties.db")
    created = datetime(2026, 8, 11, 10, 0, tzinfo=UTC)
    completed = created + timedelta(minutes=5)
    _save_delivery_candidate(
        database,
        tmp_path,
        run_id="z-run",
        created_at=created,
        completed_at=completed,
    )
    expected = _save_delivery_candidate(
        database,
        tmp_path,
        run_id="a-run",
        created_at=created,
        completed_at=completed,
    )
    _save_delivery_candidate(
        database,
        tmp_path,
        run_id="created-later",
        created_at=created + timedelta(seconds=1),
        completed_at=completed,
    )

    selected = database.get_oldest_pending_delivery(source="auto")

    assert selected is not None
    assert selected.run_id == expected.run_id


def _authoritative_probe() -> OutputProbe:
    return OutputProbe(
        codec="h264",
        container="mp4",
        duration_seconds=42.5,
        size_bytes=4096,
        pixel_format="yuv420p",
        color_transfer="bt709",
        color_primaries="bt709",
        width=1920,
        height=1080,
        decoded_frames=1020,
    )


def test_tracker_completes_artifact_from_authoritative_probe_without_reprobing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Artifact completion trusts validated probe facts and refreshes the JSON sidecar."""
    output_path = tmp_path / "memory.mp4"
    output_path.write_bytes(b"small-test-file")
    tracker = RunTracker(
        "artifact-run",
        db_path=tmp_path / "tracker.db",
        capture_system=False,
    )
    tracker.start_run(
        source="auto",
        automation_attempt_id="attempt-artifact",
    )
    monkeypatch.setattr(
        tracker,
        "_get_video_duration",
        lambda _path: (_ for _ in ()).throw(AssertionError("legacy probe called")),
    )

    completed = tracker.complete_artifact(
        output_path,
        _authoritative_probe(),
        warnings=["Optional music failed: timeout"],
        delivery_album="Family Album",
        clips_analyzed=12,
        clips_selected=7,
        errors_count=1,
    )

    assert completed.status == "completed"
    assert completed.output_path == str(output_path)
    assert completed.output_size_bytes == 4096
    assert completed.output_duration_seconds == 42.5
    assert completed.delivery_status is DeliveryStatus.NOT_REQUESTED
    assert completed.delivery_attempts == 0
    assert completed.delivery_album == "Family Album"
    assert completed.warnings == ["Optional music failed: timeout"]
    assert completed.clips_analyzed == 12
    assert completed.clips_selected == 7
    assert completed.errors_count == 1
    assert completed.automation_attempt_id == "attempt-artifact"
    sidecar = RunMetadata.from_json((tmp_path / "run_metadata.json").read_text())
    assert sidecar.to_dict() == completed.to_dict()


def test_tracker_completes_requested_artifact_as_retryable_before_upload(
    tmp_path: Path,
) -> None:
    """Requested delivery is pending with zero attempts in the artifact commit itself."""
    output_path = tmp_path / "memory.mp4"
    output_path.write_bytes(b"validated")
    tracker = RunTracker(
        "requested-artifact", db_path=tmp_path / "requested.db", capture_system=False
    )
    tracker.start_run(automation_attempt_id="attempt-requested")

    completed = tracker.complete_artifact(
        output_path,
        _authoritative_probe(),
        warnings=["music fallback"],
        delivery_requested=True,
        delivery_album="Album At Request Time",
    )

    assert completed.status == "completed"
    assert completed.delivery_status is DeliveryStatus.PENDING
    assert completed.delivery_attempts == 0
    assert completed.delivery_error is None
    assert completed.immich_asset_id is None
    assert completed.delivery_album == "Album At Request Time"
    assert completed.automation_attempt_id == "attempt-requested"


def test_legacy_completion_keeps_database_authoritative_when_sidecar_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The complete_run compatibility path also treats JSON as a diagnostic mirror."""
    configured_literal = "legacy-sidecar-detail-must-not-be-logged"
    output_path = tmp_path / "legacy.mp4"
    output_path.write_bytes(b"legacy-output")
    tracker = RunTracker("legacy-completion", db_path=tmp_path / "legacy.db", capture_system=False)
    tracker.start_run(automation_attempt_id="attempt-legacy")
    monkeypatch.setattr(tracker, "_get_video_duration", lambda _path: 3.5)
    monkeypatch.setattr(
        tracker,
        "_save_metadata_json",
        lambda *_args: (_ for _ in ()).throw(OSError(configured_literal)),
    )
    caplog.set_level(logging.WARNING)

    completed = tracker.complete_run(output_path)
    saved = RunDatabase(tmp_path / "legacy.db").get_run("legacy-completion")

    assert completed.status == "completed"
    assert saved is not None
    assert saved.status == "completed"
    assert saved.output_path == str(output_path)
    assert saved.output_size_bytes == len(b"legacy-output")
    assert saved.output_duration_seconds == 3.5
    assert configured_literal not in caplog.text


def test_sidecar_file_write_failure_logs_only_controlled_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The real JSON writer cannot leak filesystem exception details or break completion."""
    configured_literal = "filesystem-detail-must-not-be-logged"
    output_path = tmp_path / "write-failure.mp4"
    output_path.write_bytes(b"validated")
    tracker = RunTracker(
        "write-failure", db_path=tmp_path / "write-failure.db", capture_system=False
    )
    tracker.start_run()
    monkeypatch.setattr(
        Path,
        "write_text",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError(configured_literal)),
    )
    caplog.set_level(logging.WARNING)

    completed = tracker.complete_artifact(output_path, _authoritative_probe(), warnings=[])

    assert completed.status == "completed"
    assert configured_literal not in caplog.text
    assert "Failed to refresh run metadata sidecar" in caplog.text


def test_tracker_marks_delivery_pending_and_refreshes_sidecar(tmp_path: Path) -> None:
    """A failed API call stays completed and persists its retry state to the sidecar."""
    output_path = tmp_path / "memory.mp4"
    output_path.write_bytes(b"validated")
    tracker = RunTracker("tracker-pending", db_path=tmp_path / "pending.db", capture_system=False)
    tracker.start_run(automation_attempt_id="attempt-pending")
    tracker.complete_artifact(
        output_path,
        _authoritative_probe(),
        warnings=["music fallback"],
        delivery_album="Original Album",
    )

    pending = tracker.mark_delivery_pending("Immich timed out")

    assert pending.status == "completed"
    assert pending.delivery_status is DeliveryStatus.PENDING
    assert pending.delivery_attempts == 1
    assert pending.delivery_error == "Immich timed out"
    assert pending.immich_asset_id is None
    assert pending.delivery_album == "Original Album"
    assert pending.automation_attempt_id == "attempt-pending"
    sidecar = RunMetadata.from_json((tmp_path / "run_metadata.json").read_text())
    assert sidecar.to_dict() == pending.to_dict()
    assert tracker.current_run == pending


def test_tracker_marks_delivery_success_and_refreshes_sidecar(tmp_path: Path) -> None:
    """A successful retry records its asset and keeps the original album for provenance."""
    output_path = tmp_path / "memory.mp4"
    output_path.write_bytes(b"validated")
    tracker = RunTracker(
        "tracker-delivered", db_path=tmp_path / "delivered.db", capture_system=False
    )
    tracker.start_run()
    tracker.complete_artifact(
        output_path,
        _authoritative_probe(),
        warnings=[],
        delivery_album="Original Album",
    )
    tracker.mark_delivery_pending("first call failed")

    delivered = tracker.mark_delivered("asset-retry")

    assert delivered.status == "completed"
    assert delivered.delivery_status is DeliveryStatus.DELIVERED
    assert delivered.delivery_attempts == 2
    assert delivered.delivery_error is None
    assert delivered.immich_asset_id == "asset-retry"
    assert delivered.delivery_album == "Original Album"
    sidecar = RunMetadata.from_json((tmp_path / "run_metadata.json").read_text())
    assert sidecar.to_dict() == delivered.to_dict()
    assert tracker.current_run == delivered


def test_tracker_marks_pending_configuration_error_without_counting_api_call(
    tmp_path: Path,
) -> None:
    """Requested delivery without a client is pending, but no API attempt occurred."""
    output_path = tmp_path / "memory.mp4"
    output_path.write_bytes(b"validated")
    tracker = RunTracker("tracker-config", db_path=tmp_path / "config.db", capture_system=False)
    tracker.start_run()
    tracker.complete_artifact(output_path, _authoritative_probe(), warnings=[])

    pending = tracker.mark_delivery_pending(
        "Immich upload requested but no client is configured",
        attempted=False,
    )

    assert pending.delivery_status is DeliveryStatus.PENDING
    assert pending.delivery_attempts == 0
    assert pending.delivery_error == "Immich upload requested but no client is configured"


def _h264_plan():
    from immich_memories.processing.encoding_plan import EncodingPlan, HdrTransfer, OutputCodec

    return EncodingPlan(
        codec=OutputCodec.H264,
        encoder="libx264",
        encoder_args=("-c:v", "libx264"),
        target_transfer=HdrTransfer.NONE,
        tone_map_to_sdr=False,
        pixel_format="yuv420p",
        container="mp4",
    )


def _probe_payload() -> dict[str, object]:
    return {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "pix_fmt": "yuv420p",
                "color_transfer": "bt709",
                "color_primaries": "bt709",
                "width": 1920,
                "height": 1080,
                "nb_read_frames": "1020",
            }
        ],
        "format": {
            "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
            "duration": "42.5",
            "size": "4096",
            "tags": {"major_brand": "isom"},
        },
    }


def _prepare_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    upload_enabled: bool,
    client: object | None,
    upload_album: str | None = None,
    music_warning: str | None = None,
    no_music: bool = True,
    upload_result: dict[str, str] | Exception | None = None,
    configured_secret: str | None = None,
) -> tuple[object, list[str]]:
    from immich_memories import generate as generate_module
    from immich_memories.generate import GenerationParams
    from immich_memories.generate_music import MusicPhaseResult
    from immich_memories.processing import output_contract
    from immich_memories.processing.assembly_config import AssemblyClip, AssemblySettings

    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    assembly_clip = AssemblyClip(path=source, duration=5.0, asset_id="clip-1")
    from immich_memories.config_loader import Config

    config = Config(
        cache={"directory": str(tmp_path / "cache"), "database": str(tmp_path / "runs.db")},
        immich={"api_key": configured_secret or ""},
    )
    params = GenerationParams(
        clips=[make_clip("clip-1")],
        output_path=tmp_path / "memory.mp4",
        config=config,
        client=client,
        no_music=no_music,
        upload_enabled=upload_enabled,
        upload_album=upload_album,
        debug_preserve_intermediates=True,
        source="auto",
        automation_attempt_id="attempt-generation",
    )
    plan = _h264_plan()
    events: list[str] = []

    class Assembler:
        def assemble_with_titles(
            self,
            _clips: object,
            output_path: Path,
            _callback: object,
            **_kwargs: object,
        ) -> Path:
            output_path.write_bytes(b"validated-artifact")
            return output_path

    original_run = subprocess.run

    def run_probe(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[0] == "ffprobe":
            return subprocess.CompletedProcess(command, 0, json.dumps(_probe_payload()), "")
        return original_run(command, **kwargs)  # type: ignore[call-overload]

    def music_phase(*_args: object, **_kwargs: object) -> MusicPhaseResult:
        events.append("music")
        return MusicPhaseResult(applied=False, warning=music_warning)

    def final_validate(path: Path, encoding_plan: object) -> OutputProbe:
        assert path.name == "memory.mp4"
        assert encoding_plan is plan
        events.append("final-probe")
        return _authoritative_probe()

    def upload(*_args: object, **_kwargs: object) -> dict[str, str]:
        artifact = RunDatabase(tmp_path / "runs.db").get_run("delivery-run")
        assert artifact is not None
        assert artifact.status == "completed"
        assert artifact.delivery_status is DeliveryStatus.PENDING
        assert artifact.delivery_attempts == 0
        assert artifact.delivery_album == upload_album
        events.append("upload")
        if isinstance(upload_result, Exception):
            raise upload_result
        return upload_result or {"asset_id": "asset-success"}

    monkeypatch.setattr("immich_memories.tracking.generate_run_id", lambda: "delivery-run")
    monkeypatch.setattr(
        "immich_memories.cache.video_cache.VideoDownloadCache",
        lambda **_kwargs: MagicMock(),
    )
    monkeypatch.setattr(generate_module, "_extract_clips", lambda *_args: [assembly_clip])
    monkeypatch.setattr(
        generate_module,
        "_build_assembly_settings",
        lambda *_args: AssemblySettings(encoding_plan=plan),
    )
    monkeypatch.setattr(generate_module, "_create_assembler", lambda *_args: Assembler())
    monkeypatch.setattr(generate_module, "_run_music_phase", music_phase)
    monkeypatch.setattr(generate_module, "_upload_to_immich", upload)
    monkeypatch.setattr(generate_module, "_cleanup_temp_clips", lambda _clips: None)
    monkeypatch.setattr(output_contract.subprocess, "run", run_probe)
    monkeypatch.setattr(generate_module, "validate_output", final_validate, raising=False)
    monkeypatch.setattr(
        RunTracker,
        "_get_video_duration",
        lambda _self, _path: (_ for _ in ()).throw(AssertionError("legacy probe called")),
    )
    return params, events


def test_deferred_generation_returns_exact_context_on_the_caller_owned_tracker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UI post-processing receives the assembly contract without completing a shadow run."""
    from immich_memories import generate as generate_module
    from immich_memories.generate import PreparedGeneration, generate_memory
    from immich_memories.processing.assembly_config import AssemblySettings

    params, events = _prepare_generation(
        tmp_path,
        monkeypatch,
        upload_enabled=True,
        client=object(),
        upload_album="UI Album",
    )
    exact_plan = _h264_plan()
    monkeypatch.setattr(
        generate_module,
        "_build_assembly_settings",
        lambda *_args: AssemblySettings(encoding_plan=exact_plan),
    )
    tracker = RunTracker("ui-owned-run", db_path=tmp_path / "runs.db", capture_system=False)

    prepared = generate_memory(
        params,  # type: ignore[arg-type]
        run_tracker=tracker,
        defer_finalization=True,
    )
    saved = RunDatabase(tmp_path / "runs.db").get_run("ui-owned-run")

    assert isinstance(prepared, PreparedGeneration)
    assert prepared.encoding_plan is exact_plan
    assert prepared.clips_analyzed == 1
    assert prepared.clips_selected == 1
    assert len(prepared.assembly_clips) == 1
    assert prepared.path.read_bytes() == b"validated-artifact"
    assert events == []
    assert tracker.current_run is not None
    assert tracker.current_run.run_id == "ui-owned-run"
    assert saved is not None
    assert saved.status == "running"
    assert [phase.phase_name for phase in saved.phases] == ["clip_extraction", "assembly"]


def test_generation_without_upload_completes_artifact_as_not_requested(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A validated final artifact completes before returning, without a delivery attempt."""
    from immich_memories.generate import generate_memory

    params, events = _prepare_generation(
        tmp_path,
        monkeypatch,
        upload_enabled=False,
        client=None,
    )

    result = generate_memory(params)  # type: ignore[arg-type]
    saved = RunDatabase(tmp_path / "runs.db").get_run("delivery-run")

    assert result.read_bytes() == b"validated-artifact"
    assert events == ["final-probe"]
    assert saved is not None
    assert saved.status == "completed"
    assert saved.output_size_bytes == 4096
    assert saved.output_duration_seconds == 42.5
    assert saved.delivery_status is DeliveryStatus.NOT_REQUESTED
    assert saved.delivery_attempts == 0
    assert saved.automation_attempt_id == "attempt-generation"


@pytest.mark.parametrize("upload_enabled", [False, True], ids=["not-requested", "delivered"])
def test_final_progress_callback_failure_preserves_authoritative_artifact_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    upload_enabled: bool,
) -> None:
    """A final observer failure can escape, but cannot downgrade durable completion."""
    from immich_memories.generate import GenerationError, generate_memory

    params, _events = _prepare_generation(
        tmp_path,
        monkeypatch,
        upload_enabled=upload_enabled,
        client=object() if upload_enabled else None,
        upload_result={"asset_id": "asset-final-callback"},
    )

    def raise_after_completion(phase: str, _progress: float, _message: str) -> None:
        if phase == "done":
            raise RuntimeError("observer failed after durable completion")

    params.progress_callback = raise_after_completion  # type: ignore[attr-defined]

    with pytest.raises(GenerationError, match="observer failed after durable completion"):
        generate_memory(params)  # type: ignore[arg-type]

    saved = RunDatabase(tmp_path / "runs.db").get_run("delivery-run")
    assert saved is not None
    assert saved.status == "completed"
    assert saved.output_path is not None
    assert Path(saved.output_path).read_bytes() == b"validated-artifact"
    if upload_enabled:
        assert saved.delivery_status is DeliveryStatus.DELIVERED
        assert saved.immich_asset_id == "asset-final-callback"
    else:
        assert saved.delivery_status is DeliveryStatus.NOT_REQUESTED
        assert saved.immich_asset_id is None


def test_successful_generation_delivery_records_asset_and_original_album(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One successful API call records one attempt after artifact completion."""
    from immich_memories.generate import generate_memory

    params, events = _prepare_generation(
        tmp_path,
        monkeypatch,
        upload_enabled=True,
        client=object(),
        upload_album="Original Family Album",
        music_warning="Optional music failed: backend unavailable",
        no_music=False,
        upload_result={"asset_id": "asset-delivered"},
    )

    generate_memory(params)  # type: ignore[arg-type]
    saved = RunDatabase(tmp_path / "runs.db").get_run("delivery-run")

    assert events == ["music", "final-probe", "upload"]
    assert saved is not None
    assert saved.status == "completed"
    assert saved.delivery_status is DeliveryStatus.DELIVERED
    assert saved.delivery_attempts == 1
    assert saved.delivery_error is None
    assert saved.immich_asset_id == "asset-delivered"
    assert saved.delivery_album == "Original Family Album"
    assert saved.warnings == ["Optional music failed: backend unavailable"]
    assert saved.automation_attempt_id == "attempt-generation"


def test_final_progress_callback_cannot_downgrade_or_leak_delivered_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A presentation callback runs after delivery and cannot redefine its outcome."""
    from immich_memories.generate import GenerationError, generate_memory

    configured_literal = "final-progress-secret-426"
    params, events = _prepare_generation(
        tmp_path,
        monkeypatch,
        upload_enabled=True,
        client=object(),
        upload_album="Finished Album",
        upload_result={"asset_id": "asset-finished"},
        configured_secret=configured_literal,
        no_music=False,
    )

    def fail_final_progress(phase: str, _progress: float, _message: str) -> None:
        if phase == "done":
            raise RuntimeError(f"presentation failed with {configured_literal}")

    params.progress_callback = fail_final_progress
    caplog.set_level(logging.WARNING, logger="immich_memories.generate")

    with pytest.raises(GenerationError) as caught:
        generate_memory(params)  # type: ignore[arg-type]
    saved = RunDatabase(tmp_path / "runs.db").get_run("delivery-run")

    assert events == ["music", "final-probe", "upload"]
    assert saved is not None
    assert saved.status == "completed"
    assert saved.delivery_status is DeliveryStatus.DELIVERED
    assert saved.delivery_attempts == 1
    assert saved.immich_asset_id == "asset-finished"
    assert Path(saved.output_path or "").read_bytes() == b"validated-artifact"
    assert configured_literal not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert configured_literal not in caplog.text


def test_post_completion_exception_guard_preserves_delivered_database_truth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Even an unexpected post-commit error cannot call the destructive failure transition."""
    from immich_memories import generate as generate_module
    from immich_memories.generate import GenerationError, generate_memory

    configured_literal = "post-commit-diagnostic-secret-527"
    params, events = _prepare_generation(
        tmp_path,
        monkeypatch,
        upload_enabled=True,
        client=object(),
        upload_result={"asset_id": "asset-committed"},
        configured_secret=configured_literal,
        no_music=False,
    )

    def fail_timing(*_args: object) -> None:
        raise RuntimeError(f"diagnostic failed with {configured_literal}")

    monkeypatch.setattr(generate_module, "_log_phase_timing", fail_timing)
    caplog.set_level(logging.ERROR, logger="immich_memories.generate")

    with pytest.raises(GenerationError) as caught:
        generate_memory(params)  # type: ignore[arg-type]
    saved = RunDatabase(tmp_path / "runs.db").get_run("delivery-run")

    assert events == ["music", "final-probe", "upload"]
    assert saved is not None
    assert saved.status == "completed"
    assert saved.delivery_status is DeliveryStatus.DELIVERED
    assert saved.delivery_attempts == 1
    assert saved.immich_asset_id == "asset-committed"
    assert configured_literal not in str(caught.value)
    assert configured_literal not in "".join(traceback.format_exception(caught.value))
    assert configured_literal not in caplog.text
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_failed_generation_delivery_is_pending_and_does_not_fail_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed API call is retryable, sanitized, and cannot rewrite completion as failure."""
    from immich_memories.generate import DeliveryError, generate_memory

    configured_literal = "configured-delivery-secret"
    params, events = _prepare_generation(
        tmp_path,
        monkeypatch,
        upload_enabled=True,
        client=object(),
        upload_album="Album At Generation Time",
        upload_result=RuntimeError(f"Immich echoed {configured_literal}"),
        configured_secret=configured_literal,
    )

    with pytest.raises(DeliveryError, match="Immich delivery failed") as caught:
        generate_memory(params)  # type: ignore[arg-type]
    saved = RunDatabase(tmp_path / "runs.db").get_run("delivery-run")

    assert events == ["final-probe", "upload"]
    assert saved is not None
    assert saved.status == "completed"
    assert saved.delivery_status is DeliveryStatus.PENDING
    assert saved.delivery_attempts == 1
    assert saved.delivery_album == "Album At Generation Time"
    assert saved.immich_asset_id is None
    assert configured_literal not in (saved.delivery_error or "")
    assert configured_literal not in str(caught.value)
    assert configured_literal not in "".join(traceback.format_exception(caught.value))
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert saved.automation_attempt_id == "attempt-generation"


def test_requested_generation_delivery_without_client_is_pending_without_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing delivery configuration is durable pending work, not an invisible no-op."""
    from immich_memories.generate import DeliveryError, generate_memory

    params, events = _prepare_generation(
        tmp_path,
        monkeypatch,
        upload_enabled=True,
        client=None,
        upload_album="Album For Later Retry",
    )

    with pytest.raises(DeliveryError, match="no Immich client"):
        generate_memory(params)  # type: ignore[arg-type]
    saved = RunDatabase(tmp_path / "runs.db").get_run("delivery-run")

    assert events == ["final-probe"]
    assert saved is not None
    assert saved.status == "completed"
    assert saved.delivery_status is DeliveryStatus.PENDING
    assert saved.delivery_attempts == 0
    assert saved.delivery_album == "Album For Later Retry"
    assert saved.immich_asset_id is None
    assert saved.automation_attempt_id == "attempt-generation"


def test_delivery_transition_failure_cannot_count_or_downgrade_a_second_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A post-upload persistence failure never enters the API-failure transition."""
    from immich_memories.config_loader import Config
    from immich_memories.generate import (
        DeliveryError,
        GenerationParams,
        _deliver_completed_artifact,
    )

    class TransitionTracker:
        def __init__(self) -> None:
            self.attempts = 0
            self.pending_calls = 0

        def mark_delivered(self, _asset_id: str) -> None:
            self.attempts += 1
            raise OSError("sidecar refresh failed after the database commit")

        def mark_delivery_pending(self, _message: str, *, attempted: bool) -> None:
            self.pending_calls += 1
            self.attempts += int(attempted)

    tracker = TransitionTracker()
    params = GenerationParams(
        clips=[],
        output_path=tmp_path / "memory.mp4",
        config=Config(),
        client=object(),  # type: ignore[arg-type]
        upload_enabled=True,
    )
    monkeypatch.setattr(
        "immich_memories.generate._upload_to_immich",
        lambda *_args: {"asset_id": "asset-already-committed"},
    )

    with pytest.raises(DeliveryError):
        _deliver_completed_artifact(
            params,
            params.output_path,
            tracker,  # type: ignore[arg-type]
        )

    assert tracker.attempts == 1
    assert tracker.pending_calls == 0


def test_pending_state_persistence_failure_logs_no_exception_literal(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Secondary tracking failures use controlled text and cannot leak arbitrary secrets."""
    from immich_memories.generate import DeliveryError, _raise_delivery_error

    configured_literal = "unlabelled-tracking-secret"
    tracker = MagicMock()
    tracker.mark_delivery_pending.side_effect = OSError(configured_literal)

    with (
        caplog.at_level(logging.ERROR, logger="immich_memories.generate"),
        pytest.raises(DeliveryError),
    ):
        _raise_delivery_error(
            tracker,
            "safe delivery failure",
            attempted=True,
        )

    assert configured_literal not in caplog.text
    assert "Could not persist pending delivery state" in caplog.text


def test_generation_delivers_from_completed_database_state_when_sidecar_mirroring_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A JSON mirror failure cannot downgrade or block an authoritative artifact."""
    from immich_memories.generate import generate_memory

    configured_literal = "sidecar-write-detail-must-not-be-logged"
    params, events = _prepare_generation(
        tmp_path,
        monkeypatch,
        upload_enabled=True,
        client=object(),
        upload_album="Durable Album",
        upload_result={"asset_id": "asset-after-sidecar-failure"},
    )
    monkeypatch.setattr(
        RunTracker,
        "_save_metadata_json",
        lambda *_args: (_ for _ in ()).throw(OSError(configured_literal)),
    )
    caplog.set_level(logging.WARNING)

    result = generate_memory(params)  # type: ignore[arg-type]
    saved = RunDatabase(tmp_path / "runs.db").get_run("delivery-run")

    assert result.read_bytes() == b"validated-artifact"
    assert events == ["final-probe", "upload"]
    assert saved is not None
    assert saved.status == "completed"
    assert saved.output_path == str(result)
    assert saved.delivery_status is DeliveryStatus.DELIVERED
    assert saved.delivery_attempts == 1
    assert saved.immich_asset_id == "asset-after-sidecar-failure"
    assert configured_literal not in caplog.text


def test_generation_queues_failed_delivery_when_sidecar_mirroring_also_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A mirror failure cannot override the primary retryable delivery state."""
    from immich_memories.generate import DeliveryError, generate_memory

    configured_literal = "secondary-sidecar-detail-must-not-be-logged"
    params, events = _prepare_generation(
        tmp_path,
        monkeypatch,
        upload_enabled=True,
        client=object(),
        upload_album="Queued Album",
        upload_result=RuntimeError("Immich unavailable"),
    )
    monkeypatch.setattr(
        RunTracker,
        "_save_metadata_json",
        lambda *_args: (_ for _ in ()).throw(OSError(configured_literal)),
    )
    caplog.set_level(logging.WARNING)

    with pytest.raises(DeliveryError, match="Immich unavailable"):
        generate_memory(params)  # type: ignore[arg-type]
    saved = RunDatabase(tmp_path / "runs.db").get_run("delivery-run")

    assert events == ["final-probe", "upload"]
    assert saved is not None
    assert saved.status == "completed"
    assert saved.output_path is not None
    assert Path(saved.output_path).read_bytes() == b"validated-artifact"
    assert saved.delivery_status is DeliveryStatus.PENDING
    assert saved.delivery_attempts == 1
    assert saved.delivery_album == "Queued Album"
    assert configured_literal not in caplog.text


def test_hard_stop_after_artifact_commit_leaves_requested_delivery_pending(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A process stop between artifact commit and upload leaves durable retry work."""
    from immich_memories.generate import generate_memory

    params, events = _prepare_generation(
        tmp_path,
        monkeypatch,
        upload_enabled=True,
        client=object(),
        upload_album="Interrupted Album",
    )
    original_complete = RunTracker.complete_artifact

    def stop_after_commit(tracker: RunTracker, *args: object, **kwargs: object) -> None:
        original_complete(tracker, *args, **kwargs)  # type: ignore[arg-type]
        raise KeyboardInterrupt

    monkeypatch.setattr(RunTracker, "complete_artifact", stop_after_commit)

    with pytest.raises(KeyboardInterrupt):
        generate_memory(params)  # type: ignore[arg-type]
    saved = RunDatabase(tmp_path / "runs.db").get_run("delivery-run")

    assert events == ["final-probe"]
    assert saved is not None
    assert saved.status == "completed"
    assert saved.delivery_status is DeliveryStatus.PENDING
    assert saved.delivery_attempts == 0
    assert saved.delivery_album == "Interrupted Album"
    assert saved.output_path is not None
    assert Path(saved.output_path).is_file()


def test_hard_stop_during_upload_leaves_requested_delivery_pending(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A process stop inside the API call retains the pre-call retry record."""
    from immich_memories import generate as generate_module
    from immich_memories.generate import generate_memory

    params, events = _prepare_generation(
        tmp_path,
        monkeypatch,
        upload_enabled=True,
        client=object(),
        upload_album="In Flight Album",
    )

    def stop_during_upload(*_args: object, **_kwargs: object) -> None:
        events.append("upload-started")
        raise KeyboardInterrupt

    monkeypatch.setattr(generate_module, "_upload_to_immich", stop_during_upload)

    with pytest.raises(KeyboardInterrupt):
        generate_memory(params)  # type: ignore[arg-type]
    saved = RunDatabase(tmp_path / "runs.db").get_run("delivery-run")

    assert events == ["final-probe", "upload-started"]
    assert saved is not None
    assert saved.status == "completed"
    assert saved.delivery_status is DeliveryStatus.PENDING
    assert saved.delivery_attempts == 0
    assert saved.delivery_album == "In Flight Album"
    assert saved.output_path is not None
    assert Path(saved.output_path).is_file()
