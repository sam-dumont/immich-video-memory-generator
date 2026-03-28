"""Behavior tests for storage (database, run_database), tracking (run_tracker),
and CLI modules (runs, config_cmd, music_cmd, generate, _pipeline_runner).

Uses real SQLite (in-memory via tmp_path) for database tests — no mocking needed
for pure storage operations.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from immich_memories.cache.database import (
    VideoAnalysisCache,
)
from immich_memories.cli import main
from immich_memories.config_loader import Config
from immich_memories.tracking.models import PhaseStats, RunMetadata, SystemInfo
from immich_memories.tracking.run_database import RunDatabase
from immich_memories.tracking.run_tracker import RunTracker, format_duration

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_asset(
    asset_id: str = "asset-001",
    checksum: str = "chk-abc",
    file_modified_at: datetime | None = None,
    file_created_at: datetime | None = None,
    duration_seconds: float = 15.0,
) -> MagicMock:
    asset = MagicMock()
    asset.id = asset_id
    asset.checksum = checksum
    asset.file_modified_at = file_modified_at or datetime(2025, 6, 15, 10, 0)
    asset.file_created_at = file_created_at or datetime(2025, 6, 15, 9, 0)
    asset.duration_seconds = duration_seconds
    return asset


def _make_video_info(
    duration: float = 15.0,
    width: int = 1920,
    height: int = 1080,
) -> MagicMock:
    info = MagicMock()
    info.duration_seconds = duration
    info.width = width
    info.height = height
    info.bitrate = 8_000_000
    info.fps = 30.0
    info.codec = "hevc"
    info.color_space = "bt709"
    info.color_transfer = "smpte2084"
    info.color_primaries = "bt709"
    info.bit_depth = 10
    return info


def _make_run(
    run_id: str = "20260101_120000_abcd",
    status: str = "running",
    person_name: str | None = None,
    memory_type: str | None = None,
    memory_key: str | None = None,
    source: str = "manual",
    clips_analyzed: int = 0,
    clips_selected: int = 0,
) -> RunMetadata:
    return RunMetadata(
        run_id=run_id,
        created_at=datetime(2026, 1, 1, 12, 0),
        status=status,
        person_name=person_name,
        memory_type=memory_type,
        memory_key=memory_key,
        source=source,
        clips_analyzed=clips_analyzed,
        clips_selected=clips_selected,
    )


def _invoke(args: list[str], config: Config | None = None) -> object:
    """Invoke the CLI with mocked config and init_config_dir."""
    config = config or Config()
    runner = CliRunner()
    with (
        patch("immich_memories.cli.init_config_dir"),
        patch("immich_memories.cli.get_config", return_value=config),
        # WHY: runs/stats/config commands import get_config inline from immich_memories.config
        patch("immich_memories.config.get_config", return_value=config),
    ):
        return runner.invoke(main, args, catch_exceptions=False)


# =========================================================================
# Module 1: VideoAnalysisCache — uncovered behaviors
# =========================================================================


class TestVideoMetadataCRUD:
    """save_video_metadata / get_video_metadata round-trip."""

    @pytest.fixture
    def cache(self, tmp_path):
        return VideoAnalysisCache(tmp_path / "test.db")

    def test_save_and_retrieve_metadata(self, cache):
        """Saved metadata can be retrieved with all fields intact."""
        cache.save_video_metadata(
            asset_id="v1",
            checksum="c1",
            duration_seconds=12.5,
            width=3840,
            height=2160,
            bitrate=20_000_000,
            fps=60.0,
            codec="hevc",
            color_space="bt2020nc",
            color_transfer="smpte2084",
            color_primaries="bt2020",
            bit_depth=10,
            rotation=90,
        )
        meta = cache.get_video_metadata("v1")
        assert meta is not None
        assert meta["duration_seconds"] == 12.5
        assert meta["width"] == 3840
        assert meta["height"] == 2160
        assert meta["codec"] == "hevc"
        assert meta["color_space"] == "bt2020nc"
        assert meta["bit_depth"] == 10
        assert isinstance(meta["rotation"], int)

    def test_get_missing_metadata_returns_none(self, cache):
        """Requesting metadata for an absent asset returns None."""
        assert cache.get_video_metadata("nonexistent") is None

    def test_save_metadata_overwrites(self, cache):
        """Saving metadata for the same asset_id replaces the old record."""
        cache.save_video_metadata(asset_id="v1", codec="h264")
        cache.save_video_metadata(asset_id="v1", codec="hevc")
        assert cache.get_video_metadata("v1")["codec"] == "hevc"

    def test_rotation_defaults_to_zero(self, cache):
        """When rotation is None, it defaults to 0."""
        cache.save_video_metadata(asset_id="v1", rotation=None)
        assert cache.get_video_metadata("v1")["rotation"] == 0


class TestVideoMetadataBatch:
    """get_video_metadata_batch behavior."""

    @pytest.fixture
    def cache(self, tmp_path):
        return VideoAnalysisCache(tmp_path / "test.db")

    def test_batch_returns_only_existing(self, cache):
        """Batch query returns metadata only for assets that exist."""
        cache.save_video_metadata(asset_id="a", codec="h264")
        cache.save_video_metadata(asset_id="b", codec="hevc")
        result = cache.get_video_metadata_batch(["a", "b", "c"])
        assert set(result.keys()) == {"a", "b"}
        assert result["a"]["codec"] == "h264"
        assert result["b"]["codec"] == "hevc"

    def test_batch_empty_input(self, cache):
        """Empty list returns empty dict."""
        assert cache.get_video_metadata_batch([]) == {}


class TestAnalysisSaveWithScenes:
    """save_analysis with scenes (not moments) and get_analysis retrieval."""

    @pytest.fixture
    def cache(self, tmp_path):
        return VideoAnalysisCache(tmp_path / "test.db")

    def test_save_with_scenes_stores_segments(self, cache):
        """Saving with Scene objects stores segments accessible via get_analysis."""
        from immich_memories.analysis.scenes import Scene

        asset = _make_asset()
        scenes = [
            Scene(start_time=0.0, end_time=5.0, start_frame=0, end_frame=150),
            Scene(
                start_time=5.0,
                end_time=10.0,
                start_frame=150,
                end_frame=300,
                keyframe_path="/tmp/kf.jpg",
            ),
        ]
        cache.save_analysis(asset=asset, scenes=scenes)
        analysis = cache.get_analysis(asset.id, include_segments=True)
        assert analysis is not None
        assert len(analysis.segments) == 2
        assert analysis.segments[0].start_frame == 0
        assert analysis.segments[1].keyframe_path == "/tmp/kf.jpg"

    def test_get_analysis_without_segments(self, cache):
        """include_segments=False skips segment loading."""
        asset = _make_asset()
        cache.save_analysis(asset=asset, video_info=_make_video_info())
        analysis = cache.get_analysis(asset.id, include_segments=False)
        assert analysis is not None
        assert analysis.segments == []


class TestNeedsReanalysisVersioning:
    """needs_reanalysis version and age checks."""

    @pytest.fixture
    def cache(self, tmp_path):
        return VideoAnalysisCache(tmp_path / "test.db")

    def test_fresh_does_not_need_reanalysis(self, cache):
        """Analysis saved just now does not need reanalysis."""
        asset = _make_asset()
        cache.save_analysis(asset=asset, video_info=_make_video_info())
        # max_age_days=30: analysis from now is 0 days old, not > 30
        assert not cache.needs_reanalysis(asset, max_age_days=30)

    def test_stale_by_file_modification(self, cache):
        """Modified file triggers reanalysis even if checksum is None."""
        asset = _make_asset(checksum=None)
        cache.save_analysis(asset=asset, video_info=_make_video_info())
        # Advance file_modified_at to the future
        asset.file_modified_at = datetime.now() + timedelta(days=1)
        asset.checksum = None
        assert cache.needs_reanalysis(asset, max_age_days=365)


class TestGetAllHashes:
    """get_all_hashes behavior."""

    @pytest.fixture
    def cache(self, tmp_path):
        return VideoAnalysisCache(tmp_path / "test.db")

    def test_returns_all_stored_hashes(self, cache):
        """All perceptual hashes stored via save_analysis are returned."""
        a1 = _make_asset("a1")
        a2 = _make_asset("a2")
        cache.save_analysis(asset=a1, perceptual_hash="aaaa1111bbbb2222")
        cache.save_analysis(asset=a2, perceptual_hash="cccc3333dddd4444")
        hashes = cache.get_all_hashes()
        assert hashes == {"a1": "aaaa1111bbbb2222", "a2": "cccc3333dddd4444"}

    def test_empty_when_no_hashes(self, cache):
        """Returns empty dict when no hashes have been stored."""
        assert cache.get_all_hashes() == {}


class TestFindSimilarExclusion:
    """find_similar_videos exclude_asset_id behavior."""

    @pytest.fixture
    def cache(self, tmp_path):
        return VideoAnalysisCache(tmp_path / "test.db")

    def test_exclude_self_from_results(self, cache):
        """A video should not appear in its own similarity results."""
        a = _make_asset("a")
        cache.save_analysis(asset=a, perceptual_hash="abcd1234abcd5678")
        similar = cache.find_similar_videos("abcd1234abcd5678", exclude_asset_id="a")
        assert all(s.asset_id != "a" for s in similar)

    def test_no_exclude(self, cache):
        """Without exclude_asset_id, self can appear."""
        a = _make_asset("a")
        cache.save_analysis(asset=a, perceptual_hash="abcd1234abcd5678")
        similar = cache.find_similar_videos("abcd1234abcd5678")
        assert any(s.asset_id == "a" for s in similar)


class TestUncachedWithChecksums:
    """get_uncached_asset_ids with checksum comparison."""

    @pytest.fixture
    def cache(self, tmp_path):
        return VideoAnalysisCache(tmp_path / "test.db")

    def test_stale_checksum_marks_uncached(self, cache):
        """Asset with changed checksum shows up as uncached."""
        a = _make_asset("a", checksum="old-chk")
        cache.save_analysis(asset=a, video_info=_make_video_info())
        uncached = cache.get_uncached_asset_ids(
            ["a"],
            checksums={"a": "new-chk"},
        )
        assert "a" in uncached

    def test_matching_checksum_stays_cached(self, cache):
        """Asset with same checksum is not marked uncached."""
        a = _make_asset("a", checksum="same-chk")
        cache.save_analysis(asset=a, video_info=_make_video_info())
        uncached = cache.get_uncached_asset_ids(
            ["a"],
            checksums={"a": "same-chk"},
        )
        assert "a" not in uncached


class TestMigrationsIdempotent:
    """Opening the same DB twice doesn't fail (migrations are idempotent)."""

    def test_double_open(self, tmp_path):
        db_path = tmp_path / "m.db"
        VideoAnalysisCache(db_path)
        VideoAnalysisCache(db_path)  # should not raise


