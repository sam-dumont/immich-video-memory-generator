"""Tests for UI state management and helper functions."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from immich_memories.ui.state import AppState, _sessions, get_app_state, reset_app_state
from tests.conftest import make_clip


class TestAppStateDefaults:
    """Test AppState default values."""

    def test_default_step(self):
        """Default step is 1."""
        state = AppState()
        assert state.step == 1

    def test_default_config_not_saved(self):
        """Config is not saved by default."""
        state = AppState()
        assert not state.config_saved

    def test_default_empty_clips(self):
        """Clips list is empty by default."""
        state = AppState()
        assert not state.clips

    def test_default_empty_selected_ids(self):
        """Selected clip IDs is empty by default."""
        state = AppState()
        assert not state.selected_clip_ids

    def test_default_not_processing(self):
        """Not processing by default."""
        state = AppState()
        assert not state.processing

    def test_default_pipeline_not_running(self):
        """Pipeline not running by default."""
        state = AppState()
        assert not state.pipeline_running

    def test_default_time_period_mode(self):
        """Default time period mode is 'year'."""
        state = AppState()
        assert state.time_period_mode == "year"


class TestAppStateResetClips:
    """Test reset_clips() method."""

    def test_reset_clears_clips(self):
        """reset_clips() empties the clips list."""
        state = AppState()
        state.clips = [make_clip("c1"), make_clip("c2")]
        state.reset_clips()
        assert not state.clips

    def test_reset_clears_selected_ids(self):
        """reset_clips() empties selected_clip_ids."""
        state = AppState()
        state.selected_clip_ids = {"id1", "id2"}
        state.reset_clips()
        assert not state.selected_clip_ids

    def test_reset_clears_segments(self):
        """reset_clips() empties clip_segments."""
        state = AppState()
        state.clip_segments = {"id1": (0.0, 5.0)}
        state.reset_clips()
        assert not state.clip_segments

    def test_reset_clears_pipeline_result(self):
        """reset_clips() sets pipeline_result to None."""
        state = AppState()
        state.pipeline_result = {"some": "data"}
        state.reset_clips()
        assert state.pipeline_result is None

    def test_reset_clears_rotations(self):
        """reset_clips() empties clip_rotations."""
        state = AppState()
        state.clip_rotations = {"id1": 90}
        state.reset_clips()
        assert not state.clip_rotations

    def test_reset_clears_title_suggestions(self):
        """reset_clips() clears LLM-generated title fields."""
        state = AppState()
        state.title_suggestion_title = "Summer 2024"
        state.title_suggestion_subtitle = "June - August"
        state.reset_clips()
        assert state.title_suggestion_title is None
        assert state.title_suggestion_subtitle is None


class TestAppStateIncludePhotos:
    """Test include_photos state field."""

    def test_default_include_photos_false(self):
        state = AppState()
        assert not state.include_photos

    def test_photo_assets_starts_empty(self):
        state = AppState()
        assert state.photo_assets == []


class TestAppStateGetSelectedClips:
    """Test get_selected_clips() method."""

    def test_returns_matching_clips(self):
        """get_selected_clips() returns clips whose asset.id is in selected_clip_ids."""
        state = AppState()
        c1 = make_clip("c1")
        c2 = make_clip("c2")
        c3 = make_clip("c3")
        state.clips = [c1, c2, c3]
        state.selected_clip_ids = {"c1", "c3"}

        selected = state.get_selected_clips()
        selected_ids = {c.asset.id for c in selected}
        assert selected_ids == {"c1", "c3"}

    def test_returns_empty_when_none_selected(self):
        """get_selected_clips() returns empty when no IDs selected."""
        state = AppState()
        state.clips = [make_clip("c1")]
        state.selected_clip_ids = set()
        assert not state.get_selected_clips()

    def test_returns_empty_when_no_clips(self):
        """get_selected_clips() returns empty when clips list is empty."""
        state = AppState()
        state.selected_clip_ids = {"c1"}
        assert not state.get_selected_clips()


class TestAppStateSingleton:
    """Test per-session state management."""

    def setup_method(self):
        _sessions.clear()

    def teardown_method(self):
        _sessions.clear()

    def test_get_returns_same_instance(self):
        """get_app_state() returns the same instance on repeated calls."""
        mock_app = MagicMock()
        mock_app.storage.user = {}
        with patch("nicegui.app", mock_app):  # WHY: no real NiceGUI server in unit tests
            reset_app_state()
            s1 = get_app_state()
            s2 = get_app_state()
        assert s1 is s2

    def test_reset_creates_new_instance(self):
        """reset_app_state() creates a fresh AppState."""
        mock_app = MagicMock()
        mock_app.storage.user = {}
        with patch("nicegui.app", mock_app):  # WHY: no real NiceGUI server in unit tests
            s1 = get_app_state()
            s1.step = 3
            s2 = reset_app_state()
        assert s2.step == 1
        assert s1 is not s2


class TestScaleModeMap:
    """Test the scale mode map includes blur."""

    def test_blur_mode_mapped(self):
        from immich_memories.ui.pages._step4_generate import _SCALE_MODE_MAP

        assert "Blur (blurred background)" in _SCALE_MODE_MAP
        assert _SCALE_MODE_MAP["Blur (blurred background)"] == "blur"

    def test_all_four_modes_present(self):
        from immich_memories.ui.pages._step4_generate import _SCALE_MODE_MAP

        assert len(_SCALE_MODE_MAP) == 4


class TestFormatDuration:
    """Test format_duration() helper."""

    def test_zero_seconds(self):
        """0 seconds formats as 0:00."""
        from immich_memories.ui.pages.step2_helpers import format_duration

        assert format_duration(0) == "0:00"

    def test_sixty_five_seconds(self):
        """65 seconds formats as 1:05."""
        from immich_memories.ui.pages.step2_helpers import format_duration

        assert format_duration(65) == "1:05"

    def test_three_minutes(self):
        """180 seconds formats as 3:00."""
        from immich_memories.ui.pages.step2_helpers import format_duration

        assert format_duration(180) == "3:00"

    def test_large_duration(self):
        """3600 seconds formats as 60:00."""
        from immich_memories.ui.pages.step2_helpers import format_duration

        assert format_duration(3600) == "60:00"


class TestAppStatePhotoDuration:
    """Test photo_duration state field."""

    def test_default_photo_duration_is_4(self):
        state = AppState()
        assert state.photo_duration == 4.0

    def test_photo_duration_can_be_set(self):
        state = AppState()
        state.photo_duration = 6.0
        assert state.photo_duration == 6.0


class TestAppStateAnalysisDepth:
    """Test analysis_depth state field."""

    def test_default_analysis_depth_is_fast(self):
        state = AppState()
        assert state.analysis_depth == "fast"

    def test_analysis_depth_can_be_set(self):
        state = AppState()
        state.analysis_depth = "thorough"
        assert state.analysis_depth == "thorough"


class TestAppStateCancelRequested:
    """Test cancel_requested state field."""

    def test_default_cancel_not_requested(self):
        state = AppState()
        assert not state.cancel_requested

    def test_cancel_requested_can_be_set(self):
        state = AppState()
        state.cancel_requested = True
        assert state.cancel_requested

    def test_reset_clips_clears_cancel(self):
        state = AppState()
        state.cancel_requested = True
        state.reset_clips()
        assert not state.cancel_requested


class TestAppStateTransitions:
    """Test state transitions."""

    def test_step_change(self):
        """Step can be changed."""
        state = AppState()
        state.step = 3
        assert state.step == 3

    def test_config_saved_flag(self):
        """config_saved flag can be toggled."""
        state = AppState()
        assert not state.config_saved
        state.config_saved = True
        assert state.config_saved

    def test_pipeline_running_flag(self):
        """pipeline_running flag tracks pipeline state."""
        state = AppState()
        state.pipeline_running = True
        assert state.pipeline_running
        state.pipeline_running = False
        assert not state.pipeline_running


class TestAppStateResetIdempotent:
    """Idempotency and edge cases for state operations."""

    def test_double_reset_is_safe(self):
        """Calling reset_clips() twice in a row is harmless."""
        state = AppState()
        state.clips = [make_clip("c1")]
        state.reset_clips()
        state.reset_clips()
        assert not state.clips

    def test_get_selected_clips_with_stale_ids(self):
        """Selected IDs referencing removed clips return empty."""
        state = AppState()
        state.clips = [make_clip("c1")]
        state.selected_clip_ids = {"c1", "c_gone"}
        selected = state.get_selected_clips()
        assert len(selected) == 1
        assert selected[0].asset.id == "c1"

    def test_reset_preserves_step(self):
        """reset_clips does not change the current step."""
        state = AppState()
        state.step = 3
        state.clips = [make_clip("c1")]
        state.reset_clips()
        assert state.step == 3


class TestFormatDurationEdgeCases:
    """Edge cases for format_duration."""

    def test_negative_seconds(self):
        """Negative seconds still produce a string (no crash)."""
        from immich_memories.ui.pages.step2_helpers import format_duration

        result = format_duration(-1)
        assert isinstance(result, str)

    def test_fractional_seconds(self):
        """Fractional seconds are truncated to whole."""
        from immich_memories.ui.pages.step2_helpers import format_duration

        result = format_duration(61.9)
        assert result == "1:01"
