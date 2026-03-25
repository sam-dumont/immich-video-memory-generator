"""Unit tests for pipeline progress tracking, ETA calculation, and formatting."""

from __future__ import annotations

from unittest.mock import patch

from immich_memories.analysis.progress import (
    MAX_COMPLETED_HISTORY,
    MAX_ERROR_HISTORY,
    CompletedItem,
    PipelinePhase,
    PipelineProgress,
    ProgressTracker,
)

# ---------------------------------------------------------------------------
# PipelinePhase enum
# ---------------------------------------------------------------------------


class TestPipelinePhase:
    def test_all_phases_have_labels(self) -> None:
        for phase in PipelinePhase:
            assert isinstance(phase.label, str)
            assert len(phase.label) > 0

    def test_phase_ordering(self) -> None:
        assert PipelinePhase.NOT_STARTED.value == 0
        assert PipelinePhase.CLUSTERING.value == 1
        assert PipelinePhase.FILTERING.value == 2
        assert PipelinePhase.ANALYZING.value == 3
        assert PipelinePhase.REFINING.value == 4
        assert PipelinePhase.COMPLETE.value == 5

    def test_specific_labels(self) -> None:
        assert PipelinePhase.NOT_STARTED.label == "Not Started"
        assert PipelinePhase.COMPLETE.label == "Complete"
        assert PipelinePhase.ANALYZING.label == "Analyzing Selected Clips"


# ---------------------------------------------------------------------------
# PipelineProgress dataclass
# ---------------------------------------------------------------------------


class TestPipelineProgress:
    def test_progress_fraction_zero_when_no_items(self) -> None:
        p = PipelineProgress(total_items=0, current_index=0)
        assert p.progress_fraction == 0.0

    def test_progress_fraction_calculated(self) -> None:
        p = PipelineProgress(total_items=10, current_index=3)
        assert p.progress_fraction == 0.3

    def test_progress_fraction_capped_at_one(self) -> None:
        p = PipelineProgress(total_items=5, current_index=10)
        assert p.progress_fraction == 1.0

    def test_progress_fraction_exact_completion(self) -> None:
        p = PipelineProgress(total_items=4, current_index=4)
        assert p.progress_fraction == 1.0

    def test_elapsed_seconds_zero_when_not_started(self) -> None:
        p = PipelineProgress(start_time=None)
        assert p.elapsed_seconds == 0.0

    @patch("immich_memories.analysis.progress.time")
    def test_elapsed_seconds_positive_when_running(self, mock_time: object) -> None:
        mock_time.time.return_value = 1000.0  # type: ignore[union-attr]
        p = PipelineProgress(start_time=990.0)
        assert p.elapsed_seconds == 10.0

    @patch("immich_memories.analysis.progress.time")
    def test_phase_elapsed_seconds_zero_when_no_phase(self, mock_time: object) -> None:
        p = PipelineProgress(phase_start_time=None)
        assert p.phase_elapsed_seconds == 0.0

    @patch("immich_memories.analysis.progress.time")
    def test_phase_elapsed_seconds_positive(self, mock_time: object) -> None:
        mock_time.time.return_value = 500.0  # type: ignore[union-attr]
        p = PipelineProgress(phase_start_time=495.0)
        assert p.phase_elapsed_seconds == 5.0


# ---------------------------------------------------------------------------
# ProgressTracker — Phase/Item lifecycle
# ---------------------------------------------------------------------------