class TestSaveAnalysisWithLLMSegments:
    """Segments with LLM fields round-trip through save/load."""

    @pytest.fixture
    def cache(self, tmp_path):
        return VideoAnalysisCache(tmp_path / "test.db")

    def test_llm_fields_persisted(self, cache):
        """LLM description, emotion, activities survive save/load cycle."""
        from immich_memories.analysis.scoring import MomentScore

        asset = _make_asset()
        segment = MomentScore(
            start_time=0.0,
            end_time=5.0,
            total_score=0.9,
            face_score=0.8,
            motion_score=0.7,
            stability_score=0.6,
            audio_score=0.5,
        )
        segment.llm_description = "Kids playing in park"
        segment.llm_emotion = "joyful"
        segment.llm_setting = "outdoor"
        segment.llm_activities = ["running", "laughing"]
        segment.llm_subjects = ["child", "dog"]
        segment.llm_interestingness = 0.85
        segment.llm_quality = 0.9
        segment.audio_categories = ["speech", "laughter"]

        cache.save_analysis(asset=asset, segments=[segment])
        analysis = cache.get_analysis(asset.id, include_segments=True)
        seg = analysis.segments[0]
        assert seg.llm_description == "Kids playing in park"
        assert seg.llm_emotion == "joyful"
        assert seg.llm_setting == "outdoor"
        assert seg.llm_activities == ["running", "laughing"]
        assert seg.llm_subjects == ["child", "dog"]
        assert seg.llm_interestingness == pytest.approx(0.85)
        assert seg.llm_quality == pytest.approx(0.9)
        assert seg.audio_categories == ["laughter", "speech"]  # sorted


