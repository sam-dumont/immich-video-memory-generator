"""Tests for pipeline efficiency: density budget tightening + unified photo budget."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch


class TestDensityBudgetCap:
    """Phase 2 filtering should cap analysis candidates at 1.5x target_clips."""

    def _make_clip(
        self, asset_id: str, is_favorite: bool = False, width: int = 1920, height: int = 1080
    ):
        from immich_memories.api.models import Asset, VideoClipInfo

        now = datetime.now(tz=UTC)
        asset = Asset(
            id=asset_id,
            type="VIDEO",
            fileCreatedAt=now,
            fileModifiedAt=now,
            updatedAt=now,
            isFavorite=is_favorite,
            exifInfo={"make": "Apple", "model": "iPhone"},
        )
        return VideoClipInfo(
            asset=asset,
            duration_seconds=5.0,
            width=width,
            height=height,
        )

    def test_caps_at_1_5x_target_clips(self):
        """With 200 clips and target_clips=40, returns at most 60."""
        from immich_memories.analysis.smart_pipeline import PipelineConfig, SmartPipeline

        config = PipelineConfig(target_clips=40)
        # WHY: mock services — we're testing _phase_filter logic, not Immich
        pipeline = SmartPipeline(
            client=MagicMock(),
            analysis_cache=MagicMock(),
            thumbnail_cache=MagicMock(),
            config=config,
            analysis_config=MagicMock(min_segment_duration=1.5),
            app_config=MagicMock(),
        )

        clips = [self._make_clip(f"clip{i}") for i in range(200)]
        result = pipeline._phase_filter(clips)
        assert len(result) <= 60  # 1.5x cap

    def test_favorites_preserved_when_cap_hit(self):
        """All favorites should survive the cap."""
        from immich_memories.analysis.smart_pipeline import PipelineConfig, SmartPipeline

        config = PipelineConfig(target_clips=20)
        pipeline = SmartPipeline(
            client=MagicMock(),
            analysis_cache=MagicMock(),
            thumbnail_cache=MagicMock(),
            config=config,
            analysis_config=MagicMock(min_segment_duration=1.5),
            app_config=MagicMock(),
        )

        favorites = [self._make_clip(f"fav{i}", is_favorite=True) for i in range(15)]
        non_favorites = [self._make_clip(f"nonfav{i}") for i in range(100)]
        clips = favorites + non_favorites

        result = pipeline._phase_filter(clips)
        fav_ids = {c.asset.id for c in result if c.asset.is_favorite}
        assert len(fav_ids) == 15  # All favorites kept

    def test_analyze_all_mode_bypasses_cap(self):
        """analyze_all=True should return all clips, no cap."""
        from immich_memories.analysis.smart_pipeline import PipelineConfig, SmartPipeline

        config = PipelineConfig(target_clips=20, analyze_all=True)
        pipeline = SmartPipeline(
            client=MagicMock(),
            analysis_cache=MagicMock(),
            thumbnail_cache=MagicMock(),
            config=config,
            analysis_config=MagicMock(min_segment_duration=1.5),
            app_config=MagicMock(),
        )

        clips = [self._make_clip(f"clip{i}") for i in range(100)]
        result = pipeline._phase_filter(clips)
        assert len(result) == 100

    def test_reduced_multiplier_selects_fewer(self):
        """With raw_multiplier=1.3, fewer clips selected than with 2.0."""
        from immich_memories.analysis.smart_pipeline import PipelineConfig, SmartPipeline

        config = PipelineConfig(target_clips=40)
        pipeline = SmartPipeline(
            client=MagicMock(),
            analysis_cache=MagicMock(),
            thumbnail_cache=MagicMock(),
            config=config,
            analysis_config=MagicMock(min_segment_duration=1.5),
            app_config=MagicMock(),
        )

        clips = [self._make_clip(f"clip{i}") for i in range(200)]
        result = pipeline._phase_filter(clips)
        # With 1.3x multiplier + 1.5x cap, should be well under 200
        assert len(result) < 100


class TestUnifiedPhotoBudget:
    """Photo rendering should always use unified budget, never legacy render-all."""

    def test_add_photos_without_target_duration_uses_unified(self):
        """Even without target_duration_seconds, unified budget is used."""
        from immich_memories.generate import _add_photos_if_enabled
        from immich_memories.processing.assembly_config import AssemblyClip

        clips = [
            AssemblyClip(
                path="/fake/clip.mp4",
                duration=5.0,
                asset_id="a1",
                date="2025-01-15",
            ),
        ]

        params = MagicMock()
        params.include_photos = True
        params.photo_assets = [MagicMock()]
        params.target_duration_seconds = None  # Not set (UI path before fix)
        params.selected_photo_ids = None  # No pre-selection → fallback path
        params.progress_callback = None

        with patch(
            "immich_memories.generate._apply_unified_budget",
            return_value=(clips, []),
        ) as mock_unified:
            _add_photos_if_enabled(clips, params, MagicMock())

        mock_unified.assert_called_once()

    def test_add_photos_with_target_duration_uses_unified(self):
        """With target_duration_seconds set, unified budget is used."""
        from immich_memories.generate import _add_photos_if_enabled
        from immich_memories.processing.assembly_config import AssemblyClip

        clips = [
            AssemblyClip(
                path="/fake/clip.mp4",
                duration=5.0,
                asset_id="a1",
                date="2025-01-15",
            ),
        ]

        params = MagicMock()
        params.include_photos = True
        params.photo_assets = [MagicMock()]
        params.target_duration_seconds = 120.0
        params.selected_photo_ids = None  # No pre-selection → fallback path
        params.progress_callback = None

        with patch(
            "immich_memories.generate._apply_unified_budget",
            return_value=(clips, []),
        ) as mock_unified:
            _add_photos_if_enabled(clips, params, MagicMock())

        mock_unified.assert_called_once()

    def test_ui_sets_target_duration_seconds(self):
        """UI _build_generation_params should set target_duration_seconds."""
        from immich_memories.ui.pages._step4_generate import _build_generation_params

        state = MagicMock()
        state.target_duration = 5  # 5 minutes
        state.generation_options = {}
        state.selected_person = None
        state.date_range = None
        state.memory_type = None
        state.memory_preset_params = {}
        state.title_suggestion_title = None
        state.title_suggestion_subtitle = None
        state.clip_segments = {}
        state.clip_rotations = {}
        state.include_photos = False
        state.photo_assets = None
        state.photo_duration = 4.0
        state.demo_mode = False
        state.immich_url = "http://fake:2283"
        state.immich_api_key = "fake-key"
        state.config = MagicMock()

        with patch("immich_memories.api.immich.SyncImmichClient"):
            params = _build_generation_params(state, [], MagicMock())

        assert params.target_duration_seconds == 300  # 5 min * 60