class TestProgressTrackerLifecycle:
    @patch("immich_memories.analysis.progress.time")
    def test_start_resets_state(self, mock_time: object) -> None:
        mock_time.time.return_value = 100.0  # type: ignore[union-attr]
        tracker = ProgressTracker()
        # Mutate state first
        tracker.progress.phase = PipelinePhase.ANALYZING
        tracker.progress.current_item = "something"
        tracker.start()
        assert tracker.progress.phase == PipelinePhase.NOT_STARTED
        assert tracker.progress.current_item is None
        assert tracker.progress.start_time == 100.0

    @patch("immich_memories.analysis.progress.time")
    def test_start_phase_sets_fields(self, mock_time: object) -> None:
        mock_time.time.return_value = 200.0  # type: ignore[union-attr]
        tracker = ProgressTracker()
        tracker.start_phase(PipelinePhase.CLUSTERING, total_items=15)
        assert tracker.progress.phase == PipelinePhase.CLUSTERING
        assert tracker.progress.total_items == 15
        assert tracker.progress.current_index == 0
        assert tracker.progress.current_item is None
        assert tracker.progress.phase_start_time == 200.0

    @patch("immich_memories.analysis.progress.time")
    def test_start_item_sets_current(self, mock_time: object) -> None:
        mock_time.time.return_value = 300.0  # type: ignore[union-attr]
        tracker = ProgressTracker()
        tracker.start_item("clip_42.mp4", asset_id="abc-123")
        assert tracker.progress.current_item == "clip_42.mp4"
        assert tracker.progress.current_asset_id == "abc-123"
        assert tracker._item_start_time == 300.0

    @patch("immich_memories.analysis.progress.time")
    def test_complete_item_success(self, mock_time: object) -> None:
        mock_time.time.return_value = 400.0  # type: ignore[union-attr]
        tracker = ProgressTracker()
        tracker.start_phase(PipelinePhase.ANALYZING, total_items=5)
        mock_time.time.return_value = 401.0  # type: ignore[union-attr]
        tracker.start_item("vid1")
        mock_time.time.return_value = 404.0  # type: ignore[union-attr]
        tracker.complete_item("vid1")

        assert len(tracker.progress.completed) == 1
        assert tracker.progress.completed[0].item_id == "vid1"
        assert tracker.progress.completed[0].duration_seconds == 3.0
        assert tracker.progress.completed[0].success is True
        assert tracker.progress.current_index == 1
        assert tracker.progress.current_item is None
        assert tracker.progress.current_asset_id is None

    @patch("immich_memories.analysis.progress.time")
    def test_complete_item_failure(self, mock_time: object) -> None:
        mock_time.time.return_value = 500.0  # type: ignore[union-attr]
        tracker = ProgressTracker()
        tracker.start_phase(PipelinePhase.ANALYZING, total_items=5)
        tracker.start_item("bad_vid")
        mock_time.time.return_value = 502.0  # type: ignore[union-attr]
        tracker.complete_item("bad_vid", success=False, error="codec error")

        assert len(tracker.progress.errors) == 1
        assert tracker.progress.errors[0].error == "codec error"
        assert tracker.progress.errors[0].success is False
        # Failures still increment the index
        assert tracker.progress.current_index == 1

    def test_complete_phase_clears_current_item(self) -> None:
        tracker = ProgressTracker()
        tracker.progress.current_item = "in_progress"
        tracker.complete_phase()
        assert tracker.progress.current_item is None
        assert tracker._item_start_time is None

    def test_finish_sets_complete(self) -> None:
        tracker = ProgressTracker()
        tracker.progress.current_item = "leftover"
        tracker.finish()
        assert tracker.progress.phase == PipelinePhase.COMPLETE
        assert tracker.progress.current_item is None


# ---------------------------------------------------------------------------
# ProgressTracker — ETA calculation
# ---------------------------------------------------------------------------