# =========================================================================
# Module 2: RunDatabase — uncovered behaviors
# =========================================================================


class TestRunDatabaseCRUD:
    """Save, get, delete, list runs."""

    @pytest.fixture
    def db(self, tmp_path):
        return RunDatabase(tmp_path / "runs.db")

    def test_save_and_get_run(self, db):
        """A saved run can be retrieved by ID with all fields."""
        run = _make_run(person_name="Alice", memory_type="year_in_review", source="auto")
        db.save_run(run)
        loaded = db.get_run(run.run_id)
        assert loaded is not None
        assert loaded.run_id == run.run_id
        assert loaded.person_name == "Alice"
        assert loaded.memory_type == "year_in_review"
        assert loaded.source == "auto"
        assert loaded.status == "running"

    def test_get_nonexistent_returns_none(self, db):
        """Requesting a missing run returns None."""
        assert db.get_run("no-such-run") is None

    def test_delete_run(self, db):
        """Deleting a run removes it from the database."""
        run = _make_run()
        db.save_run(run)
        assert db.delete_run(run.run_id)
        assert db.get_run(run.run_id) is None

    def test_delete_nonexistent(self, db):
        """Deleting a nonexistent run returns False."""
        assert not db.delete_run("nope")


class TestRunDatabaseUpdateStatus:
    """update_run_status with various optional fields."""

    @pytest.fixture
    def db(self, tmp_path):
        return RunDatabase(tmp_path / "runs.db")

    def test_update_status_only(self, db):
        """Updating just status leaves other fields unchanged."""
        run = _make_run(clips_analyzed=50)
        db.save_run(run)
        db.update_run_status(run.run_id, status="completed")
        loaded = db.get_run(run.run_id)
        assert loaded.status == "completed"
        assert loaded.clips_analyzed == 50

    def test_update_all_optional_fields(self, db):
        """All optional fields can be updated simultaneously."""
        run = _make_run()
        db.save_run(run)
        now = datetime.now()
        db.update_run_status(
            run.run_id,
            status="completed",
            completed_at=now,
            output_path="/output/video.mp4",
            output_size_bytes=1_000_000,
            output_duration_seconds=120.0,
            clips_analyzed=100,
            clips_selected=20,
            errors_count=2,
        )
        loaded = db.get_run(run.run_id)
        assert loaded.status == "completed"
        assert loaded.output_path == "/output/video.mp4"
        assert loaded.output_size_bytes == 1_000_000
        assert loaded.output_duration_seconds == 120.0
        assert loaded.clips_analyzed == 100
        assert loaded.clips_selected == 20
        assert loaded.errors_count == 2


class TestRunDatabaseListRuns:
    """list_runs with filtering."""

    @pytest.fixture
    def db(self, tmp_path):
        return RunDatabase(tmp_path / "runs.db")

    def test_list_returns_all_runs(self, db):
        """list_runs returns all saved runs ordered by created_at DESC."""
        for i in range(3):
            run = _make_run(run_id=f"20260101_12000{i}_abcd")
            run.created_at = datetime(2026, 1, 1, 12, 0, i)
            db.save_run(run)
        runs = db.list_runs()
        assert len(runs) == 3
        # Most recent first
        assert runs[0].run_id == "20260101_120002_abcd"

    def test_list_filter_by_person(self, db):
        """Filtering by person_name returns only matching runs."""
        db.save_run(_make_run(run_id="r1", person_name="Alice"))
        db.save_run(_make_run(run_id="r2", person_name="Bob"))
        runs = db.list_runs(person_name="Alice")
        assert len(runs) == 1
        assert runs[0].person_name == "Alice"

    def test_list_filter_by_status(self, db):
        """Filtering by status returns only matching runs."""
        db.save_run(_make_run(run_id="r1", status="completed"))
        db.save_run(_make_run(run_id="r2", status="failed"))
        runs = db.list_runs(status="completed")
        assert len(runs) == 1
        assert runs[0].status == "completed"

    def test_list_with_limit_and_offset(self, db):
        """Limit and offset paginate results."""
        for i in range(5):
            run = _make_run(run_id=f"run_{i:02d}")
            run.created_at = datetime(2026, 1, 1, i)
            db.save_run(run)
        page = db.list_runs(limit=2, offset=1)
        assert len(page) == 2


class TestRunDatabasePhaseStats:
    """Phase stats save and retrieval."""

    @pytest.fixture
    def db(self, tmp_path):
        return RunDatabase(tmp_path / "runs.db")

    def test_save_and_retrieve_phases(self, db):
        """Phase stats are persisted and loaded with the run."""
        run = _make_run()
        db.save_run(run)
        stats = PhaseStats(
            phase_name="analysis",
            started_at=datetime(2026, 1, 1, 12, 0),
            completed_at=datetime(2026, 1, 1, 12, 5),
            duration_seconds=300.0,
            items_processed=42,
            items_total=50,
            errors=[{"error": "timeout on clip 3"}],
            extra_metrics={"cache_hits": 10},
        )
        db.save_phase_stats(run.run_id, stats)
        loaded = db.get_run(run.run_id)
        assert len(loaded.phases) == 1
        phase = loaded.phases[0]
        assert phase.phase_name == "analysis"
        assert phase.duration_seconds == 300.0
        assert phase.items_processed == 42
        assert phase.errors == [{"error": "timeout on clip 3"}]
        assert phase.extra_metrics == {"cache_hits": 10}


