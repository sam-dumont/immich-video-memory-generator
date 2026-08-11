"""Tests for PreviewBuilder.run_legacy_analysis — verifies unified analyzer routing."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from immich_memories.analysis.analyzer_models import ScoredSegment
from immich_memories.analysis.preview_builder import PreviewBuilder
from immich_memories.config_models import AnalysisConfig, CacheConfig, ContentAnalysisConfig


def _make_builder(cache_config: CacheConfig | None = None) -> PreviewBuilder:
    return PreviewBuilder(
        client=MagicMock(),
        cache_config=cache_config or CacheConfig(),
        analysis_config=AnalysisConfig(),
        content_analysis_config=ContentAnalysisConfig(),
    )


class TestPreviewCacheLocation:
    """Pipeline previews are disposable cache data, not user-home state."""

    def test_finds_pipeline_preview_under_configured_cache(self, tmp_path: Path):
        cache_config = CacheConfig(directory=str(tmp_path / "cache"))
        preview = cache_config.cache_path / "previews" / "preview_video-1.mp4"
        preview.parent.mkdir(parents=True)
        preview.write_bytes(b"preview")

        result = _make_builder(cache_config).find_cached_preview("video-1", 0.0, 2.0)

        assert result == str(preview)

    def test_extracts_pipeline_preview_under_configured_cache(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        cache_config = CacheConfig(directory=str(tmp_path / "cache"))
        forbidden_home = tmp_path / "not-the-cache"

        def fake_run(command, **_kwargs):
            if command[0] == "ffprobe":
                return MagicMock(returncode=0, stdout="2.0")
            Path(command[-1]).write_bytes(b"preview")
            return MagicMock(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(Path, "home", classmethod(lambda _cls: forbidden_home))
        monkeypatch.setattr("subprocess.run", fake_run)
        monkeypatch.setattr(
            "immich_memories.analysis.preview_builder._get_fast_encoder_args",
            lambda: [],
        )

        result = Path(
            _make_builder(cache_config).extract_preview_segment(
                tmp_path / "source.mp4",
                0.0,
                2.0,
                asset_id="video-1",
            )
        )

        assert result.parent == cache_config.cache_path / "previews"
        assert not forbidden_home.exists()


def _make_segment(start: float, end: float, score: float) -> ScoredSegment:
    return ScoredSegment(
        start_time=start,
        end_time=end,
        total_score=score,
        visual_score=score,
        audio_score=0.5,
        duration_score=0.5,
        face_score=0.6,
        motion_score=0.4,
        stability_score=0.5,
    )


class TestRunLegacyAnalysis:
    def test_returns_best_segment(self):
        builder = _make_builder()
        seg = _make_segment(1.0, 4.0, 0.8)

        # WHY: mock at source module — import is inside function body
        with patch(
            "immich_memories.analysis.unified_analyzer.UnifiedSegmentAnalyzer.analyze",
            return_value=[seg],
        ):
            clip = MagicMock()
            clip.duration_seconds = 10.0
            config = MagicMock()
            config.avg_clip_duration = 5.0
            cache = MagicMock()

            start, end, score = builder.run_legacy_analysis(
                clip, Path("/fake.mp4"), None, 10.0, config, cache
            )

        assert score == 0.8
        assert start == 1.0
        cache.save_analysis.assert_called_once()

    def test_empty_segments_returns_fallback(self):
        builder = _make_builder()

        # WHY: mock to simulate no segments found
        with patch(
            "immich_memories.analysis.unified_analyzer.UnifiedSegmentAnalyzer.analyze",
            return_value=[],
        ):
            clip = MagicMock()
            clip.duration_seconds = 8.0
            config = MagicMock()
            config.avg_clip_duration = 5.0
            cache = MagicMock()

            start, end, score = builder.run_legacy_analysis(
                clip, Path("/fake.mp4"), None, 10.0, config, cache
            )

        assert start == 0.0
        assert end == 5.0
        assert score == 0.0

    def test_clamps_end_to_video_duration(self):
        builder = _make_builder()
        seg = _make_segment(9.0, 14.0, 0.7)

        # WHY: mock to control segment positions near video end
        with patch(
            "immich_memories.analysis.unified_analyzer.UnifiedSegmentAnalyzer.analyze",
            return_value=[seg],
        ):
            clip = MagicMock()
            clip.duration_seconds = 10.0
            config = MagicMock()
            config.avg_clip_duration = 5.0
            cache = MagicMock()

            start, end, score = builder.run_legacy_analysis(
                clip, Path("/fake.mp4"), None, 10.0, config, cache
            )

        assert end <= 10.0
        assert start >= 0.0