class TestProgressTrackerETA:
    def test_eta_no_completed_uses_initial_estimate(self) -> None:
        tracker = ProgressTracker(initial_estimate_seconds=10.0)
        tracker.progress.total_items = 8
        tracker.progress.current_index = 0
        eta = tracker.get_eta_seconds()
        assert eta == 80.0  # 8 remaining * 10s

    def test_eta_one_completed_uses_initial_estimate(self) -> None:
        """With fewer than 2 items, initial estimate is used."""
        tracker = ProgressTracker(initial_estimate_seconds=20.0)
        tracker.progress.total_items = 5
        tracker.progress.current_index = 1
        tracker.progress.completed = [
            CompletedItem(item_id="a", duration_seconds=5.0),
        ]
        eta = tracker.get_eta_seconds()
        # 4 remaining * 20s initial estimate (not enough history)
        assert eta == 80.0

    def test_eta_with_rolling_average(self) -> None:
        tracker = ProgressTracker(rolling_window=3, initial_estimate_seconds=100.0)
        tracker.progress.total_items = 10
        tracker.progress.current_index = 5
        tracker.progress.completed = [
            CompletedItem(item_id="a", duration_seconds=6.0),
            CompletedItem(item_id="b", duration_seconds=9.0),
            CompletedItem(item_id="c", duration_seconds=12.0),
        ]
        eta = tracker.get_eta_seconds()
        # avg = (6+9+12)/3 = 9.0, remaining = 5
        assert eta == 45.0

    def test_eta_rolling_window_limits_history(self) -> None:
        tracker = ProgressTracker(rolling_window=2)
        tracker.progress.total_items = 10
        tracker.progress.current_index = 5
        tracker.progress.completed = [
            CompletedItem(item_id="old", duration_seconds=100.0),
            CompletedItem(item_id="a", duration_seconds=4.0),
            CompletedItem(item_id="b", duration_seconds=6.0),
        ]
        eta = tracker.get_eta_seconds()
        # Window=2, uses last 2: avg = (4+6)/2 = 5.0, remaining = 5
        assert eta == 25.0

    def test_eta_zero_remaining(self) -> None:
        tracker = ProgressTracker()
        tracker.progress.total_items = 5
        tracker.progress.current_index = 5
        assert tracker.get_eta_seconds() == 0.0

    def test_eta_with_explicit_remaining(self) -> None:
        tracker = ProgressTracker(initial_estimate_seconds=15.0)
        eta = tracker.get_eta_seconds(remaining_count=3)
        assert eta == 45.0

    def test_eta_negative_remaining_returns_zero(self) -> None:
        tracker = ProgressTracker()
        assert tracker.get_eta_seconds(remaining_count=-1) == 0.0

    def test_eta_excludes_failed_items_from_average(self) -> None:
        tracker = ProgressTracker(rolling_window=5, initial_estimate_seconds=50.0)
        tracker.progress.total_items = 10
        tracker.progress.current_index = 4
        tracker.progress.completed = [
            CompletedItem(item_id="ok1", duration_seconds=8.0, success=True),
            CompletedItem(item_id="fail", duration_seconds=1.0, success=False),
            CompletedItem(item_id="ok2", duration_seconds=12.0, success=True),
        ]
        eta = tracker.get_eta_seconds()
        # Only 2 successful → avg = (8+12)/2 = 10.0, remaining = 6
        assert eta == 60.0

    def test_get_average_duration_no_history(self) -> None:
        tracker = ProgressTracker(initial_estimate_seconds=25.0)
        assert tracker.get_average_duration() == 25.0

    def test_get_average_duration_with_history(self) -> None:
        tracker = ProgressTracker(rolling_window=3)
        tracker.progress.completed = [
            CompletedItem(item_id="a", duration_seconds=3.0),
            CompletedItem(item_id="b", duration_seconds=6.0),
            CompletedItem(item_id="c", duration_seconds=9.0),
        ]
        assert tracker.get_average_duration() == 6.0


# ---------------------------------------------------------------------------
# ProgressTracker — History limits
# ---------------------------------------------------------------------------