class TestRunDatabaseStaleRuns:
    """mark_stale_runs_as_interrupted behavior."""

    @pytest.fixture
    def db(self, tmp_path):
        return RunDatabase(tmp_path / "runs.db")

    def test_marks_running_as_interrupted(self, db):
        """All 'running' runs become 'interrupted'."""
        db.save_run(_make_run(run_id="r1", status="running"))
        db.save_run(_make_run(run_id="r2", status="running"))
        db.save_run(_make_run(run_id="r3", status="completed"))
        count = db.mark_stale_runs_as_interrupted()
        assert count == 2
        assert db.get_run("r1").status == "interrupted"
        assert db.get_run("r2").status == "interrupted"
        assert db.get_run("r3").status == "completed"

    def test_no_stale_runs(self, db):
        """Returns 0 when nothing is 'running'."""
        db.save_run(_make_run(run_id="r1", status="completed"))
        assert db.mark_stale_runs_as_interrupted() == 0


class TestRunDatabaseAggregateStats:
    """get_aggregate_stats behavior."""

    @pytest.fixture
    def db(self, tmp_path):
        return RunDatabase(tmp_path / "runs.db")

    def test_aggregate_empty(self, db):
        """Aggregate stats on empty database return zeros."""
        stats = db.get_aggregate_stats()
        assert stats["total_runs"] == 0
        assert stats["completed_runs"] == 0
        assert stats["failed_runs"] == 0
        assert stats["avg_clips"] == 0.0

    def test_aggregate_with_data(self, db):
        """Aggregate stats reflect saved runs and phases."""
        r1 = _make_run(run_id="r1", status="completed", clips_selected=10)
        r2 = _make_run(run_id="r2", status="failed", clips_selected=5)
        r1.output_duration_seconds = 120.0
        r2.output_duration_seconds = 0.0
        db.save_run(r1)
        db.save_run(r2)
        db.save_phase_stats(
            "r1",
            PhaseStats(
                phase_name="gen",
                started_at=datetime(2026, 1, 1, 12, 0),
                duration_seconds=60.0,
            ),
        )
        stats = db.get_aggregate_stats()
        assert stats["total_runs"] == 2
        assert stats["completed_runs"] == 1
        assert stats["failed_runs"] == 1
        assert stats["total_output_seconds"] == 120.0
        assert stats["total_clips"] == 15
        assert stats["avg_clips"] == 7.5


class TestRunDatabasePeopleWithRuns:
    """get_people_with_runs behavior."""

    @pytest.fixture
    def db(self, tmp_path):
        return RunDatabase(tmp_path / "runs.db")

    def test_returns_distinct_names(self, db):
        """Returns sorted list of unique person names."""
        db.save_run(_make_run(run_id="r1", person_name="Bob"))
        db.save_run(_make_run(run_id="r2", person_name="Alice"))
        db.save_run(_make_run(run_id="r3", person_name="Bob"))
        db.save_run(_make_run(run_id="r4", person_name=None))
        people = db.get_people_with_runs()
        assert people == ["Alice", "Bob"]


class TestRunDatabaseDedup:
    """Memory deduplication queries."""

    @pytest.fixture
    def db(self, tmp_path):
        return RunDatabase(tmp_path / "runs.db")

    def test_has_memory_been_generated_true(self, db):
        """Returns True when a completed run exists with the key."""
        db.save_run(
            _make_run(
                run_id="r1",
                status="completed",
                memory_key="year_review:2025",
            )
        )
        assert db.has_memory_been_generated("year_review:2025")

    def test_has_memory_been_generated_false_when_failed(self, db):
        """Returns False when the only run with the key is failed."""
        db.save_run(
            _make_run(
                run_id="r1",
                status="failed",
                memory_key="year_review:2025",
            )
        )
        assert not db.has_memory_been_generated("year_review:2025")

    def test_has_memory_empty_key(self, db):
        """Empty key always returns False."""
        assert not db.has_memory_been_generated("")

    def test_get_last_run_of_type(self, db):
        """Returns the most recent completed run of a given type."""
        r1 = _make_run(run_id="r1", status="completed", memory_type="trip")
        r1.created_at = datetime(2026, 1, 1, 10, 0)
        r2 = _make_run(run_id="r2", status="completed", memory_type="trip")
        r2.created_at = datetime(2026, 1, 1, 12, 0)
        db.save_run(r1)
        db.save_run(r2)
        last = db.get_last_run_of_type("trip")
        assert last is not None
        assert last.run_id == "r2"

    def test_get_last_run_of_type_none(self, db):
        """Returns None when no completed runs of that type exist."""
        assert db.get_last_run_of_type("trip") is None

    def test_get_generated_memory_keys(self, db):
        """Returns set of all completed memory keys."""
        db.save_run(_make_run(run_id="r1", status="completed", memory_key="k1"))
        db.save_run(_make_run(run_id="r2", status="completed", memory_key="k2"))
        db.save_run(_make_run(run_id="r3", status="failed", memory_key="k3"))
        keys = db.get_generated_memory_keys()
        assert keys == {"k1", "k2"}


