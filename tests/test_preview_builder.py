"""Tests for PreviewBuilder.run_legacy_analysis — verifies unified analyzer routing."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from immich_memories.analysis.analyzer_models import ScoredSegment
from immich_memories.analysis.preview_builder import PreviewBuilder
from immich_memories.config_models import AnalysisConfig, CacheConfig, ContentAnalysisConfig


def _make_builder(
    cache_config: CacheConfig | None = None,
    *,
    hardware_enabled: bool = True,
) -> PreviewBuilder:
    return PreviewBuilder(
        client=MagicMock(),
        cache_config=cache_config or CacheConfig(),
        analysis_config=AnalysisConfig(),
        content_analysis_config=ContentAnalysisConfig(),
        hardware_enabled=hardware_enabled,
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
            "immich_memories.analysis.preview_builder.fast_encoder_args",
            lambda **_kwargs: [],
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

    def test_hardware_disabled_uses_software_encoder(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        cache_config = CacheConfig(directory=str(tmp_path / "cache"))
        encode_commands: list[list[str]] = []

        def fake_run(command, **_kwargs):
            if command[0] == "ffprobe":
                return MagicMock(returncode=0, stdout="2.0")
            encode_commands.append(command)
            Path(command[-1]).write_bytes(b"preview")
            return MagicMock(returncode=0, stdout="", stderr="")

        monkeypatch.setattr("subprocess.run", fake_run)

        _make_builder(cache_config, hardware_enabled=False).extract_preview_segment(
            tmp_path / "source.mp4",
            0.0,
            2.0,
            asset_id="video-1",
        )

        assert len(encode_commands) == 1
        assert "libx264" in encode_commands[0]
        assert "h264_nvenc" not in encode_commands[0]


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
    def test_bound_provider_reuses_batch_analyzer_in_legacy_mode(self):
        builder = _make_builder()
        shared_analyzer = MagicMock()
        shared_analyzer.analyze.return_value = [_make_segment(1.0, 4.0, 0.8)]
        provider = MagicMock(return_value=shared_analyzer)
        clip = MagicMock(duration_seconds=10.0)
        config = MagicMock(avg_clip_duration=5.0)

        builder.bind_legacy_analyzer_provider(provider)
        builder.run_legacy_analysis(clip, Path("/video.mp4"), None, 10.0, config, MagicMock())

        provider.assert_called_once()
        shared_analyzer.analyze.assert_called_once_with(
            Path("/video.mp4"),
            video_duration=10.0,
            enable_content_analysis=False,
            enable_audio_content_analysis=False,
        )
        shared_analyzer.reset_for_video.assert_called_once()

    def test_reuses_standalone_analyzer_across_legacy_calls(self):
        builder = _make_builder()
        segment = _make_segment(1.0, 4.0, 0.8)
        clip = MagicMock(duration_seconds=10.0)
        config = MagicMock(avg_clip_duration=5.0)
        cache = MagicMock()

        with patch(
            "immich_memories.analysis.unified_analyzer.UnifiedSegmentAnalyzer"
        ) as analyzer_cls:
            analyzer_cls.return_value.analyze.return_value = [segment]
            builder.run_legacy_analysis(clip, Path("/first.mp4"), None, 10.0, config, cache)
            builder.run_legacy_analysis(clip, Path("/second.mp4"), None, 10.0, config, cache)

        analyzer_cls.assert_called_once()
        assert analyzer_cls.return_value.reset_for_video.call_count == 2

    def test_binding_provider_releases_existing_standalone_analyzer(self):
        builder = _make_builder()
        segment = _make_segment(1.0, 4.0, 0.8)
        clip = MagicMock(duration_seconds=10.0)
        config = MagicMock(avg_clip_duration=5.0)
        cache = MagicMock()
        shared_analyzer = MagicMock()
        shared_analyzer.analyze.return_value = [segment]

        with patch(
            "immich_memories.analysis.unified_analyzer.UnifiedSegmentAnalyzer"
        ) as standalone_cls:
            standalone_cls.return_value.analyze.return_value = [segment]
            builder.run_legacy_analysis(clip, Path("/first.mp4"), None, 10.0, config, cache)
            builder.bind_legacy_analyzer_provider(MagicMock(return_value=shared_analyzer))
            builder.run_legacy_analysis(clip, Path("/second.mp4"), None, 10.0, config, cache)

        standalone_cls.return_value.clear_cache.assert_called_once_with(release_audio_analyzer=True)
        standalone_cls.return_value.reset_for_video.assert_called()
        shared_analyzer.analyze.assert_called_once_with(
            Path("/second.mp4"),
            video_duration=10.0,
            enable_content_analysis=False,
            enable_audio_content_analysis=False,
        )

    def test_standalone_legacy_close_releases_reusable_service_without_gc(self):
        builder = _make_builder()
        segment = _make_segment(1.0, 4.0, 0.8)
        clip = MagicMock(duration_seconds=10.0)
        config = MagicMock(avg_clip_duration=5.0)
        cache = MagicMock()

        with (
            patch(
                "immich_memories.analysis.unified_analyzer.UnifiedSegmentAnalyzer"
            ) as analyzer_cls,
            patch("gc.collect") as collect,
        ):
            analyzer_cls.return_value.analyze.return_value = [segment]
            builder.run_legacy_analysis(clip, Path("/video.mp4"), None, 10.0, config, cache)
            builder.close()

        analyzer_cls.return_value.reset_for_video.assert_called()
        analyzer_cls.return_value.clear_cache.assert_called_once_with(release_audio_analyzer=True)
        collect.assert_not_called()

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


@pytest.fixture(scope="module")
def tiny_video(tmp_path_factory) -> Path:
    import subprocess

    path = tmp_path_factory.mktemp("preview_src") / "clip.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=640x360:rate=10:duration=8",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )
    return path


class TestPreviewCacheActuallyHits:
    """A preview has to be findable again by the asset it was built for.

    The writer named files `preview_<ms-timestamp>.mp4` while the reader globbed
    `*<asset_id[:8]}*`, so the lookup could never match its own output: every
    analysed clip re-encoded a preview it already had, from the 4K original.
    """

    def test_a_written_preview_is_found_again_for_the_same_asset(
        self, tiny_video: Path, tmp_path: Path
    ):
        cache_config = CacheConfig(directory=str(tmp_path / "cache"))
        builder = _make_builder(cache_config)

        written = builder.extract_preview_segment(
            tiny_video, 1.0, 4.0, asset_id="abcd1234-5678-90ab-cdef-1234567890ab"
        )
        assert written and Path(written).exists()

        found = builder.find_cached_preview("abcd1234-5678-90ab-cdef-1234567890ab", 1.0, 4.0)

        assert found == written, "the preview cache cannot find its own output"

    def test_a_different_asset_does_not_match(self, tiny_video: Path, tmp_path: Path):
        """Naming by asset must not turn the cache into a single shared file."""
        cache_config = CacheConfig(directory=str(tmp_path / "cache"))
        builder = _make_builder(cache_config)
        builder.extract_preview_segment(tiny_video, 1.0, 4.0, asset_id="aaaaaaaa-1111")

        assert builder.find_cached_preview("bbbbbbbb-2222", 1.0, 4.0) is None


class TestPreviewSourceResolution:
    def test_preview_is_built_from_the_analysis_proxy_not_the_original(self, tmp_path: Path):
        """The preview is a UI thumbnail; encoding it from the 4K original cost
        3.4s and 72MB per clip against 0.4s and 10MB from the 480p proxy."""
        builder = _make_builder(CacheConfig(directory=str(tmp_path / "cache")))
        original = tmp_path / "original.mov"
        analysis = tmp_path / "analysis_480p.mp4"
        original.write_bytes(b"x")
        analysis.write_bytes(b"y")
        clip = MagicMock()
        clip.asset.id = "asset-1"

        with patch.object(
            builder, "extract_preview_segment", return_value=str(analysis)
        ) as extract:
            builder.extract_and_log_preview(clip, original, analysis, 1.0, 4.0)

        assert extract.call_args.args[0] == analysis