class TestProgressTrackerHistoryLimits:
    @patch("immich_memories.analysis.progress.time")
    def test_completed_capped_at_max(self, mock_time: object) -> None:
        mock_time.time.return_value = 1.0  # type: ignore[union-attr]
        tracker = ProgressTracker()
        tracker.start_phase(PipelinePhase.ANALYZING, total_items=200)

        for i in range(MAX_COMPLETED_HISTORY + 20):
            mock_time.time.return_value = float(i * 2)  # type: ignore[union-attr]
            tracker.start_item(f"item_{i}")
            mock_time.time.return_value = float(i * 2 + 1)  # type: ignore[union-attr]
            tracker.complete_item(f"item_{i}")

        assert len(tracker.progress.completed) == MAX_COMPLETED_HISTORY
        # Should keep the most recent items
        assert tracker.progress.completed[-1].item_id == f"item_{MAX_COMPLETED_HISTORY + 19}"

    @patch("immich_memories.analysis.progress.time")
    def test_errors_capped_at_max(self, mock_time: object) -> None:
        mock_time.time.return_value = 1.0  # type: ignore[union-attr]
        tracker = ProgressTracker()
        tracker.start_phase(PipelinePhase.ANALYZING, total_items=200)

        for i in range(MAX_ERROR_HISTORY + 10):
            mock_time.time.return_value = float(i * 2)  # type: ignore[union-attr]
            tracker.start_item(f"err_{i}")
            mock_time.time.return_value = float(i * 2 + 1)  # type: ignore[union-attr]
            tracker.complete_item(f"err_{i}", success=False, error=f"fail {i}")

        assert len(tracker.progress.errors) == MAX_ERROR_HISTORY
        assert tracker.progress.errors[-1].item_id == f"err_{MAX_ERROR_HISTORY + 9}"


# ---------------------------------------------------------------------------
# ProgressTracker — Speed ratio
# ---------------------------------------------------------------------------


class TestProgressTrackerSpeedRatio:
    def test_speed_ratio_no_processing_time(self) -> None:
        tracker = ProgressTracker()
        assert tracker.get_speed_ratio() == 0.0

    def test_speed_ratio_with_data(self) -> None:
        tracker = ProgressTracker()
        tracker.progress.total_video_duration = 120.0
        tracker.progress.total_processing_time = 60.0
        assert tracker.get_speed_ratio() == 2.0

    @patch("immich_memories.analysis.progress.time")
    def test_speed_ratio_accumulated_via_complete_item(self, mock_time: object) -> None:
        mock_time.time.return_value = 0.0  # type: ignore[union-attr]
        tracker = ProgressTracker()
        tracker.start_phase(PipelinePhase.ANALYZING, total_items=2)

        # Item 1: 30s video processed in 10s
        mock_time.time.return_value = 10.0  # type: ignore[union-attr]
        tracker.start_item("v1")
        mock_time.time.return_value = 20.0  # type: ignore[union-attr]
        tracker.complete_item("v1", video_duration=30.0)

        # Item 2: 60s video processed in 20s
        mock_time.time.return_value = 20.0  # type: ignore[union-attr]
        tracker.start_item("v2")
        mock_time.time.return_value = 40.0  # type: ignore[union-attr]
        tracker.complete_item("v2", video_duration=60.0)

        # total_video = 90, total_processing = 30 → ratio = 3.0
        assert tracker.progress.total_video_duration == 90.0
        assert tracker.progress.total_processing_time == 30.0
        assert tracker.get_speed_ratio() == 3.0

    @patch("immich_memories.analysis.progress.time")
    def test_speed_ratio_skips_zero_duration_videos(self, mock_time: object) -> None:
        mock_time.time.return_value = 0.0  # type: ignore[union-attr]
        tracker = ProgressTracker()
        tracker.start_phase(PipelinePhase.ANALYZING, total_items=1)
        mock_time.time.return_value = 1.0  # type: ignore[union-attr]
        tracker.start_item("v1")
        mock_time.time.return_value = 2.0  # type: ignore[union-attr]
        tracker.complete_item("v1", video_duration=0.0)

        assert tracker.progress.total_video_duration == 0.0
        assert tracker.progress.total_processing_time == 0.0


# ---------------------------------------------------------------------------
# ProgressTracker — Formatting
# ---------------------------------------------------------------------------