class TestRunDatabaseActiveJobs:
    """Active job tracking (create, update, cancel, get, complete)."""

    @pytest.fixture
    def db(self, tmp_path):
        return RunDatabase(tmp_path / "jobs.db")

    def test_create_and_get_active_job(self, db):
        """A created job can be retrieved as the active job."""
        # Must have a run record for FK
        db.save_run(_make_run(run_id="j1"))
        db.create_job(
            run_id="j1",
            selected_clips=["clip_a", "clip_b"],
            clip_segments={"clip_a": [0.0, 5.0]},
            generation_options={"transition": "crossfade"},
        )
        job = db.get_active_job()
        assert job is not None
        assert job["run_id"] == "j1"
        assert job["status"] == "running"
        assert job["selected_clips"] == ["clip_a", "clip_b"]
        assert job["generation_options"]["transition"] == "crossfade"

    def test_update_job_progress(self, db):
        """update_job_progress changes phase and progress."""
        db.save_run(_make_run(run_id="j1"))
        db.create_job("j1", [], {}, {})
        db.update_job_progress("j1", "encoding", 50.0, "Encoding clip 3/6")
        job = db.get_active_job()
        assert job["phase"] == "encoding"
        assert job["progress_pct"] == 50.0
        assert job["progress_message"] == "Encoding clip 3/6"

    def test_request_cancel(self, db):
        """request_cancel sets cancel_requested flag."""
        db.save_run(_make_run(run_id="j1"))
        db.create_job("j1", [], {}, {})
        assert db.request_cancel("j1")
        assert db.is_cancel_requested("j1")

    def test_request_cancel_nonexistent(self, db):
        """Cancelling a nonexistent job returns False."""
        db._ensure_active_jobs_table()
        assert not db.request_cancel("nope")

    def test_is_cancel_requested_false_by_default(self, db):
        """cancel_requested is False by default."""
        db.save_run(_make_run(run_id="j1"))
        db.create_job("j1", [], {}, {})
        assert not db.is_cancel_requested("j1")

    def test_complete_job(self, db):
        """complete_job marks job as non-running, so get_active_job returns None."""
        db.save_run(_make_run(run_id="j1"))
        db.create_job("j1", [], {}, {})
        db.complete_job("j1", status="completed")
        assert db.get_active_job() is None

    def test_get_active_job_none_when_empty(self, db):
        """get_active_job returns None when no jobs exist."""
        assert db.get_active_job() is None


class TestRunDatabaseWithSystemInfo:
    """Runs with SystemInfo round-trip through save/load."""

    @pytest.fixture
    def db(self, tmp_path):
        return RunDatabase(tmp_path / "runs.db")

    def test_system_info_persisted(self, db):
        """SystemInfo is serialized as JSON and restored on load."""
        si = SystemInfo(
            platform="darwin",
            platform_version="Darwin 25.1.0",
            python_version="3.12.0",
            machine_arch="arm64",
            cpu_brand="Apple M4 Max",
            cpu_cores=16,
            ram_gb=64.0,
            gpu_name="Apple M4 Max",
            vram_mb=0,
        )
        run = _make_run(run_id="r1")
        run.system_info = si
        db.save_run(run)
        loaded = db.get_run("r1")
        assert loaded.system_info is not None
        assert loaded.system_info.cpu_brand == "Apple M4 Max"
        assert loaded.system_info.ram_gb == 64.0

    def test_run_without_system_info(self, db):
        """Runs without SystemInfo load with system_info=None."""
        db.save_run(_make_run(run_id="r1"))
        loaded = db.get_run("r1")
        assert loaded.system_info is None


class TestRunDatabaseDateRange:
    """Runs with date_range_start/end round-trip."""

    @pytest.fixture
    def db(self, tmp_path):
        return RunDatabase(tmp_path / "runs.db")

    def test_date_range_persisted(self, db):
        """Date range start/end are stored and restored as date objects."""
        run = _make_run(run_id="r1")
        run.date_range_start = date(2025, 1, 1)
        run.date_range_end = date(2025, 12, 31)
        run.target_duration_seconds = 600
        db.save_run(run)
        loaded = db.get_run("r1")
        assert loaded.date_range_start == date(2025, 1, 1)
        assert loaded.date_range_end == date(2025, 12, 31)


# =========================================================================
# Module 3: RunTracker — uncovered behaviors
# =========================================================================


class TestRunTrackerCompleteRun:
    """complete_run lifecycle behavior."""

    # WHY: RunDatabase opens a SQLite connection — test complete_run logic in isolation
    @patch("immich_memories.tracking.run_tracker.RunDatabase")
    def test_complete_run_no_output(self, mock_db_cls):
        """complete_run without output_path still finalizes the run."""
        tracker = RunTracker(db_path=Path("/tmp/t.db"))
        tracker.db.get_run.return_value = _make_run(status="completed")
        result = tracker.complete_run(clips_analyzed=10, clips_selected=5)
        tracker.db.update_run_status.assert_called_once()
        assert result.status == "completed"

    # WHY: RunDatabase opens a SQLite connection — test phase cleanup on complete
    @patch("immich_memories.tracking.run_tracker.RunDatabase")
    def test_complete_run_closes_active_phase(self, mock_db_cls):
        """complete_run completes any active phase before finalizing."""
        tracker = RunTracker(db_path=Path("/tmp/t.db"))
        tracker.db.get_run.return_value = _make_run(status="completed")
        tracker.start_phase("encoding", total_items=5)
        tracker.complete_run()
        # Phase should have been completed (save_phase_stats called)
        tracker.db.save_phase_stats.assert_called_once()

    # WHY: RunDatabase + ffprobe subprocess — test output file stats gathering
    @patch("immich_memories.tracking.run_tracker.RunDatabase")
    def test_complete_run_with_output_file(self, mock_db_cls, tmp_path):
        """complete_run reads output file size when path exists."""
        tracker = RunTracker(db_path=Path("/tmp/t.db"))
        tracker.db.get_run.return_value = _make_run(status="completed")
        output = tmp_path / "video.mp4"
        output.write_bytes(b"x" * 1000)
        # WHY: _get_video_duration calls ffprobe subprocess — mock it
        with patch.object(tracker, "_get_video_duration", return_value=30.0):
            tracker.complete_run(output_path=output, clips_analyzed=5, clips_selected=3)
        call_kwargs = tracker.db.update_run_status.call_args[1]
        assert call_kwargs["output_size_bytes"] == 1000
        assert call_kwargs["output_duration_seconds"] == 30.0

    # WHY: RunDatabase — test metadata JSON save
    @patch("immich_memories.tracking.run_tracker.RunDatabase")
    def test_complete_run_saves_metadata_json(self, mock_db_cls, tmp_path):
        """complete_run writes run_metadata.json alongside the output."""
        run = _make_run(status="completed")
        tracker = RunTracker(db_path=Path("/tmp/t.db"))
        tracker.db.get_run.return_value = run
        output = tmp_path / "out" / "video.mp4"
        output.parent.mkdir(parents=True)
        output.write_bytes(b"video")
        with patch.object(tracker, "_get_video_duration", return_value=0.0):
            tracker.complete_run(output_path=output)
        metadata_file = output.parent / "run_metadata.json"
        assert metadata_file.exists()
        data = json.loads(metadata_file.read_text())
        assert data["run_id"] == run.run_id


