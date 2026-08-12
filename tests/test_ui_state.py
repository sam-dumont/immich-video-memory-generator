"""Tests for UI state management and helper functions."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from immich_memories.api.compatibility import ApiVersionPolicy
from immich_memories.config_loader import Config
from immich_memories.tracking import DeliveryStatus
from immich_memories.ui.state import (
    AppState,
    _sessions,
    ensure_config,
    get_app_state,
    reset_app_state,
)
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

    def test_default_immich_api_version_is_auto(self):
        """UI clients default to automatic Immich compatibility detection."""
        state = AppState()
        assert state.immich_api_version is ApiVersionPolicy.AUTO

    def test_default_generation_outcome_is_typed_and_empty(self):
        """A new UI session cannot imply a delivery request or stale warning."""
        state = AppState()
        assert state.generation_warning is None
        assert state.delivery_status is DeliveryStatus.NOT_REQUESTED

    def test_ensure_config_loads_explicit_immich_api_version(self):
        """UI clients retain an explicit compatibility policy from configuration."""
        state = AppState()
        config = Config(
            immich={
                "url": "https://immich.example.com",
                "api_key": "test-key",
                "api_version": "v2",
            }
        )

        with patch("immich_memories.config_loader.get_config", return_value=config):
            ensure_config(state)

        assert state.immich_api_version is ApiVersionPolicy.V2


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

    def test_selected_photo_ids_starts_empty(self):
        state = AppState()
        assert state.selected_photo_ids == set()

    def test_reset_clips_clears_selected_photo_ids(self):
        state = AppState()
        state.selected_photo_ids = {"p1", "p2"}
        state.reset_clips()
        assert state.selected_photo_ids == set()


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


def test_ui_output_options_include_h265() -> None:
    from immich_memories.ui.pages.step3_options import OUTPUT_FORMAT_OPTIONS

    assert "MP4 (H.265)" in OUTPUT_FORMAT_OPTIONS


def test_ui_output_label_is_initialized_from_config() -> None:
    from immich_memories.ui.pages.step3_options import configured_output_format_label

    config = Config()
    config.output.codec = "h265"

    assert configured_output_format_label(config) == "MP4 (H.265)"


def test_ui_output_label_includes_configured_h264_mov_container() -> None:
    from immich_memories.ui.pages.step3_options import (
        OUTPUT_FORMAT_OPTIONS,
        configured_output_format_label,
    )

    config = Config()
    config.output.codec = "h264"
    config.output.format = "mov"

    assert configured_output_format_label(config) == "MOV (H.264)"
    assert "MOV (H.264)" in OUTPUT_FORMAT_OPTIONS


def test_ui_explicit_h265_mov_choice_preserves_codec_and_container() -> None:
    from immich_memories.processing.encoding_plan import OutputCodec
    from immich_memories.ui.pages._step4_generate import resolve_ui_output_selection
    from immich_memories.ui.pages.step3_options import OUTPUT_FORMAT_OPTIONS

    state = AppState(
        config=Config(),
        generation_options={"format_override": "MOV (H.265)"},
    )

    selection = resolve_ui_output_selection(state)

    assert selection.codec is OutputCodec.H265
    assert selection.container == "mov"
    assert "MOV (H.265)" in OUTPUT_FORMAT_OPTIONS


def test_ui_explicit_h264_mov_choice_preserves_codec_and_container() -> None:
    from immich_memories.processing.encoding_plan import OutputCodec
    from immich_memories.ui.pages._step4_generate import resolve_ui_output_selection

    state = AppState(
        config=Config(),
        generation_options={"format_override": "MOV (H.264)"},
    )

    selection = resolve_ui_output_selection(state)

    assert selection.codec is OutputCodec.H264
    assert selection.container == "mov"


def test_generation_factory_passes_state_api_version_to_client(tmp_path) -> None:
    from immich_memories.ui.pages._step4_generate import _build_generation_params

    state = AppState(
        config=Config(),
        immich_url="https://immich.example.com",
        immich_api_key="test-api-key",
        immich_api_version=ApiVersionPolicy.V2,
    )

    with patch("immich_memories.api.immich.SyncImmichClient") as client_factory:
        _build_generation_params(state, [], tmp_path / "memory.mp4")

    client_factory.assert_called_once_with(
        base_url="https://immich.example.com",
        api_key="test-api-key",
        api_version=ApiVersionPolicy.V2,
    )


def test_generation_factory_preserves_configured_h265_when_ui_untouched(tmp_path) -> None:
    from immich_memories.ui.pages._step4_generate import _build_generation_params

    config = Config()
    config.output.codec = "h265"
    state = AppState(
        config=config,
        generation_options={},
        immich_url="https://immich.example.com",
        immich_api_key="test-api-key",
    )

    with patch("immich_memories.api.immich.SyncImmichClient"):
        params = _build_generation_params(state, [], tmp_path / "memory.mp4")

    assert params.output_format is None


def test_generation_factory_maps_explicit_ui_h265_override(tmp_path) -> None:
    from immich_memories.ui.pages._step4_generate import _build_generation_params

    state = AppState(
        config=Config(),
        generation_options={"format_override": "MP4 (H.265)"},
        immich_url="https://immich.example.com",
        immich_api_key="test-api-key",
    )

    with patch("immich_memories.api.immich.SyncImmichClient"):
        params = _build_generation_params(state, [], tmp_path / "memory.mp4")

    assert params.output_format == "h265"


def test_generation_factory_preserves_ui_delivery_request_for_deferred_finalization(
    tmp_path,
) -> None:
    """The prepared artifact records upload intent and the original UI album."""
    from immich_memories.ui.pages._step4_generate import _build_generation_params

    state = AppState(
        config=Config(),
        generation_options={},
        immich_url="https://immich.example.com",
        immich_api_key="test-api-key",
        upload_enabled=True,
        upload_album_name="Album At Click Time",
    )

    with patch("immich_memories.api.immich.SyncImmichClient"):
        params = _build_generation_params(state, [], tmp_path / "memory.mp4")

    assert params.upload_enabled is True
    assert params.upload_album == "Album At Click Time"


def test_config_initialized_ui_label_is_not_an_explicit_override(tmp_path) -> None:
    from immich_memories.ui.pages._step4_generate import _build_generation_params

    config = Config()
    config.output.codec = "h265"
    state = AppState(
        config=config,
        generation_options={"format": "MP4 (H.265)"},
        immich_url="https://immich.example.com",
        immich_api_key="test-api-key",
    )

    with patch("immich_memories.api.immich.SyncImmichClient"):
        params = _build_generation_params(state, [], tmp_path / "memory.mp4")

    assert params.output_format is None


def test_ui_prores_output_path_uses_resolved_mov_container(tmp_path) -> None:
    from immich_memories.ui.pages._step4_generate import normalize_ui_output_path

    config = Config()
    config.output.codec = "prores"
    config.output.format = "mov"
    state = AppState(config=config, generation_options={})

    assert normalize_ui_output_path(state, tmp_path / "memory.mp4").suffix == ".mov"


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