class TestProgressTrackerFormatting:
    def test_format_eta_seconds_only(self) -> None:
        tracker = ProgressTracker()
        assert tracker.format_eta(30) == "30s"

    def test_format_eta_minutes_and_seconds(self) -> None:
        tracker = ProgressTracker()
        assert tracker.format_eta(90) == "1m 30s"

    def test_format_eta_exact_minutes(self) -> None:
        tracker = ProgressTracker()
        assert tracker.format_eta(60) == "1m"

    def test_format_eta_hours_only(self) -> None:
        tracker = ProgressTracker()
        assert tracker.format_eta(3600) == "1h"

    def test_format_eta_hours_and_minutes(self) -> None:
        tracker = ProgressTracker()
        assert tracker.format_eta(3660) == "1h 1m"

    def test_format_eta_zero(self) -> None:
        tracker = ProgressTracker()
        assert tracker.format_eta(0) == "0s"

    def test_format_eta_sub_minute_boundary(self) -> None:
        tracker = ProgressTracker()
        assert tracker.format_eta(59) == "59s"

    def test_format_eta_multi_hour(self) -> None:
        tracker = ProgressTracker()
        assert tracker.format_eta(7320) == "2h 2m"

    @patch("immich_memories.analysis.progress.time")
    def test_format_elapsed_delegates_to_format_eta(self, mock_time: object) -> None:
        mock_time.time.return_value = 200.0  # type: ignore[union-attr]
        tracker = ProgressTracker()
        tracker.progress.start_time = 110.0
        result = tracker.format_elapsed()
        assert result == "1m 30s"


# ---------------------------------------------------------------------------
# ProgressTracker — Callbacks
# ---------------------------------------------------------------------------


class TestProgressTrackerCallbacks:
    @patch("immich_memories.analysis.progress.time")
    def test_callback_called_on_start(self, mock_time: object) -> None:
        mock_time.time.return_value = 1.0  # type: ignore[union-attr]
        tracker = ProgressTracker()
        calls: list[PipelineProgress] = []
        tracker.add_callback(calls.append)
        tracker.start()
        assert len(calls) == 1
        assert calls[0].start_time == 1.0

    @patch("immich_memories.analysis.progress.time")
    def test_callback_called_on_start_phase(self, mock_time: object) -> None:
        mock_time.time.return_value = 1.0  # type: ignore[union-attr]
        tracker = ProgressTracker()
        calls: list[PipelineProgress] = []
        tracker.add_callback(calls.append)
        tracker.start_phase(PipelinePhase.FILTERING, total_items=3)
        assert len(calls) == 1
        assert calls[0].phase == PipelinePhase.FILTERING

    @patch("immich_memories.analysis.progress.time")
    def test_callback_called_on_start_item(self, mock_time: object) -> None:
        mock_time.time.return_value = 1.0  # type: ignore[union-attr]
        tracker = ProgressTracker()
        calls: list[PipelineProgress] = []
        tracker.add_callback(calls.append)
        tracker.start_item("clip.mp4")
        assert len(calls) == 1
        assert calls[0].current_item == "clip.mp4"

    @patch("immich_memories.analysis.progress.time")
    def test_callback_called_on_complete_item(self, mock_time: object) -> None:
        mock_time.time.return_value = 1.0  # type: ignore[union-attr]
        tracker = ProgressTracker()
        calls: list[PipelineProgress] = []
        tracker.add_callback(calls.append)
        tracker.start_item("clip.mp4")
        calls.clear()
        tracker.complete_item("clip.mp4")
        assert len(calls) == 1

    def test_callback_called_on_finish(self) -> None:
        tracker = ProgressTracker()
        calls: list[PipelineProgress] = []
        tracker.add_callback(calls.append)
        tracker.finish()
        assert len(calls) == 1
        assert calls[0].phase == PipelinePhase.COMPLETE

    @patch("immich_memories.analysis.progress.time")
    def test_failing_callback_suppressed(self, mock_time: object) -> None:
        mock_time.time.return_value = 1.0  # type: ignore[union-attr]

        def bad_callback(_: PipelineProgress) -> None:
            msg = "boom"
            raise RuntimeError(msg)

        tracker = ProgressTracker()
        tracker.add_callback(bad_callback)
        # Should not raise
        tracker.start()
        tracker.start_phase(PipelinePhase.CLUSTERING, total_items=1)
        tracker.start_item("x")
        tracker.complete_item("x")
        tracker.finish()

    @patch("immich_memories.analysis.progress.time")
    def test_remove_callback_stops_notifications(self, mock_time: object) -> None:
        mock_time.time.return_value = 1.0  # type: ignore[union-attr]
        tracker = ProgressTracker()
        calls: list[PipelineProgress] = []
        tracker.add_callback(calls.append)
        tracker.start()
        assert len(calls) == 1

        tracker.remove_callback(calls.append)
        tracker.finish()
        # No new calls after removal
        assert len(calls) == 1

    def test_remove_nonexistent_callback_no_error(self) -> None:
        tracker = ProgressTracker()

        def noop(_: PipelineProgress) -> None:
            pass

        # Should not raise even though noop was never added
        tracker.remove_callback(noop)

    @patch("immich_memories.analysis.progress.time")
    def test_multiple_callbacks_all_called(self, mock_time: object) -> None:
        mock_time.time.return_value = 1.0  # type: ignore[union-attr]
        tracker = ProgressTracker()
        calls_a: list[PipelineProgress] = []
        calls_b: list[PipelineProgress] = []
        tracker.add_callback(calls_a.append)
        tracker.add_callback(calls_b.append)
        tracker.start()
        assert len(calls_a) == 1
        assert len(calls_b) == 1