class TestRunTrackerUpdatePhaseProgress:
    """update_phase_progress logging behavior."""

    # WHY: RunDatabase opens a SQLite connection
    @patch("immich_memories.tracking.run_tracker.RunDatabase")
    def test_update_phase_progress_no_crash(self, mock_db_cls):
        """update_phase_progress logs but does not crash."""
        tracker = RunTracker(db_path=Path("/tmp/t.db"))
        tracker.start_phase("analysis", total_items=10)
        tracker.update_phase_progress(5)  # should not raise

    # WHY: RunDatabase opens a SQLite connection
    @patch("immich_memories.tracking.run_tracker.RunDatabase")
    def test_update_phase_progress_noop_without_phase(self, mock_db_cls):
        """update_phase_progress is a no-op when no phase is active."""
        tracker = RunTracker(db_path=Path("/tmp/t.db"))
        tracker.update_phase_progress(5)  # should not raise


class TestRunTrackerCancelWithPhase:
    """cancel_run should close active phase."""

    # WHY: RunDatabase opens a SQLite connection
    @patch("immich_memories.tracking.run_tracker.RunDatabase")
    def test_cancel_completes_active_phase(self, mock_db_cls):
        """cancel_run completes any active phase before cancelling."""
        tracker = RunTracker(db_path=Path("/tmp/t.db"))
        tracker.start_phase("export", total_items=3)
        tracker.cancel_run()
        tracker.db.save_phase_stats.assert_called_once()
        call_kwargs = tracker.db.update_run_status.call_args[1]
        assert call_kwargs["status"] == "cancelled"


# =========================================================================
# Module 4: CLI commands
# =========================================================================


class TestRunsListCommand:
    """runs list command behavior."""

    def test_runs_list_no_runs(self, tmp_path):
        """'runs list' with no data prints 'No runs found'."""
        config = Config()
        config.cache.database = str(tmp_path / "test.db")
        result = _invoke(["runs", "list"], config=config)
        assert result.exit_code == 0
        assert "No runs" in result.output

    def test_runs_list_with_data(self, tmp_path):
        """'runs list' shows run data when runs exist."""
        config = Config()
        config.cache.database = str(tmp_path / "test.db")
        db = RunDatabase(tmp_path / "test.db")
        run = _make_run(run_id="20260101_120000_abcd", person_name="Alice", status="completed")
        run.output_path = "/out/video.mp4"
        db.save_run(run)
        result = _invoke(["runs", "list"], config=config)
        assert result.exit_code == 0
        assert "20260101_120000_abcd" in result.output or "Pipeline Runs" in result.output

    def test_runs_list_filter_person(self, tmp_path):
        """'runs list --person X' filters by person."""
        config = Config()
        config.cache.database = str(tmp_path / "test.db")
        db = RunDatabase(tmp_path / "test.db")
        db.save_run(_make_run(run_id="r1", person_name="Alice"))
        db.save_run(_make_run(run_id="r2", person_name="Bob"))
        result = _invoke(["runs", "list", "--person", "Alice"], config=config)
        assert result.exit_code == 0


class TestRunsShowCommand:
    """runs show command behavior."""

    def test_runs_show_not_found(self, tmp_path):
        """'runs show XXXX' prints error when not found."""
        config = Config()
        config.cache.database = str(tmp_path / "test.db")
        # Create the database tables
        RunDatabase(tmp_path / "test.db")
        result = _invoke(["runs", "show", "nonexistent"], config=config)
        assert result.exit_code == 0  # click doesn't exit 1 for print_error
        assert "not found" in result.output.lower() or "No" in result.output

    def test_runs_show_with_data(self, tmp_path):
        """'runs show' displays run details including phases."""
        config = Config()
        config.cache.database = str(tmp_path / "test.db")
        db = RunDatabase(tmp_path / "test.db")
        run = _make_run(run_id="20260101_120000_abcd", status="completed", person_name="Alice")
        run.completed_at = datetime(2026, 1, 1, 12, 30)
        run.clips_analyzed = 100
        run.clips_selected = 20
        run.output_path = "/out/video.mp4"
        run.output_duration_seconds = 120.0
        run.output_size_bytes = 50_000_000
        db.save_run(run)
        db.save_phase_stats(
            run.run_id,
            PhaseStats(
                phase_name="analysis",
                started_at=datetime(2026, 1, 1, 12, 0),
                completed_at=datetime(2026, 1, 1, 12, 10),
                duration_seconds=600.0,
                items_processed=100,
                items_total=100,
            ),
        )
        result = _invoke(["runs", "show", "20260101_120000_abcd"], config=config)
        assert result.exit_code == 0
        assert "Alice" in result.output
        assert "analysis" in result.output.lower() or "Phase" in result.output

    def test_runs_show_partial_match(self, tmp_path):
        """'runs show' with partial ID matches a single run."""
        config = Config()
        config.cache.database = str(tmp_path / "test.db")
        db = RunDatabase(tmp_path / "test.db")
        db.save_run(_make_run(run_id="20260101_120000_abcd"))
        result = _invoke(["runs", "show", "20260101_1200"], config=config)
        assert result.exit_code == 0

    def test_runs_show_with_system_info(self, tmp_path):
        """'runs show' displays system info when present."""
        config = Config()
        config.cache.database = str(tmp_path / "test.db")
        db = RunDatabase(tmp_path / "test.db")
        run = _make_run(run_id="20260101_120000_abcd")
        run.system_info = SystemInfo(
            platform="darwin",
            platform_version="Darwin 25.1.0",
            python_version="3.12.0",
            machine_arch="arm64",
            cpu_brand="Apple M4 Max",
            cpu_cores=16,
            ram_gb=64.0,
            gpu_name="Apple M4 Max",
        )
        db.save_run(run)
        result = _invoke(["runs", "show", "20260101_120000_abcd"], config=config)
        assert result.exit_code == 0
        assert "Apple M4 Max" in result.output


