"""Tests for unified photo+video selection pool.

Verifies:
- SmartPipeline.run_analysis() returns analyzed ClipWithSegments (not PipelineResult)
- SmartPipeline.run_selection() takes analyzed clips and returns PipelineResult
- SmartPipeline.run() still works as before (backward compat)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

from immich_memories.analysis.smart_pipeline import (
    ClipWithSegment,
    PipelineConfig,
    PipelineResult,
    SmartPipeline,
)
from immich_memories.api.models import Asset, AssetType, VideoClipInfo
from immich_memories.config_loader import Config
from immich_memories.config_models import AnalysisConfig


def _make_clip(
    asset_id: str,
    date: datetime,
    *,
    is_favorite: bool = True,
    duration: float = 10.0,
) -> VideoClipInfo:
    asset = Asset(
        id=asset_id,
        type=AssetType.VIDEO,
        fileCreatedAt=date,
        fileModifiedAt=date,
        updatedAt=date,
        isFavorite=is_favorite,
        originalFileName=f"{asset_id}.MOV",
        exifInfo={"make": "Apple", "model": "iPhone 15 Pro"},
    )
    return VideoClipInfo(
        asset=asset,
        width=1920,
        height=1080,
        duration_seconds=duration,
        bitrate=10_000_000,
        codec="hevc",
    )


def _make_clips(count: int) -> list[VideoClipInfo]:
    base = datetime(2024, 1, 15, 12, 0, tzinfo=UTC)
    return [_make_clip(f"clip-{i:03d}", base + timedelta(days=i * 7)) for i in range(count)]


def _make_cached_analysis(asset_id: str, score: float = 0.5) -> MagicMock:
    segment = MagicMock()
    segment.start_time = 0.0
    segment.end_time = 5.0
    segment.total_score = score
    segment.face_score = 0.3
    segment.motion_score = 0.2
    segment.stability_score = 0.4
    segment.llm_description = None
    segment.llm_emotion = None
    segment.audio_categories = None

    analysis = MagicMock()
    analysis.asset_id = asset_id
    analysis.segments = [segment]
    return analysis


def _make_pipeline(
    mock_client: MagicMock,
    mock_cache: MagicMock,
    mock_thumb_cache: MagicMock,
    config: PipelineConfig | None = None,
) -> SmartPipeline:
    return SmartPipeline(
        client=mock_client,
        analysis_cache=mock_cache,
        thumbnail_cache=mock_thumb_cache,
        config=config or PipelineConfig(target_clips=10, avg_clip_duration=5.0),
        analysis_config=AnalysisConfig(),
        app_config=Config(),
    )


def _setup_cache(mock_cache: MagicMock) -> None:
    def get_analysis(asset_id: str, include_segments: bool = True):
        return _make_cached_analysis(asset_id)

    mock_cache.get_analysis.side_effect = get_analysis


class TestRunAnalysis:
    """run_analysis returns analyzed ClipWithSegment list, not PipelineResult."""

    def test_run_analysis_returns_list_of_clip_with_segment(
        self,
        mock_immich_client,
        mock_analysis_cache,
        mock_thumbnail_cache,
    ):
        _setup_cache(mock_analysis_cache)
        pipeline = _make_pipeline(mock_immich_client, mock_analysis_cache, mock_thumbnail_cache)
        clips = _make_clips(10)

        result = pipeline.run_analysis(clips)

        assert isinstance(result, list)
        assert all(isinstance(item, ClipWithSegment) for item in result)
        assert len(result) > 0

    def test_run_analysis_does_not_finish_tracker(
        self,
        mock_immich_client,
        mock_analysis_cache,
        mock_thumbnail_cache,
    ):
        """Tracker should NOT be finished after run_analysis — run_selection does that."""
        _setup_cache(mock_analysis_cache)
        pipeline = _make_pipeline(mock_immich_client, mock_analysis_cache, mock_thumbnail_cache)
        clips = _make_clips(10)

        pipeline.run_analysis(clips)

        # Tracker started but not finished — we can still call run_selection
        assert pipeline.tracker.progress.start_time is not None


class TestRunSelection:
    """run_selection takes analyzed clips and returns PipelineResult."""

    def test_run_selection_returns_pipeline_result(
        self,
        mock_immich_client,
        mock_analysis_cache,
        mock_thumbnail_cache,
    ):
        _setup_cache(mock_analysis_cache)
        pipeline = _make_pipeline(mock_immich_client, mock_analysis_cache, mock_thumbnail_cache)
        clips = _make_clips(10)

        analyzed = pipeline.run_analysis(clips)
        result = pipeline.run_selection(analyzed)

        assert isinstance(result, PipelineResult)
        assert len(result.selected_clips) > 0
        assert isinstance(result.clip_segments, dict)


class TestUnifiedPool:
    """Photos converted to ClipWithSegment merge with videos in run_selection."""

    def test_photos_as_clip_with_segment_pass_through_selection(
        self,
        mock_immich_client,
        mock_analysis_cache,
        mock_thumbnail_cache,
    ):
        """Photo ClipWithSegments mixed with video ones survive run_selection."""
        _setup_cache(mock_analysis_cache)
        pipeline = _make_pipeline(
            mock_immich_client,
            mock_analysis_cache,
            mock_thumbnail_cache,
            PipelineConfig(target_clips=20, avg_clip_duration=5.0),
        )
        clips = _make_clips(10)

        # Analyze videos
        analyzed_videos = pipeline.run_analysis(clips)

        # Create photo ClipWithSegments (as the pipeline runner would)
        base = datetime(2024, 3, 1, 12, 0, tzinfo=UTC)
        photo_candidates: list[ClipWithSegment] = []
        for i in range(5):
            photo_asset = Asset(
                id=f"photo-{i:03d}",
                type=AssetType.IMAGE,
                fileCreatedAt=base + timedelta(days=i * 14),
                fileModifiedAt=base + timedelta(days=i * 14),
                updatedAt=base + timedelta(days=i * 14),
                isFavorite=True,
                originalFileName=f"IMG_{i}.HEIC",
            )
            photo_clip = VideoClipInfo(
                asset=photo_asset,
                duration_seconds=4.0,
                width=4032,
                height=3024,
            )
            photo_candidates.append(
                ClipWithSegment(
                    clip=photo_clip,
                    start_time=0.0,
                    end_time=4.0,
                    score=0.8,
                )
            )

        # Merge and select
        all_candidates = analyzed_videos + photo_candidates
        result = pipeline.run_selection(all_candidates)

        assert isinstance(result, PipelineResult)
        # Some photos should survive selection
        selected_ids = {c.asset.id for c in result.selected_clips}
        photo_ids_in_result = {pid for pid in selected_ids if pid.startswith("photo-")}
        assert len(photo_ids_in_result) > 0, "At least one photo should be selected"

    def test_photo_clips_have_image_asset_type(
        self,
        mock_immich_client,
        mock_analysis_cache,
        mock_thumbnail_cache,
    ):
        """Selected photo clips retain their IMAGE asset type for downstream handling."""
        _setup_cache(mock_analysis_cache)
        pipeline = _make_pipeline(
            mock_immich_client,
            mock_analysis_cache,
            mock_thumbnail_cache,
            PipelineConfig(target_clips=10, avg_clip_duration=5.0),
        )
        clips = _make_clips(5)
        analyzed_videos = pipeline.run_analysis(clips)

        # One photo with high score
        photo_asset = Asset(
            id="photo-high-score",
            type=AssetType.IMAGE,
            fileCreatedAt=datetime(2024, 2, 1, tzinfo=UTC),
            fileModifiedAt=datetime(2024, 2, 1, tzinfo=UTC),
            updatedAt=datetime(2024, 2, 1, tzinfo=UTC),
            isFavorite=True,
        )
        photo_clip = VideoClipInfo(asset=photo_asset, duration_seconds=4.0, width=4032, height=3024)
        photo_cws = ClipWithSegment(clip=photo_clip, start_time=0.0, end_time=4.0, score=0.9)

        result = pipeline.run_selection(analyzed_videos + [photo_cws])

        photo_selected = [c for c in result.selected_clips if c.asset.type == AssetType.IMAGE]
        assert len(photo_selected) > 0
        assert photo_selected[0].asset.id == "photo-high-score"


class TestPipelineRunnerUnifiedFlow:
    """_pipeline_runner merges photos into the selection pool."""

    def test_photos_converted_to_clip_with_segment(self):
        """score_photos results should be convertible to ClipWithSegment."""
        # This tests the conversion logic that the pipeline runner uses
        photo_asset = Asset(
            id="photo-001",
            type=AssetType.IMAGE,
            fileCreatedAt=datetime(2024, 6, 15, tzinfo=UTC),
            fileModifiedAt=datetime(2024, 6, 15, tzinfo=UTC),
            updatedAt=datetime(2024, 6, 15, tzinfo=UTC),
            isFavorite=True,
            originalFileName="IMG_001.HEIC",
            width=4032,
            height=3024,
        )
        photo_score = 0.75
        photo_duration = 4.0

        # Convert to ClipWithSegment (same logic as pipeline runner)
        clip = VideoClipInfo(
            asset=photo_asset,
            duration_seconds=photo_duration,
            width=photo_asset.width,
            height=photo_asset.height,
        )
        cws = ClipWithSegment(
            clip=clip,
            start_time=0.0,
            end_time=photo_duration,
            score=photo_score,
        )

        assert cws.clip.asset.type == AssetType.IMAGE
        assert cws.score == 0.75
        assert cws.end_time == 4.0
        assert cws.clip.duration_seconds == 4.0


class TestMergePhotosIntoPool:
    """Tests for _merge_photos_into_pool helper in _pipeline_runner."""

    def test_returns_unchanged_when_photos_disabled(self):
        from immich_memories.cli._pipeline_runner import _merge_photos_into_pool

        mock_client = MagicMock()
        analyzed = [MagicMock(), MagicMock()]

        result = _merge_photos_into_pool(
            analyzed,
            photo_assets=None,
            include_photos=False,
            config=Config(),
            client=mock_client,
            work_dir=Path("/tmp"),
        )

        assert result is analyzed

    def test_returns_unchanged_when_no_photo_assets(self):
        from immich_memories.cli._pipeline_runner import _merge_photos_into_pool

        mock_client = MagicMock()
        analyzed = [MagicMock(), MagicMock()]

        result = _merge_photos_into_pool(
            analyzed,
            photo_assets=[],
            include_photos=True,
            config=Config(),
            client=mock_client,
            work_dir=Path("/tmp"),
        )

        assert result is analyzed

    def test_merges_scored_photos_with_videos(self, tmp_path):
        from unittest.mock import patch

        from immich_memories.cli._pipeline_runner import _merge_photos_into_pool

        mock_client = MagicMock()
        mock_client.download_asset = MagicMock()
        mock_client.get_asset_thumbnail = MagicMock()

        video_candidates = [MagicMock(), MagicMock()]

        photo_asset = Asset(
            id="photo-merge-001",
            type=AssetType.IMAGE,
            fileCreatedAt=datetime(2024, 6, 15, tzinfo=UTC),
            fileModifiedAt=datetime(2024, 6, 15, tzinfo=UTC),
            updatedAt=datetime(2024, 6, 15, tzinfo=UTC),
            isFavorite=True,
            width=4032,
            height=3024,
        )

        # WHY: mock score_photos to avoid LLM/download I/O
        with patch(
            "immich_memories.photos.photo_pipeline.score_photos",
            return_value=[(photo_asset, 0.8)],
        ):
            result = _merge_photos_into_pool(
                video_candidates,
                photo_assets=[photo_asset],
                include_photos=True,
                config=Config(),
                client=mock_client,
                work_dir=tmp_path,
            )

        assert len(result) == 3  # 2 videos + 1 photo
        photo_cws = result[2]
        assert isinstance(photo_cws, ClipWithSegment)
        assert photo_cws.clip.asset.type == AssetType.IMAGE
        assert photo_cws.score == 0.8
        assert photo_cws.end_time == Config().photos.duration


class TestRenderPhotoAsClip:
    """Tests for _render_photo_as_clip helper in generate.py."""

    def test_returns_none_without_client(self):
        from immich_memories.generate import GenerationParams, _render_photo_as_clip

        photo_asset = Asset(
            id="photo-no-client",
            type=AssetType.IMAGE,
            fileCreatedAt=datetime(2024, 6, 15, tzinfo=UTC),
            fileModifiedAt=datetime(2024, 6, 15, tzinfo=UTC),
            updatedAt=datetime(2024, 6, 15, tzinfo=UTC),
        )
        clip = VideoClipInfo(asset=photo_asset, duration_seconds=4.0, width=4032, height=3024)
        params = GenerationParams(
            clips=[clip],
            output_path=Path("/tmp/out.mp4"),
            config=Config(),
            client=None,
        )

        result = _render_photo_as_clip(clip, params, Path("/tmp"))
        assert result is None

    def test_delegates_to_render_single_photo(self, tmp_path):
        from unittest.mock import patch

        from immich_memories.generate import GenerationParams, _render_photo_as_clip
        from immich_memories.processing.assembly_config import AssemblyClip

        photo_asset = Asset(
            id="photo-delegate",
            type=AssetType.IMAGE,
            fileCreatedAt=datetime(2024, 6, 15, tzinfo=UTC),
            fileModifiedAt=datetime(2024, 6, 15, tzinfo=UTC),
            updatedAt=datetime(2024, 6, 15, tzinfo=UTC),
        )
        clip = VideoClipInfo(asset=photo_asset, duration_seconds=4.0, width=4032, height=3024)
        mock_client = MagicMock()
        params = GenerationParams(
            clips=[clip],
            output_path=tmp_path / "out.mp4",
            config=Config(),
            client=mock_client,
        )

        expected = AssemblyClip(
            path=tmp_path / "photo.mp4",
            duration=4.0,
            asset_id="photo-delegate",
            is_photo=True,
        )

        # WHY: mock _render_single_photo to avoid FFmpeg/cv2 dependencies
        with patch(
            "immich_memories.photos.photo_pipeline._render_single_photo",
            return_value=expected,
        ) as mock_render:
            result = _render_photo_as_clip(clip, params, tmp_path)

        assert result is expected
        mock_render.assert_called_once()

    def test_returns_none_when_render_fails(self, tmp_path):
        from unittest.mock import patch

        from immich_memories.generate import GenerationParams, _render_photo_as_clip

        photo_asset = Asset(
            id="photo-fail",
            type=AssetType.IMAGE,
            fileCreatedAt=datetime(2024, 6, 15, tzinfo=UTC),
            fileModifiedAt=datetime(2024, 6, 15, tzinfo=UTC),
            updatedAt=datetime(2024, 6, 15, tzinfo=UTC),
        )
        clip = VideoClipInfo(asset=photo_asset, duration_seconds=4.0, width=4032, height=3024)
        mock_client = MagicMock()
        params = GenerationParams(
            clips=[clip],
            output_path=tmp_path / "out.mp4",
            config=Config(),
            client=mock_client,
        )

        # WHY: mock _render_single_photo to simulate failure
        with patch(
            "immich_memories.photos.photo_pipeline._render_single_photo",
            return_value=None,
        ):
            result = _render_photo_as_clip(clip, params, tmp_path)

        assert result is None


class TestExtractClipsHandlesImages:
    """_extract_clips in generate.py should handle IMAGE-type clips."""

    def test_image_clip_calls_render_photo_as_clip(self, tmp_path):
        """IMAGE-type clips in params.clips should be rendered as photo animations."""
        from unittest.mock import patch

        from immich_memories.api.models import Asset, AssetType, VideoClipInfo
        from immich_memories.generate import GenerationParams, _extract_clips
        from immich_memories.processing.assembly_config import AssemblyClip

        photo_asset = Asset(
            id="photo-test-001",
            type=AssetType.IMAGE,
            fileCreatedAt=datetime(2024, 6, 15, tzinfo=UTC),
            fileModifiedAt=datetime(2024, 6, 15, tzinfo=UTC),
            updatedAt=datetime(2024, 6, 15, tzinfo=UTC),
            isFavorite=True,
            originalFileName="IMG_001.HEIC",
            width=4032,
            height=3024,
        )
        photo_clip = VideoClipInfo(asset=photo_asset, duration_seconds=4.0, width=4032, height=3024)

        mock_client = MagicMock()
        params = GenerationParams(
            clips=[photo_clip],
            output_path=tmp_path / "output.mp4",
            config=Config(),
            client=mock_client,
            clip_segments={"photo-test-001": (0.0, 4.0)},
        )

        # WHY: mock the photo rendering to avoid needing FFmpeg/cv2
        fake_photo_path = tmp_path / "photo_rendered.mp4"
        fake_photo_path.write_bytes(b"\x00" * 1000)
        mock_assembly_clip = AssemblyClip(
            path=fake_photo_path,
            duration=4.0,
            date="2024-06-15",
            asset_id="photo-test-001",
            is_photo=True,
        )

        mock_video_cache = MagicMock()

        with patch(
            "immich_memories.generate_photos._render_photo_as_clip",
            return_value=mock_assembly_clip,
        ):
            result = _extract_clips(params, mock_video_cache, tmp_path)

        assert len(result) == 1
        assert result[0].is_photo is True
        assert result[0].asset_id == "photo-test-001"

    def test_video_clips_still_extracted_normally(self, tmp_path):
        """VIDEO-type clips should still go through the normal download+extract path."""
        from unittest.mock import patch

        from immich_memories.generate import GenerationParams, _extract_clips

        video_asset = Asset(
            id="video-test-001",
            type=AssetType.VIDEO,
            fileCreatedAt=datetime(2024, 6, 15, tzinfo=UTC),
            fileModifiedAt=datetime(2024, 6, 15, tzinfo=UTC),
            updatedAt=datetime(2024, 6, 15, tzinfo=UTC),
            isFavorite=True,
            originalFileName="VID_001.MOV",
            exifInfo={"make": "Apple", "model": "iPhone 15 Pro"},
        )
        video_clip = VideoClipInfo(
            asset=video_asset, duration_seconds=10.0, width=1920, height=1080
        )

        mock_client = MagicMock()
        params = GenerationParams(
            clips=[video_clip],
            output_path=tmp_path / "output.mp4",
            config=Config(),
            client=mock_client,
            clip_segments={"video-test-001": (2.0, 7.0)},
        )

        mock_video_cache = MagicMock()
        fake_video = tmp_path / "video.mp4"
        fake_video.write_bytes(b"\x00" * 1000)
        fake_segment = tmp_path / "segment.mp4"
        fake_segment.write_bytes(b"\x00" * 500)

        with (
            patch(
                "immich_memories.generate_downloads.download_clip",
                return_value=fake_video,
            ),
            patch(
                "immich_memories.processing.clips.extract_clip",
                return_value=fake_segment,
            ),
        ):
            result = _extract_clips(params, mock_video_cache, tmp_path)

        assert len(result) == 1
        assert result[0].is_photo is False
        assert result[0].asset_id == "video-test-001"


class TestAddPhotosSkipped:
    """When photos are in the unified pool, _add_photos_if_enabled is a no-op."""

    def test_add_photos_returns_unchanged_when_disabled(self):
        from immich_memories.generate import GenerationParams, _add_photos_if_enabled

        params = GenerationParams(
            clips=[],
            output_path=Path("/tmp/test.mp4"),
            config=Config(),
            include_photos=False,
            photo_assets=None,
        )
        fake_clips = [MagicMock()]
        result = _add_photos_if_enabled(fake_clips, params, Path("/tmp"))

        # Should return the input unchanged — no photo processing
        assert result is fake_clips


class TestRunBackwardCompat:
    """run() still works as before — calls run_analysis + run_selection."""

    def test_run_produces_same_result_type(
        self,
        mock_immich_client,
        mock_analysis_cache,
        mock_thumbnail_cache,
    ):
        _setup_cache(mock_analysis_cache)
        pipeline = _make_pipeline(mock_immich_client, mock_analysis_cache, mock_thumbnail_cache)
        clips = _make_clips(10)

        result = pipeline.run(clips)

        assert isinstance(result, PipelineResult)
        assert len(result.selected_clips) > 0