# ---------------------------------------------------------------------------
# ProgressTracker — Display fields
# ---------------------------------------------------------------------------


class TestProgressTrackerDisplayFields:
    @patch("immich_memories.analysis.progress.time")
    def test_complete_item_with_display_data(self, mock_time: object) -> None:
        mock_time.time.return_value = 1.0  # type: ignore[union-attr]
        tracker = ProgressTracker()
        tracker.start_phase(PipelinePhase.ANALYZING, total_items=5)
        tracker.start_item("vid1", asset_id="asset-1")
        mock_time.time.return_value = 2.0  # type: ignore[union-attr]
        tracker.complete_item(
            "vid1",
            preview_path="/tmp/preview.mp4",
            segment=(1.5, 4.5),
            score=0.85,
            llm_description="Dog playing in snow",
            llm_emotion="joyful",
            llm_interestingness=0.9,
            llm_quality=0.8,
            audio_categories=["speech", "laughter"],
        )

        p = tracker.progress
        assert p.last_completed_asset_id == "vid1"
        assert p.last_completed_segment == (1.5, 4.5)
        assert p.last_completed_score == 0.85
        assert p.last_completed_video_path == "/tmp/preview.mp4"
        assert p.last_completed_llm_description == "Dog playing in snow"
        assert p.last_completed_llm_emotion == "joyful"
        assert p.last_completed_llm_interestingness == 0.9
        assert p.last_completed_llm_quality == 0.8
        assert p.last_completed_audio_categories == ["speech", "laughter"]

    @patch("immich_memories.analysis.progress.time")
    def test_complete_item_without_display_data(self, mock_time: object) -> None:
        mock_time.time.return_value = 1.0  # type: ignore[union-attr]
        tracker = ProgressTracker()
        tracker.start_phase(PipelinePhase.ANALYZING, total_items=5)
        tracker.start_item("vid1")
        mock_time.time.return_value = 2.0  # type: ignore[union-attr]
        tracker.complete_item("vid1")

        p = tracker.progress
        # No display fields should be set
        assert p.last_completed_asset_id is None
        assert p.last_completed_segment is None
        assert p.last_completed_score is None
        assert p.last_completed_video_path is None
        assert p.last_completed_llm_description is None

    @patch("immich_memories.analysis.progress.time")
    def test_start_item_does_not_clear_last_completed(self, mock_time: object) -> None:
        """Preview should stay visible while the next item processes."""
        mock_time.time.return_value = 1.0  # type: ignore[union-attr]
        tracker = ProgressTracker()
        tracker.start_phase(PipelinePhase.ANALYZING, total_items=5)

        # Complete first item with display data
        tracker.start_item("vid1")
        mock_time.time.return_value = 2.0  # type: ignore[union-attr]
        tracker.complete_item("vid1", preview_path="/tmp/p1.mp4", score=0.7)

        # Start next item — last_completed fields should persist
        mock_time.time.return_value = 3.0  # type: ignore[union-attr]
        tracker.start_item("vid2")
        assert tracker.progress.last_completed_asset_id == "vid1"
        assert tracker.progress.last_completed_video_path == "/tmp/p1.mp4"
        assert tracker.progress.last_completed_score == 0.7

    @patch("immich_memories.analysis.progress.time")
    def test_partial_display_data_clears_unprovided_fields(self, mock_time: object) -> None:
        """When a new clip has display data, unprovided LLM fields reset to None."""
        mock_time.time.return_value = 1.0  # type: ignore[union-attr]
        tracker = ProgressTracker()
        tracker.start_phase(PipelinePhase.ANALYZING, total_items=5)
        tracker.start_item("vid1")
        mock_time.time.return_value = 2.0  # type: ignore[union-attr]
        tracker.complete_item("vid1", preview_path="/tmp/p.mp4")

        p = tracker.progress
        assert p.last_completed_asset_id == "vid1"
        assert p.last_completed_video_path == "/tmp/p.mp4"
        # LLM fields not provided → should be None
        assert p.last_completed_llm_description is None
        assert p.last_completed_llm_emotion is None

    @patch("immich_memories.analysis.progress.time")
    def test_llm_description_does_not_leak_between_clips(self, mock_time: object) -> None:
        """Regression test for #121: stale LLM description from clip A must not
        persist when clip B completes without LLM data."""
        mock_time.time.return_value = 1.0  # type: ignore[union-attr]
        tracker = ProgressTracker()
        tracker.start_phase(PipelinePhase.ANALYZING, total_items=3)

        # Clip A: has full LLM analysis
        tracker.start_item("clip_a", asset_id="asset-a")
        mock_time.time.return_value = 2.0  # type: ignore[union-attr]
        tracker.complete_item(
            "clip_a",
            preview_path="/tmp/a.mp4",
            llm_description="A young child in a snowy yard",
            llm_emotion="joyful",
            llm_interestingness=0.9,
            llm_quality=0.8,
            audio_categories=["speech"],
        )
        assert tracker.progress.last_completed_llm_description == "A young child in a snowy yard"

        # Clip B: no LLM analysis, but has audio categories (triggers display update)
        mock_time.time.return_value = 3.0  # type: ignore[union-attr]
        tracker.start_item("clip_b", asset_id="asset-b")
        mock_time.time.return_value = 4.0  # type: ignore[union-attr]
        tracker.complete_item(
            "clip_b",
            audio_categories=["laughter"],
        )

        p = tracker.progress
        # Asset ID must update to clip B
        assert p.last_completed_asset_id == "clip_b"
        # LLM fields must NOT leak from clip A — they should be None
        assert p.last_completed_llm_description is None
        assert p.last_completed_llm_emotion is None
        assert p.last_completed_llm_interestingness is None
        assert p.last_completed_llm_quality is None
        # Audio categories should reflect clip B
        assert p.last_completed_audio_categories == ["laughter"]
        # Preview path should be cleared (clip B has no preview)
        assert p.last_completed_video_path is None