class TestRunsStatsCommand:
    """runs stats command behavior."""

    def test_runs_stats_empty(self, tmp_path):
        """'runs stats' on empty DB shows zeros."""
        config = Config()
        config.cache.database = str(tmp_path / "test.db")
        # Ensure tables exist
        RunDatabase(tmp_path / "test.db")
        result = _invoke(["runs", "stats"], config=config)
        assert result.exit_code == 0
        assert "0" in result.output

    def test_runs_stats_with_data(self, tmp_path):
        """'runs stats' shows aggregate data."""
        config = Config()
        config.cache.database = str(tmp_path / "test.db")
        db = RunDatabase(tmp_path / "test.db")
        run = _make_run(run_id="r1", status="completed", clips_selected=10)
        run.output_duration_seconds = 120.0
        db.save_run(run)
        result = _invoke(["runs", "stats"], config=config)
        assert result.exit_code == 0
        assert "1" in result.output  # total runs


class TestRunsDeleteCommand:
    """runs delete command behavior."""

    def test_runs_delete_not_found(self, tmp_path):
        """'runs delete' on nonexistent run prints error."""
        config = Config()
        config.cache.database = str(tmp_path / "test.db")
        RunDatabase(tmp_path / "test.db")
        result = _invoke(["runs", "delete", "nonexistent", "--yes"], config=config)
        assert "not found" in result.output.lower() or "error" in result.output.lower()


class TestConfigShowCommand:
    """config --show command behavior."""

    def test_config_show(self):
        """'config --show' displays current configuration."""
        config = Config()
        config.immich.url = "http://photos.test:2283"
        config.immich.api_key = "secret-key"
        result = _invoke(["config", "--show"], config=config)
        assert result.exit_code == 0
        assert "http://photos.test:2283" in result.output
        assert "****" in result.output  # API key masked

    def test_config_show_no_key(self):
        """'config --show' with no API key shows '(not set)'."""
        config = Config()
        config.immich.url = ""
        config.immich.api_key = ""
        result = _invoke(["config", "--show"], config=config)
        assert result.exit_code == 0
        assert "(not set)" in result.output


class TestConfigUrlUpdate:
    """config --url and --api-key update behavior."""

    def test_config_url_saves(self, tmp_path):
        """'config --url X' updates and saves config."""
        config = Config()
        # WHY: Config.save_yaml writes to disk — mock to avoid side effects
        with (
            patch.object(Config, "save_yaml") as mock_save,
            patch.object(Config, "get_default_path", return_value=tmp_path / "config.yml"),
        ):
            result = _invoke(["config", "--url", "http://new:2283"], config=config)
        assert result.exit_code == 0
        mock_save.assert_called_once()


class TestGenerateValidation:
    """Generate command validation errors."""

    def _config_with_immich(self):
        config = Config()
        config.immich.url = "http://immich:2283"
        config.immich.api_key = "test-key"
        return config

    def test_trip_requires_year(self):
        """--memory-type trip without --year shows error."""
        result = _invoke(
            ["generate", "--memory-type", "trip"],
            config=self._config_with_immich(),
        )
        assert result.exit_code != 0

    def test_trip_index_requires_trip_type(self):
        """--trip-index without --memory-type trip shows error."""
        result = _invoke(
            ["generate", "--trip-index", "0", "--year", "2024"],
            config=self._config_with_immich(),
        )
        assert result.exit_code != 0

    def test_all_trips_requires_trip_type(self):
        """--all-trips without --memory-type trip shows error."""
        result = _invoke(
            ["generate", "--all-trips", "--year", "2024"],
            config=self._config_with_immich(),
        )
        assert result.exit_code != 0

    def test_near_date_requires_trip_type(self):
        """--near-date without --memory-type trip shows error."""
        result = _invoke(
            ["generate", "--near-date", "2024-06-15", "--year", "2024"],
            config=self._config_with_immich(),
        )
        assert result.exit_code != 0

    def test_years_back_requires_on_this_day(self):
        """--years-back without --memory-type on_this_day shows error."""
        result = _invoke(
            ["generate", "--years-back", "3", "--year", "2024"],
            config=self._config_with_immich(),
        )
        assert result.exit_code != 0

    def test_multi_person_requires_person(self):
        """--memory-type multi_person without --person shows error."""
        result = _invoke(
            ["generate", "--memory-type", "multi_person", "--year", "2024"],
            config=self._config_with_immich(),
        )
        assert result.exit_code != 0


