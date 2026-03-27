"""Tests for score_and_select_photos() — extracted photo scoring + budget selection."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from immich_memories.analysis.unified_budget import BudgetCandidate
from immich_memories.api.models import Asset
from immich_memories.config_models import PhotoConfig
from immich_memories.photos.photo_pipeline import (
    PhotoSelectionResult,
    score_and_select_photos,
)


def _make_asset(asset_id: str, favorite: bool = False) -> Asset:
    now = datetime.now(tz=UTC)
    return Asset(
        id=asset_id,
        type="IMAGE",
        fileCreatedAt=now,
        fileModifiedAt=now,
        updatedAt=now,
        isFavorite=favorite,
    )


def _make_video_candidate(
    asset_id: str, duration: float, date: datetime | None = None
) -> BudgetCandidate:
    return BudgetCandidate(
        asset_id=asset_id,
        duration=duration,
        score=0.5,
        candidate_type="video",
        date=date or datetime(2025, 7, 15, tzinfo=UTC),
    )


class TestScoreAndSelectPhotos:
    """Extracted scoring + budget selection returns scored photos and selected IDs."""

    def test_returns_scored_photos_and_selection(self, tmp_path):
        assets = [_make_asset("p1", favorite=True), _make_asset("p2")]
        video_candidates = [_make_video_candidate("v1", 10.0)]

        config = MagicMock()
        config.photos = PhotoConfig()
        config.photos.duration = 4.0
        config.photos.max_ratio = 0.50

        # WHY: mock download_fn — testing scoring+selection logic, not Immich I/O
        download_fn = MagicMock()

        result = score_and_select_photos(
            photo_assets=assets,
            video_candidates=video_candidates,
            config=config,
            target_duration=30.0,
            work_dir=tmp_path,
            download_fn=download_fn,
        )

        assert isinstance(result, PhotoSelectionResult)
        assert len(result.scored_photos) == 2
        # All scored photos are (Asset, float) tuples
        assert all(isinstance(s, float) for _, s in result.scored_photos)
        # Selection has selected_photo_ids
        assert len(result.selection.selected_photo_ids) > 0

    def test_empty_photos_returns_empty_result(self, tmp_path):
        config = MagicMock()
        config.photos = PhotoConfig()
        config.photos.duration = 4.0
        config.photos.max_ratio = 0.50

        result = score_and_select_photos(
            photo_assets=[],
            video_candidates=[_make_video_candidate("v1", 10.0)],
            config=config,
            target_duration=30.0,
            work_dir=tmp_path,
            download_fn=MagicMock(),
        )

        assert result.scored_photos == []
        assert result.selection.selected_photo_ids == []

    def test_budget_respects_target_duration(self, tmp_path):
        assets = [_make_asset(f"p{i}") for i in range(20)]
        # 10 videos using 50s of a 60s budget → 10s left for photos
        video_candidates = [_make_video_candidate(f"v{i}", 5.0) for i in range(10)]

        config = MagicMock()
        config.photos = PhotoConfig()
        config.photos.duration = 4.0
        config.photos.max_ratio = 0.50
        config.title_screens.enabled = False

        # WHY: mock download_fn — testing budget math, not Immich I/O
        download_fn = MagicMock()

        result = score_and_select_photos(
            photo_assets=assets,
            video_candidates=video_candidates,
            config=config,
            target_duration=60.0,
            work_dir=tmp_path,
            download_fn=download_fn,
        )

        # Total photo duration should fit within remaining budget
        total_photo_duration = len(result.selection.selected_photo_ids) * 4.0
        assert total_photo_duration <= 60.0


class TestPreSelectionShortCircuit:
    """_add_photos_if_enabled() skips scoring when selected_photo_ids is set."""

    def test_pre_selected_skips_budget(self):
        """When selected_photo_ids is set, skip _apply_unified_budget entirely."""
        from immich_memories.generate import GenerationParams, _add_photos_if_enabled
        from immich_memories.processing.assembly_config import AssemblyClip

        video_clip = AssemblyClip(
            path=MagicMock(),
            duration=10.0,
            date="2025-07-15T12:00:00",
            asset_id="v1",
        )

        asset_p1 = _make_asset("p1", favorite=True)
        asset_p2 = _make_asset("p2")

        config = MagicMock()
        config.photos = PhotoConfig()
        config.photos.duration = 4.0
        config.output.resolution_tuple = (1920, 1080)

        params = GenerationParams(
            clips=[MagicMock(width=1920, height=1080)],
            output_path=MagicMock(),
            config=config,
            client=MagicMock(),
            photo_assets=[asset_p1, asset_p2],
            include_photos=True,
            target_duration_seconds=30.0,
            selected_photo_ids={"p1"},
        )
        params.client.download_asset = MagicMock()
        params.client.get_asset_thumbnail = MagicMock()

        rendered_clip = AssemblyClip(
            path=MagicMock(),
            duration=4.0,
            date="2025-07-15T12:00:00",
            asset_id="p1",
            is_photo=True,
        )

        # WHY: mock render_photo_clips — testing pre-selection logic, not FFmpeg
        with (
            patch(
                "immich_memories.photos.photo_pipeline.render_photo_clips",
                return_value=[rendered_clip],
            ) as mock_render,
            patch(
                "immich_memories.generate._apply_unified_budget",
            ) as mock_budget,
        ):
            _add_photos_if_enabled([video_clip], params, MagicMock())

        # Budget should NOT be called — pre-selected path
        mock_budget.assert_not_called()
        # render_photo_clips should be called with only asset p1
        assert mock_render.call_count == 1
        rendered_assets = mock_render.call_args.kwargs["assets"]
        assert len(rendered_assets) == 1
        assert rendered_assets[0].id == "p1"

    def test_no_pre_selection_falls_through(self):
        """When selected_photo_ids is None, use existing _apply_unified_budget path."""
        from immich_memories.generate import GenerationParams, _add_photos_if_enabled
        from immich_memories.processing.assembly_config import AssemblyClip

        video_clip = AssemblyClip(
            path=MagicMock(),
            duration=10.0,
            date="2025-07-15T12:00:00",
            asset_id="v1",
        )

        config = MagicMock()
        config.photos = PhotoConfig()
        config.photos.duration = 4.0

        params = GenerationParams(
            clips=[MagicMock(width=1920, height=1080)],
            output_path=MagicMock(),
            config=config,
            client=MagicMock(),
            photo_assets=[_make_asset("p1")],
            include_photos=True,
            target_duration_seconds=30.0,
            selected_photo_ids=None,
        )

        # WHY: mock _apply_unified_budget — testing routing logic, not budget math
        with patch(
            "immich_memories.generate._apply_unified_budget",
            return_value=([video_clip], []),
        ) as mock_budget:
            _add_photos_if_enabled([video_clip], params, MagicMock())

        mock_budget.assert_called_once()


class TestAppStatePhotoFields:
    """AppState has photo scoring/selection fields."""

    def test_state_has_photo_selection_fields(self):
        from immich_memories.ui.state import AppState

        state = AppState()
        assert state.scored_photos == []
        assert state.selected_photo_ids == set()
        assert state.photo_budget_result is None

    def test_reset_clips_clears_photo_selection(self):
        from immich_memories.ui.state import AppState

        state = AppState()
        state.scored_photos = [("fake_asset", 0.8)]
        state.selected_photo_ids = {"p1", "p2"}
        state.photo_budget_result = "fake_result"

        state.reset_clips()

        assert state.scored_photos == []
        assert state.selected_photo_ids == set()
        assert state.photo_budget_result is None


class TestStep4PassesPreSelectedPhotos:
    """Step 4 passes selected_photo_ids from state to GenerationParams."""

    def test_build_generation_params_includes_selected_photo_ids(self):
        state = MagicMock()
        state.generation_options = {}
        state.selected_person = None
        state.date_range = None
        state.include_photos = True
        state.photo_assets = [_make_asset("p1")]
        state.photo_duration = 4.0
        state.config = MagicMock()
        state.config.photos.duration = 4.0
        state.immich_url = "http://localhost:2283"
        state.immich_api_key = "test-key"
        state.demo_mode = False
        state.memory_type = None
        state.memory_preset_params = {}
        state.title_suggestion_title = None
        state.title_suggestion_subtitle = None
        state.clip_segments = {}
        state.clip_rotations = {}
        state.target_duration = 10
        state.selected_photo_ids = {"p1"}

        with patch("immich_memories.api.immich.SyncImmichClient"):
            from immich_memories.ui.pages._step4_generate import _build_generation_params

            params = _build_generation_params(state, [], MagicMock())

        assert params.selected_photo_ids == {"p1"}

    def test_build_generation_params_none_when_empty_selection(self):
        state = MagicMock()
        state.generation_options = {}
        state.selected_person = None
        state.date_range = None
        state.include_photos = True
        state.photo_assets = [_make_asset("p1")]
        state.photo_duration = 4.0
        state.config = MagicMock()
        state.config.photos.duration = 4.0
        state.immich_url = "http://localhost:2283"
        state.immich_api_key = "test-key"
        state.demo_mode = False
        state.memory_type = None
        state.memory_preset_params = {}
        state.title_suggestion_title = None
        state.title_suggestion_subtitle = None
        state.clip_segments = {}
        state.clip_rotations = {}
        state.target_duration = 10
        state.selected_photo_ids = set()

        with patch("immich_memories.api.immich.SyncImmichClient"):
            from immich_memories.ui.pages._step4_generate import _build_generation_params

            params = _build_generation_params(state, [], MagicMock())

        assert params.selected_photo_ids is None


class TestEndToEndPhotoFlow:
    """Full flow: score → store on state → pass to generation → skip re-scoring."""

    def test_full_ui_photo_flow(self, tmp_path):
        from immich_memories.generate import GenerationParams

        assets = [_make_asset("p1", favorite=True), _make_asset("p2"), _make_asset("p3")]
        video_candidates = [_make_video_candidate("v1", 10.0), _make_video_candidate("v2", 8.0)]

        config = MagicMock()
        config.photos = PhotoConfig()
        config.photos.duration = 4.0
        config.photos.max_ratio = 0.50
        config.title_screens.enabled = False

        result = score_and_select_photos(
            photo_assets=assets,
            video_candidates=video_candidates,
            config=config,
            target_duration=60.0,
            work_dir=tmp_path,
            download_fn=MagicMock(),
        )

        assert len(result.scored_photos) > 0
        selected_ids = set(result.selection.selected_photo_ids)
        assert len(selected_ids) > 0

        config.output = MagicMock()
        config.output.resolution_tuple = (1920, 1080)
        params = GenerationParams(
            clips=[MagicMock(width=1920, height=1080)],
            output_path=tmp_path / "output.mp4",
            config=config,
            include_photos=True,
            photo_assets=assets,
            target_duration_seconds=60.0,
            selected_photo_ids=selected_ids,
        )

        assert params.selected_photo_ids == selected_ids
        assert params.selected_photo_ids is not None