# ---------------------------------------------------------------------------
# ProgressTracker — Status summary
# ---------------------------------------------------------------------------


class TestProgressTrackerStatusSummary:
    @patch("immich_memories.analysis.progress.time")
    def test_get_status_summary_keys(self, mock_time: object) -> None:
        mock_time.time.return_value = 100.0  # type: ignore[union-attr]
        tracker = ProgressTracker(total_phases=4)
        tracker.start()
        tracker.start_phase(PipelinePhase.ANALYZING, total_items=10)
        summary = tracker.get_status_summary()

        expected_keys = {
            "phase",
            "phase_label",
            "phase_number",
            "total_phases",
            "current_item",
            "current_asset_id",
            "current_index",
            "total_items",
            "progress_fraction",
            "elapsed",
            "elapsed_seconds",
            "eta",
            "eta_seconds",
            "avg_duration",
            "speed_ratio",
            "completed_count",
            "error_count",
            "errors",
            "last_completed_asset_id",
            "last_completed_segment",
            "last_completed_score",
            "last_completed_video_path",
            "last_completed_llm_description",
            "last_completed_llm_emotion",
            "last_completed_llm_interestingness",
            "last_completed_llm_quality",
            "last_completed_audio_categories",
        }
        assert set(summary.keys()) == expected_keys

    @patch("immich_memories.analysis.progress.time")
    def test_get_status_summary_values(self, mock_time: object) -> None:
        mock_time.time.return_value = 100.0  # type: ignore[union-attr]
        tracker = ProgressTracker(total_phases=4, initial_estimate_seconds=10.0)
        tracker.start()
        mock_time.time.return_value = 100.0  # type: ignore[union-attr]
        tracker.start_phase(PipelinePhase.ANALYZING, total_items=10)

        mock_time.time.return_value = 101.0  # type: ignore[union-attr]
        tracker.start_item("clip_1", asset_id="a1")
        mock_time.time.return_value = 105.0  # type: ignore[union-attr]
        tracker.complete_item("clip_1", preview_path="/tmp/c1.mp4")

        mock_time.time.return_value = 130.0  # type: ignore[union-attr]
        summary = tracker.get_status_summary()

        assert summary["phase"] == PipelinePhase.ANALYZING
        assert summary["phase_label"] == "Analyzing Selected Clips"
        assert summary["phase_number"] == 3
        assert summary["total_phases"] == 4
        assert summary["current_item"] is None  # completed, no new start_item
        assert summary["current_index"] == 1
        assert summary["total_items"] == 10
        assert summary["progress_fraction"] == 0.1
        assert summary["completed_count"] == 1
        assert summary["error_count"] == 0
        assert summary["errors"] == []
        assert summary["last_completed_asset_id"] == "clip_1"
        assert summary["last_completed_video_path"] == "/tmp/c1.mp4"
        # elapsed = 130 - 100 = 30s
        assert summary["elapsed_seconds"] == 30.0
        assert summary["elapsed"] == "30s"

    @patch("immich_memories.analysis.progress.time")
    def test_status_summary_errors_format(self, mock_time: object) -> None:
        mock_time.time.return_value = 1.0  # type: ignore[union-attr]
        tracker = ProgressTracker()
        tracker.start_phase(PipelinePhase.ANALYZING, total_items=5)
        tracker.start_item("bad1")
        mock_time.time.return_value = 2.0  # type: ignore[union-attr]
        tracker.complete_item("bad1", success=False, error="corrupt")

        summary = tracker.get_status_summary()
        assert summary["error_count"] == 1
        assert summary["errors"] == [{"id": "bad1", "error": "corrupt"}]


# ---------------------------------------------------------------------------
# CompletedItem dataclass
# ---------------------------------------------------------------------------


class TestCompletedItem:
    def test_defaults(self) -> None:
        item = CompletedItem(item_id="x", duration_seconds=1.5)
        assert item.success is True
        assert item.error is None

    def test_error_item(self) -> None:
        item = CompletedItem(item_id="y", duration_seconds=0.5, success=False, error="timeout")
        assert item.success is False
        assert item.error == "timeout"