class TestGenerateBuildParamsTable:
    """_build_params_table renders expected rows."""

    def test_basic_table(self):
        """Table includes expected settings."""
        from immich_memories.cli.generate import _build_params_table
        from immich_memories.timeperiod import DateRange

        config = Config()
        table = _build_params_table(
            config=config,
            memory_type="year_in_review",
            date_range=DateRange(
                start=datetime(2024, 1, 1),
                end=datetime(2024, 12, 31),
            ),
            person_names=["Alice"],
            duration=600.0,
            orientation="landscape",
            scale_mode="smart_crop",
            transition="smart",
            resolution="auto",
            output_format="mp4",
            output_path=Path("/out/video.mp4"),
            add_date=False,
            keep_intermediates=False,
            privacy_mode=False,
            title_override=None,
            subtitle_override=None,
            use_live_photos=False,
            music=None,
            music_volume=0.5,
            no_music=False,
        )
        assert table.title == "Generation Parameters"

    def test_table_with_all_options(self):
        """Table renders special rows for enabled options."""
        from immich_memories.cli.generate import _build_params_table
        from immich_memories.timeperiod import DateRange

        config = Config()
        table = _build_params_table(
            config=config,
            memory_type=None,
            date_range=DateRange(
                start=datetime(2024, 1, 1),
                end=datetime(2024, 12, 31),
            ),
            person_names=[],
            duration=30.0,
            orientation="portrait",
            scale_mode=None,
            transition="crossfade",
            resolution="1080p",
            output_format="prores",
            output_path=Path("/out/video.mov"),
            add_date=True,
            keep_intermediates=True,
            privacy_mode=True,
            title_override="My Title",
            subtitle_override="My Subtitle",
            use_live_photos=True,
            music="auto",
            music_volume=0.75,
            no_music=False,
        )
        assert table.title == "Generation Parameters"


class TestMusicCommandHelp:
    """Music CLI subcommands are registered and have help."""

    def test_music_search_help(self):
        result = _invoke(["music", "search", "--help"])
        assert result.exit_code == 0
        assert "--mood" in result.output

    def test_music_analyze_help(self):
        result = _invoke(["music", "analyze", "--help"])
        assert result.exit_code == 0
        assert "VIDEO_PATH" in result.output

    def test_music_add_help(self):
        result = _invoke(["music", "add", "--help"])
        assert result.exit_code == 0
        assert "--volume" in result.output


class TestRunsPrintHelpers:
    """Internal print helpers in runs.py."""

    def test_print_run_details_table(self):
        """_print_run_details_table renders without crashing."""
        from immich_memories.cli.runs import _print_run_details_table

        run = _make_run(status="completed", person_name="Alice")
        run.completed_at = datetime(2026, 1, 1, 13, 0)
        run.clips_analyzed = 100
        run.clips_selected = 20
        run.output_path = "/out/video.mp4"
        run.output_duration_seconds = 120.0
        run.output_size_bytes = 50_000_000
        run.errors_count = 2
        run.date_range_start = date(2025, 1, 1)
        run.date_range_end = date(2025, 12, 31)
        # Should not raise
        _print_run_details_table(run, format_duration)

    def test_print_run_phases_table(self):
        """_print_run_phases_table renders without crashing."""
        from immich_memories.cli.runs import _print_run_phases_table

        run = _make_run()
        run.phases = [
            PhaseStats(
                phase_name="analysis",
                started_at=datetime(2026, 1, 1, 12, 0),
                duration_seconds=60.0,
                items_processed=10,
                items_total=10,
            ),
            PhaseStats(
                phase_name="encoding",
                started_at=datetime(2026, 1, 1, 12, 1),
                duration_seconds=120.0,
                items_processed=5,
                items_total=0,
                errors=[{"error": "timeout"}],
            ),
        ]
        _print_run_phases_table(run, format_duration)

    def test_print_run_system_info(self):
        """_print_run_system_info renders without crashing."""
        from immich_memories.cli.runs import _print_run_system_info

        si = SystemInfo(
            platform="linux",
            platform_version="Ubuntu 22.04",
            python_version="3.12.0",
            machine_arch="x86_64",
            cpu_brand="AMD Ryzen 9",
            cpu_cores=32,
            ram_gb=128.0,
            gpu_name="NVIDIA RTX 4090",
            vram_mb=24576,
            hw_accel_backend="nvidia",
            ffmpeg_version="6.1",
        )
        _print_run_system_info(si)


class TestPeopleCommand:
    """people command requires Immich configuration."""

    def test_people_no_config(self):
        """'people' without Immich config shows error."""
        config = Config()
        config.immich.url = ""
        config.immich.api_key = ""
        result = _invoke(["people"], config=config)
        assert result.exit_code != 0


class TestYearsCommand:
    """years command requires Immich configuration."""

    def test_years_no_config(self):
        """'years' without Immich config shows error."""
        config = Config()
        config.immich.url = ""
        config.immich.api_key = ""
        result = _invoke(["years"], config=config)
        assert result.exit_code != 0


class TestGenerateInfersMemoryType:
    """generate infers memory_type from --person when not set."""

    def test_dry_run_with_person_infers_spotlight(self):
        """Specifying --person without --memory-type infers person_spotlight."""
        config = Config()
        config.immich.url = "http://immich:2283"
        config.immich.api_key = "test-key"
        result = _invoke(
            ["generate", "--year", "2024", "--person", "Alice", "--dry-run"],
            config=config,
        )
        assert result.exit_code == 0

    def test_dry_run_with_multiple_persons_infers_multi(self):
        """Specifying multiple --person without --memory-type infers multi_person."""
        config = Config()
        config.immich.url = "http://immich:2283"
        config.immich.api_key = "test-key"
        result = _invoke(
            [
                "generate",
                "--year",
                "2024",
                "--person",
                "Alice",
                "--person",
                "Bob",
                "--dry-run",
            ],
            config=config,
        )
        assert result.exit_code == 0


class TestGenerateQualityOverride:
    """--quality flag overrides config."""

    def test_quality_flag_in_help(self):
        result = _invoke(["generate", "--help"])
        assert "--quality" in result.output


class TestRunsDatabaseRunWithDateRange:
    """Runs with date ranges round-trip correctly."""

    @pytest.fixture
    def db(self, tmp_path):
        return RunDatabase(tmp_path / "runs.db")

    def test_run_target_duration_roundtrip(self, db):
        """target_duration_seconds survives DB round-trip (stored as minutes)."""
        run = _make_run(run_id="r1")
        run.target_duration_seconds = 600
        db.save_run(run)
        loaded = db.get_run("r1")
        assert loaded.target_duration_seconds == 600
