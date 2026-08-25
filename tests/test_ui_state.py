"""Tests for UI state management and helper functions."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from immich_memories.api.compatibility import ApiVersionPolicy
from immich_memories.config_loader import Config
from immich_memories.memory_types.factory import create_preset
from immich_memories.memory_types.registry import MemoryType
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

    def test_duration_defaults_to_auto(self):
        state = AppState()

        assert state.duration_mode == "auto"

    def test_fractional_minutes_preserve_exact_target_seconds(self):
        state = AppState(target_duration=2.5)

        assert state.target_duration_seconds == 150.0

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

    def test_reset_clips_clears_the_preliminary_timeline_plan(self):
        state = AppState()
        state.timeline_plan = MagicMock()

        state.reset_clips()

        assert state.timeline_plan is None


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


class TestScaleModeOptions:
    """The wizard must offer the same scale modes, and the same default, as the CLI."""

    def test_default_label_is_the_configured_mode(self):
        from immich_memories.ui.pages.step3_options import (
            SCALE_MODE_OPTIONS,
            resolve_scale_mode_label,
        )

        assert SCALE_MODE_OPTIONS[resolve_scale_mode_label(None)] == Config().defaults.scale_mode

    def test_default_label_follows_a_configured_override(self):
        from immich_memories.ui.pages.step3_options import (
            SCALE_MODE_OPTIONS,
            resolve_scale_mode_label,
        )

        config = Config()
        config.defaults.scale_mode = "fit"

        assert SCALE_MODE_OPTIONS[resolve_scale_mode_label(config)] == "fit"

    def test_offered_labels_cover_exactly_the_configurable_modes(self):
        """No option the config rejects, and no configurable mode the wizard cannot show."""
        from typing import get_args

        from immich_memories.config_models_render import DefaultsConfig
        from immich_memories.ui.pages.step3_options import SCALE_MODE_OPTIONS

        configurable = set(get_args(DefaultsConfig.model_fields["scale_mode"].annotation))

        assert set(SCALE_MODE_OPTIONS.values()) == configurable

    def test_retired_stored_label_falls_back_to_the_configured_mode(self):
        """A wizard state saved when 'Smart Crop (keep faces)' existed must still render."""
        from immich_memories.ui.pages.step3_options import (
            SCALE_MODE_OPTIONS,
            resolve_scale_mode_label,
        )

        label = resolve_scale_mode_label(None, "Smart Crop (keep faces)")

        assert SCALE_MODE_OPTIONS[label] == Config().defaults.scale_mode


def test_ui_output_options_include_h265() -> None:
    from immich_memories.ui.pages.step3_options import OUTPUT_FORMAT_OPTIONS

    assert "MP4 (H.265)" in OUTPUT_FORMAT_OPTIONS


def test_ui_output_label_is_initialized_from_config() -> None:
    from immich_memories.ui.pages.step3_options import configured_output_format_label

    config = Config()
    config.output.codec = "h265"

    assert configured_output_format_label(config) == "MP4 (H.265)"


@pytest.mark.parametrize(
    ("codec", "expected_label"),
    [("h264", "MOV (H.264)"), ("h265", "MOV (H.265)")],
)
def test_ui_output_label_includes_the_configured_mov_container(
    codec: str, expected_label: str
) -> None:
    from immich_memories.ui.pages.step3_options import (
        OUTPUT_FORMAT_OPTIONS,
        configured_output_format_label,
    )

    config = Config()
    config.output.codec = codec
    config.output.format = "mov"

    assert configured_output_format_label(config) == expected_label
    assert expected_label in OUTPUT_FORMAT_OPTIONS


def test_ui_explicit_h265_mov_choice_preserves_both_dimensions() -> None:
    from immich_memories.processing.encoding_plan import OutputCodec
    from immich_memories.ui.pages._step4_generate import resolve_ui_output_selection

    state = AppState(
        config=Config(),
        generation_options={"format_override": "MOV (H.265)"},
    )

    selection = resolve_ui_output_selection(state)

    assert selection.codec is OutputCodec.H265
    assert selection.container == "mov"


def test_step2_status_consumes_shared_phase_event_message() -> None:
    from immich_memories.operations.phases import OperationalPhase, PhaseEvent
    from immich_memories.ui.pages.step2_loading import _set_phase_status

    label = MagicMock()
    event = PhaseEvent(OperationalPhase.DOWNLOAD, 2, 5, "Loading thumbnails 2/5", 1.0)

    _set_phase_status(label, event)

    label.set_text.assert_called_once_with("Loading thumbnails 2/5")


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


def test_generation_factory_preserves_ui_music_and_delivery_boundaries(
    tmp_path,
) -> None:
    """UI keeps music deferred while preserving the requested delivery intent."""
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

    assert params.no_music is True
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

    def test_default_analysis_depth_is_auto(self):
        state = AppState()
        assert state.analysis_depth == "auto"

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


class TestPersonScope:
    """Who a memory is about, as the one list every fetch reads."""

    def test_a_group_of_one_is_still_a_filter(self) -> None:
        """A Multi-Person memory naming one person used to fetch everybody.

        That card writes its picks to memory_preset_params and never touches
        selected_person, so a lone id read as "no people named" turned a memory
        of Alice into a memory of the whole window.
        """
        state = AppState()
        state.memory_preset_params = {"person_ids": ["person-alice"]}

        assert state.person_ids == ["person-alice"]

    def test_a_single_pick_reads_as_a_list_of_one(self) -> None:
        from immich_memories.api.models import Person

        state = AppState()
        state.selected_person = Person(id="person-alice", name="Alice")

        assert state.person_ids == ["person-alice"]

    def test_a_memory_about_nobody_names_nobody(self) -> None:
        assert AppState().person_ids == []


class TestAdoptingAPreset:
    """A preset is the one thing the wizard reads a memory's scope from.

    ``apply_preset`` is where a card's answer becomes state, so the person
    filter it carries has to reach the fetch the same way its windows do --
    that is what makes the wizard and ``--person`` agree (#666, #683).
    """

    def _state_with(self, *people):
        from immich_memories.api.models import Person

        state = AppState()
        state.people = [Person(id=f"person-{n.lower()}", name=n) for n in people]
        return state

    def test_a_year_in_review_narrows_to_everyone_the_filter_names(self) -> None:
        """The wizard could not phrase this at all before #666."""
        state = self._state_with("Alice", "Bob")

        state.apply_preset(
            create_preset(MemoryType.YEAR_IN_REVIEW, year=2024, person_names=["Alice", "Bob"])
        )

        assert state.person_ids == ["person-alice", "person-bob"]

    def test_naming_nobody_leaves_the_memory_wide(self) -> None:
        state = self._state_with("Alice", "Bob")

        state.apply_preset(create_preset(MemoryType.YEAR_IN_REVIEW, year=2024))

        assert state.person_ids == []

    def test_a_name_immich_does_not_know_narrows_nothing(self) -> None:
        """A filter is written in names and fetched by ids, and only ids exist.

        The roster is what Immich returned; a name absent from it has no id to
        query with, so it cannot silently become "everybody".
        """
        state = self._state_with("Alice")

        state.apply_preset(
            create_preset(MemoryType.YEAR_IN_REVIEW, year=2024, person_names=["Alice", "Mallory"])
        )

        assert state.person_ids == ["person-alice"]

    def test_one_person_is_named_for_the_title(self) -> None:
        state = self._state_with("Alice", "Bob")

        state.apply_preset(
            create_preset(MemoryType.PERSON_SPOTLIGHT, year=2024, person_names=["Alice"])
        )

        assert state.selected_person is not None
        assert state.selected_person.name == "Alice"

    def test_a_group_has_no_single_name_to_put_on_the_title(self) -> None:
        state = self._state_with("Alice", "Bob")
        state.apply_preset(
            create_preset(MemoryType.PERSON_SPOTLIGHT, year=2024, person_names=["Alice"])
        )

        state.apply_preset(
            create_preset(MemoryType.MULTI_PERSON, year=2024, person_names=["Alice", "Bob"])
        )

        assert state.selected_person is None

    def test_the_preset_also_brings_its_windows_and_its_length(self) -> None:
        state = self._state_with("Alice")
        preset = create_preset(MemoryType.MONTHLY_HIGHLIGHTS, year=2024, month=3)

        state.apply_preset(preset)

        assert state.date_ranges == preset.date_ranges
        assert state.target_duration == 1.0


class TestChoosingAMemoryType:
    """Switching cards drops what the previous card collected."""

    def test_the_person_does_not_follow_you_to_the_next_card(self) -> None:
        """Alice picked for a Person Spotlight must not narrow a Year in Review.

        Every card shows a person widget now, so a person left behind by the
        previous one would filter the new memory with a picker on screen
        claiming nobody is named.
        """
        from immich_memories.api.models import Person

        state = AppState()
        state.selected_person = Person(id="person-alice", name="Alice")
        state.memory_preset_params = {"person_id": "person-alice", "year": 2024}

        state.choose_memory_type("year_in_review")

        assert state.person_ids == []
        assert state.memory_preset_params == {}

    def test_switching_away_from_an_album_drops_it(self) -> None:
        """A left-over album would otherwise satisfy the step 1 scope check."""
        state = AppState()
        state.choose_memory_type("album")
        state.album_id, state.album_name = "album-1", "Holiday snaps"

        state.choose_memory_type("season")

        assert state.album_id is None

    def test_choosing_the_album_card_keeps_the_album_selectable(self) -> None:
        state = AppState()
        state.album_id, state.album_name = "album-1", "Holiday snaps"

        state.choose_memory_type("album")

        assert state.album_id == "album-1"
