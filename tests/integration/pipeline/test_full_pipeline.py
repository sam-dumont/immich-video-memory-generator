"""Full pipeline integration tests — maximum feature coverage in minimal tests.

Each test enables MANY features simultaneously to exercise hundreds of lines
across assembly_engine, filter_builder, title_inserter, renderer_pil,
clip_encoder, text_builder, audio_mixer, and more.

Real FFmpeg, real title rendering. No mocks.
Run: make test-integration-pipeline
"""

from __future__ import annotations

import subprocess
from datetime import date
from pathlib import Path

import pytest

from tests.conftest import make_clip
from tests.integration.conftest import (
    ffprobe_json,
    get_duration,
    has_stream,
    requires_ffmpeg,
)

pytestmark = [pytest.mark.integration, requires_ffmpeg]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_resolution(probe_data: dict) -> tuple[int, int]:
    for s in probe_data.get("streams", []):
        if s.get("codec_type") == "video":
            return int(s["width"]), int(s["height"])
    raise ValueError("No video stream found")


def _get_stream_duration(probe_data: dict, codec_type: str) -> float:
    """Get duration of a specific stream type (video/audio)."""
    for s in probe_data.get("streams", []):
        if s.get("codec_type") == codec_type and "duration" in s:
            return float(s["duration"])
    return 0.0


def _extract_frame_brightness(video_path: Path, timestamp: float) -> float:
    """Extract a frame at the given timestamp and return its mean brightness (0-255)."""
    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            str(timestamp),
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "gray",
            "-",
        ],
        capture_output=True,
        timeout=10,
    )
    if result.returncode != 0 or len(result.stdout) == 0:
        return -1.0
    import numpy as np

    pixels = np.frombuffer(result.stdout, dtype=np.uint8)
    return float(np.mean(pixels))


def _make_test_clip(path: Path, asset_id: str = "test", **kwargs) -> object:
    """Create a VideoClipInfo pointing to a real local file."""
    clip = make_clip(asset_id, duration=3.0, width=1280, height=720, **kwargs)
    clip.local_path = str(path)
    return clip


def _has_immich() -> bool:
    try:
        from immich_memories.config_loader import Config

        config = Config.from_yaml(Config.get_default_path())
        if not config.immich.url or not config.immich.api_key:
            return False
        import httpx

        resp = httpx.get(
            f"{config.immich.url.rstrip('/')}/api/server/ping",
            headers={"x-api-key": config.immich.api_key},
            timeout=5.0,
        )
        return resp.status_code == 200
    except Exception:
        return False


requires_immich = pytest.mark.skipif(not _has_immich(), reason="Immich not reachable")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def short_clip_a(tmp_path_factory) -> Path:
    """3s 640x360 clip (small for speed, enough for title rendering)."""
    out = tmp_path_factory.mktemp("full_pipeline") / "clip_a.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=640x360:rate=30:duration=3",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=3",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "28",
            "-c:a",
            "aac",
            "-b:a",
            "64k",
            "-shortest",
            str(out),
        ],
        check=True,
        capture_output=True,
        timeout=15,
    )
    return out


@pytest.fixture(scope="module")
def short_clip_b(tmp_path_factory) -> Path:
    """3s 640x360 clip with different visual pattern."""
    out = tmp_path_factory.mktemp("full_pipeline") / "clip_b.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=640x360:rate=30:duration=3:alpha=160",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:duration=3",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "28",
            "-c:a",
            "aac",
            "-b:a",
            "64k",
            "-shortest",
            str(out),
        ],
        check=True,
        capture_output=True,
        timeout=15,
    )
    return out


@pytest.fixture(scope="module")
def short_clip_c(tmp_path_factory) -> Path:
    """3s 640x360 clip — third clip for multi-clip tests."""
    out = tmp_path_factory.mktemp("full_pipeline") / "clip_c.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=640x360:rate=30:duration=3:alpha=80",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=660:duration=3",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "28",
            "-c:a",
            "aac",
            "-b:a",
            "64k",
            "-shortest",
            str(out),
        ],
        check=True,
        capture_output=True,
        timeout=15,
    )
    return out


@pytest.fixture(scope="module")
def short_music(tmp_path_factory) -> Path:
    """10s music file — long enough to cover a multi-clip assembly."""
    out = tmp_path_factory.mktemp("full_pipeline") / "music.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=330:duration=10",
            "-ar",
            "44100",
            str(out),
        ],
        check=True,
        capture_output=True,
        timeout=10,
    )
    return out


@pytest.fixture(scope="module")
def immich_short_clips():
    """Fetch 3 short clips (<30s) from Immich. Module-scoped for speed."""
    from immich_memories.api.immich import SyncImmichClient
    from immich_memories.config_loader import Config
    from immich_memories.generate import assets_to_clips
    from immich_memories.timeperiod import DateRange

    config = Config.from_yaml(Config.get_default_path())
    client = SyncImmichClient(base_url=config.immich.url, api_key=config.immich.api_key)

    date_range = DateRange(start=date(2025, 1, 1), end=date(2025, 1, 31))
    assets = client.get_videos_for_date_range(date_range)

    if not assets:
        date_range = DateRange(start=date(2024, 6, 1), end=date(2024, 12, 31))
        assets = client.get_videos_for_date_range(date_range)

    clips = assets_to_clips(assets)
    short = [c for c in clips if c.duration_seconds < 30]

    if len(short) < 2:
        pytest.skip("Need at least 2 short clips (<30s) in Immich")

    return short[:3], config, client


# ---------------------------------------------------------------------------
# Test 1: Full pipeline with title screens, smart transitions, date overlay
# ---------------------------------------------------------------------------


class TestFullPipelineWithTitles:
    @requires_immich
    def test_full_pipeline_with_titles_and_transitions(self, immich_short_clips, tmp_path):
        """Full pipeline: real Immich clips -> smart transitions -> title screen -> valid video.

        Exercises: generate_memory, _build_assembly_settings, _build_title_settings,
        VideoAssembler.assemble_with_titles, TitleInserter, TitleScreenGenerator,
        RenderingService, renderer_pil, text_builder, AssemblyEngine, FilterBuilder,
        ClipEncoder, clip_encoder, assembly_context_builder.
        """
        from immich_memories.generate import GenerationParams, generate_memory

        clips, config, client = immich_short_clips

        # Enable title screens (default is enabled=True, so don't disable)
        config.title_screens.enabled = True
        config.title_screens.title_duration = 2.0  # Shorter for test speed
        config.title_screens.ending_duration = 2.0
        config.title_screens.month_divider_duration = 1.5

        output = tmp_path / "full_titles.mp4"
        progress_phases = []

        params = GenerationParams(
            clips=clips[:2],
            output_path=output,
            config=config,
            client=client,
            transition="smart",  # TransitionType.SMART — mix of cut and crossfade
            transition_duration=0.3,
            person_name="Test Person",
            date_start=date(2024, 1, 1),
            date_end=date(2025, 12, 31),
            memory_type="year_in_review",
            progress_callback=lambda phase, _pct, _msg: progress_phases.append(phase),
        )

        result = generate_memory(params)

        assert result.exists(), f"Output file not created at {result}"
        assert result.stat().st_size > 1000, "Output file too small"

        probe = ffprobe_json(result)
        assert has_stream(probe, "video"), "Missing video stream"
        duration = get_duration(probe)

        # 2 clips (3-30s each) + title (~2s) + ending (~2s) — at least 4s
        assert duration > 4.0, f"Duration too short: {duration}s"

        # Progress callback was invoked
        assert len(progress_phases) > 0
        assert "extract" in progress_phases
        assert "assemble" in progress_phases


# ---------------------------------------------------------------------------
# Test 2: Full pipeline with synthetic clips + title screens (no Immich)
# ---------------------------------------------------------------------------


class TestFullPipelineSynthetic:
    def test_titles_and_crossfade_with_synthetic_clips(
        self, short_clip_a, short_clip_b, short_clip_c, tmp_path
    ):
        """Synthetic clips -> title screens + crossfade -> valid video.

        Same code paths as real Immich but uses local synthetic clips.
        Exercises title generation, rendering, text layout, assembly engine.
        """
        from immich_memories.config_loader import Config
        from immich_memories.generate import GenerationParams, generate_memory

        clip_a = _make_test_clip(short_clip_a, "synth-a")
        clip_b = _make_test_clip(short_clip_b, "synth-b")
        clip_c = _make_test_clip(short_clip_c, "synth-c")

        config = Config()
        config.title_screens.enabled = True
        config.title_screens.title_duration = 2.0
        config.title_screens.ending_duration = 2.0
        config.title_screens.month_divider_duration = 1.5

        output = tmp_path / "synthetic_titles.mp4"

        params = GenerationParams(
            clips=[clip_a, clip_b, clip_c],
            output_path=output,
            config=config,
            transition="crossfade",
            transition_duration=0.3,
            person_name="Jane Doe",
            date_start=date(2025, 6, 1),
            date_end=date(2025, 6, 30),
            memory_type="month",
        )

        result = generate_memory(params)

        assert result.exists()
        assert result.stat().st_size > 1000

        probe = ffprobe_json(result)
        assert has_stream(probe, "video")
        duration = get_duration(probe)
        # 3 clips * 3s - crossfade overlap + titles ~4s = should be > 8s
        assert duration > 8.0, f"Duration too short: {duration}s"

        # Title screen at t=0.5 should NOT be all-black — title rendering produced content
        brightness = _extract_frame_brightness(result, 0.5)
        assert brightness > 5.0, (
            f"Title frame at t=0.5 appears all-black (brightness={brightness:.1f}), "
            "title rendering may have failed"
        )


# ---------------------------------------------------------------------------
# Test 3: Trip memory type pipeline
# ---------------------------------------------------------------------------


class TestTripMemoryPipeline:
    def test_trip_memory_type_with_gps(self, short_clip_a, short_clip_b, tmp_path):
        """Trip memory: GPS locations -> title settings -> assembly with trip titles.

        Exercises: _build_title_settings trip branch, _extract_trip_locations,
        _generate_trip_title_text, TitleScreenGenerator trip_service path.
        """
        from immich_memories.config_loader import Config
        from immich_memories.generate import GenerationParams, generate_memory

        clip_a = _make_test_clip(short_clip_a, "trip-a")
        clip_b = _make_test_clip(short_clip_b, "trip-b")

        config = Config()
        config.title_screens.enabled = True
        config.title_screens.title_duration = 2.0
        config.title_screens.ending_duration = 2.0

        output = tmp_path / "trip_memory.mp4"

        params = GenerationParams(
            clips=[clip_a, clip_b],
            output_path=output,
            config=config,
            transition="crossfade",
            transition_duration=0.3,
            memory_type="trip",
            memory_preset_params={
                "location_name": "Barcelona",
                "trip_start": date(2025, 7, 1),
                "trip_end": date(2025, 7, 5),
            },
            date_start=date(2025, 7, 1),
            date_end=date(2025, 7, 5),
        )

        result = generate_memory(params)

        assert result.exists()
        assert result.stat().st_size > 1000

        probe = ffprobe_json(result)
        assert has_stream(probe, "video")
        duration = get_duration(probe)
        assert duration > 4.0, f"Duration too short: {duration}s"


# ---------------------------------------------------------------------------
# Test 4: Pipeline with music mixing
# ---------------------------------------------------------------------------


class TestPipelineWithMusic:
    def test_pipeline_with_music_mixing(self, short_clip_a, short_clip_b, short_music, tmp_path):
        """Assembly + music mixing: verify audio stream in output.

        Exercises: generate_memory music path, _apply_music_file,
        AudioMixerService, DuckingConfig, mix_audio_with_ducking.
        """
        from immich_memories.config_loader import Config
        from immich_memories.generate import GenerationParams, generate_memory

        clip_a = _make_test_clip(short_clip_a, "music-a")
        clip_b = _make_test_clip(short_clip_b, "music-b")

        config = Config()
        config.title_screens.enabled = False  # Isolate music testing

        output = tmp_path / "with_music.mp4"

        params = GenerationParams(
            clips=[clip_a, clip_b],
            output_path=output,
            config=config,
            transition="crossfade",
            transition_duration=0.3,
            music_path=short_music,
            music_volume=0.7,
        )

        result = generate_memory(params)

        assert result.exists()
        assert result.stat().st_size > 1000

        probe = ffprobe_json(result)
        assert has_stream(probe, "video")
        assert has_stream(probe, "audio"), "Music should produce an audio stream"
        duration = get_duration(probe)
        assert duration > 3.0

        # Audio duration should match video duration (music trimmed to fit)
        audio_dur = _get_stream_duration(probe, "audio")
        video_dur = _get_stream_duration(probe, "video")
        if audio_dur > 0 and video_dur > 0:
            assert abs(audio_dur - video_dur) < 0.5, (
                f"Audio/video duration mismatch: audio={audio_dur:.2f}s, video={video_dur:.2f}s"
            )


# ---------------------------------------------------------------------------
# Test 5: Resolution and format variations
# ---------------------------------------------------------------------------


class TestResolutionVariations:
    def test_resolution_override_720p(self, test_clip_720p, test_clip_720p_b, tmp_path):
        """Force 720p output resolution via GenerationParams.

        Uses two 720p clips so the assembly engine (not passthrough) runs.
        Exercises: _build_assembly_settings resolution_map path,
        auto_resolution=False, target_resolution override.
        """
        from immich_memories.config_loader import Config
        from immich_memories.generate import GenerationParams, generate_memory

        clip_a = _make_test_clip(test_clip_720p, "res-720a")
        clip_b = _make_test_clip(test_clip_720p_b, "res-720b")

        config = Config()
        config.title_screens.enabled = False

        output = tmp_path / "forced_720p.mp4"

        params = GenerationParams(
            clips=[clip_a, clip_b],
            output_path=output,
            config=config,
            transition="crossfade",
            transition_duration=0.3,
            output_resolution="720p",
        )

        result = generate_memory(params)

        assert result.exists()
        probe = ffprobe_json(result)
        assert has_stream(probe, "video")
        w, h = _get_resolution(probe)
        # Source is 720p (1280x720), target is 720p — output must be at most 720p
        assert w <= 1280, f"Width exceeds 720p target: {w}"
        assert h <= 720, f"Height exceeds 720p target: {h}"
        # Should actually BE 720p (not downscaled further) since source matches target
        assert w == 1280 and h == 720, (
            f"Expected exact 720p output (1280x720) since source is 720p, got {w}x{h}"
        )

    def test_scale_mode_applied_in_assembly(self, test_clip_720p, test_clip_720p_b, tmp_path):
        """Verify assembly with explicit scale_mode setting runs without error.

        Exercises: AssemblySettings.scale_mode, scaling_utilities path.
        """
        from immich_memories.processing.assembly_config import (
            AssemblyClip,
            AssemblySettings,
            TransitionType,
        )
        from immich_memories.processing.video_assembler import VideoAssembler

        settings = AssemblySettings(
            transition=TransitionType.CROSSFADE,
            transition_duration=0.3,
            output_crf=28,
            auto_resolution=False,
            target_resolution=(1280, 720),
            scale_mode="blur",
            normalize_clip_audio=False,
        )

        assembler = VideoAssembler(settings)
        output = tmp_path / "scale_blur.mp4"
        clips = [
            AssemblyClip(path=test_clip_720p, duration=3.0),
            AssemblyClip(path=test_clip_720p_b, duration=3.0),
        ]

        result = assembler.assemble(clips, output)

        assert result.exists()
        probe = ffprobe_json(result)
        assert has_stream(probe, "video")
        assert get_duration(probe) > 4.0

        # Output resolution should match the target_resolution (1280x720)
        w, h = _get_resolution(probe)
        assert w == 1280 and h == 720, (
            f"Scale mode 'blur' should produce target resolution 1280x720, got {w}x{h}"
        )


# ---------------------------------------------------------------------------
# Test 6: Full pipeline with Immich + live photos
# ---------------------------------------------------------------------------


@requires_immich
class TestLivePhotoFullPipeline:
    def test_full_pipeline_with_live_photos(self, tmp_path):
        """Full pipeline including live photo burst detection from Immich.

        Exercises: cluster_live_photos, build_merge_command, filter_valid_clips,
        _download_and_merge_burst, live_photo_merger.
        """
        from immich_memories.api.immich import SyncImmichClient
        from immich_memories.config_loader import Config
        from immich_memories.processing.live_photo_merger import cluster_live_photos
        from immich_memories.timeperiod import DateRange

        config = Config.from_yaml(Config.get_default_path())
        client = SyncImmichClient(base_url=config.immich.url, api_key=config.immich.api_key)

        live_assets = client.get_live_photos_for_date_range(
            DateRange(start=date(2024, 1, 1), end=date(2026, 1, 1))
        )

        if len(live_assets) < 2:
            pytest.skip("Need at least 2 live photos in Immich")

        clusters = cluster_live_photos(live_assets, merge_window_seconds=10.0)
        merge_cluster = next((c for c in clusters if c.count >= 2), None)
        if merge_cluster is None:
            pytest.skip("No live photo clusters with 2+ photos found")

        burst_ids = merge_cluster.video_asset_ids
        if len(burst_ids) < 2:
            pytest.skip("Cluster missing video component IDs")

        # Download burst video components
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        clip_paths = []
        for vid in burst_ids:
            dest = cache_dir / f"{vid}.MOV"
            try:
                client.download_asset(vid, dest)
                if dest.exists() and dest.stat().st_size > 0:
                    clip_paths.append(dest)
            except Exception:
                pass

        if len(clip_paths) < 2:
            pytest.skip("Could not download enough burst video components")

        # Merge and verify
        from immich_memories.processing.live_photo_merger import build_merge_command

        trim_points = merge_cluster.trim_points()[: len(clip_paths)]
        merged_path = tmp_path / "merged_burst.mp4"
        cmd = build_merge_command(clip_paths, trim_points, merged_path)

        merge_result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        assert merge_result.returncode == 0, f"FFmpeg merge failed: {merge_result.stderr[:500]}"
        assert merged_path.exists()
        assert merged_path.stat().st_size > 1000

        probe = ffprobe_json(merged_path)
        assert has_stream(probe, "video")
        assert get_duration(probe) > 0

        # Now run generate_memory with the merged burst as a regular clip
        from immich_memories.generate import GenerationParams, generate_memory

        burst_clip = make_clip("burst-clip", duration=get_duration(probe), width=1280, height=720)
        burst_clip.local_path = str(merged_path)

        config.title_screens.enabled = False
        output = tmp_path / "burst_pipeline.mp4"

        params = GenerationParams(
            clips=[burst_clip],
            output_path=output,
            config=config,
            transition="cut",
        )

        final = generate_memory(params)
        assert final.exists()
        assert final.stat().st_size > 1000


# ---------------------------------------------------------------------------
# Test 7: Title override + custom subtitle
# ---------------------------------------------------------------------------


class TestTitleOverridePipeline:
    def test_custom_title_override_renders(self, short_clip_a, short_clip_b, tmp_path):
        """Custom title + subtitle override flows through to rendered title screen.

        Exercises: GenerationParams.title/subtitle, _build_title_settings
        title_override branch, TitleInserter with overrides.
        """
        from immich_memories.config_loader import Config
        from immich_memories.generate import GenerationParams, generate_memory

        clip_a = _make_test_clip(short_clip_a, "title-a")
        clip_b = _make_test_clip(short_clip_b, "title-b")

        config = Config()
        config.title_screens.enabled = True
        config.title_screens.title_duration = 2.0
        config.title_screens.ending_duration = 2.0

        output = tmp_path / "custom_title.mp4"

        params = GenerationParams(
            clips=[clip_a, clip_b],
            output_path=output,
            config=config,
            transition="crossfade",
            transition_duration=0.3,
            title="Summer Memories 2025",
            subtitle="The Best Days",
            date_start=date(2025, 6, 1),
            date_end=date(2025, 8, 31),
        )

        result = generate_memory(params)

        assert result.exists()
        assert result.stat().st_size > 1000

        probe = ffprobe_json(result)
        assert has_stream(probe, "video")
        duration = get_duration(probe)
        # 2 clips + title + ending
        assert duration > 6.0, f"Duration too short: {duration}s"


# ---------------------------------------------------------------------------
# Test 8: Cut transitions (no crossfade)
# ---------------------------------------------------------------------------


class TestCutTransitionPipeline:
    def test_cut_transition_no_overlap(self, short_clip_a, short_clip_b, tmp_path):
        """Cut transitions produce output = sum of clip durations.

        Exercises: TransitionType.CUT path in AssemblyEngine,
        ConcatService concat-only strategy.
        """
        from immich_memories.config_loader import Config
        from immich_memories.generate import GenerationParams, generate_memory

        clip_a = _make_test_clip(short_clip_a, "cut-a")
        clip_b = _make_test_clip(short_clip_b, "cut-b")

        config = Config()
        config.title_screens.enabled = False

        output = tmp_path / "cut_only.mp4"

        params = GenerationParams(
            clips=[clip_a, clip_b],
            output_path=output,
            config=config,
            transition="cut",
        )

        result = generate_memory(params)

        assert result.exists()
        probe = ffprobe_json(result)
        assert has_stream(probe, "video")
        duration = get_duration(probe)
        # Cut transitions: no crossfade overlap, so output = sum of inputs
        expected_duration = 6.0  # 2 clips * 3s each
        assert abs(duration - expected_duration) < 0.5, (
            f"Cut transition duration should equal sum of inputs: expected ~{expected_duration}s, got {duration}s"
        )


# ---------------------------------------------------------------------------
# Test 9: Privacy mode
# ---------------------------------------------------------------------------


class TestPrivacyModePipeline:
    def test_privacy_mode_blurs_clips(self, short_clip_a, short_clip_b, tmp_path):
        """Privacy mode: clips get blurred (sigma=30). Verify by comparing
        the same clip assembled with and without privacy mode.
        """
        import subprocess

        import numpy as np

        from immich_memories.config_loader import Config
        from immich_memories.generate import GenerationParams, generate_memory

        clip_a = _make_test_clip(short_clip_a, "priv-a")

        config = Config()
        config.title_screens.enabled = False

        # Generate WITHOUT privacy mode
        normal_output = tmp_path / "normal.mp4"
        params_normal = GenerationParams(
            clips=[clip_a],
            output_path=normal_output,
            config=config,
            transition="cut",
            privacy_mode=False,
        )
        result_normal = generate_memory(params_normal)

        # Generate WITH privacy mode
        privacy_output = tmp_path / "privacy.mp4"
        params_privacy = GenerationParams(
            clips=[clip_a],
            output_path=privacy_output,
            config=config,
            transition="cut",
            privacy_mode=True,
        )
        result_privacy = generate_memory(params_privacy)

        assert result_normal.exists()
        assert result_privacy.exists()

        # Extract a frame from each at t=1s
        frames_dir = tmp_path / "frames"
        frames_dir.mkdir()

        for label, path in [("normal", result_normal), ("privacy", result_privacy)]:
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-ss",
                    "1",
                    "-i",
                    str(path),
                    "-frames:v",
                    "1",
                    "-f",
                    "rawvideo",
                    "-pix_fmt",
                    "gray",
                    str(frames_dir / f"{label}.raw"),
                ],
                capture_output=True,
                timeout=10,
            )

        normal_f = frames_dir / "normal.raw"
        privacy_f = frames_dir / "privacy.raw"

        if normal_f.exists() and privacy_f.exists():
            normal_data = np.fromfile(str(normal_f), dtype=np.uint8).astype(np.float32)
            privacy_data = np.fromfile(str(privacy_f), dtype=np.uint8).astype(np.float32)

            # Edge content: variance of pixel differences (high = sharp, low = blurred)
            normal_edges = float(np.var(np.diff(normal_data))) if len(normal_data) > 1 else 0
            privacy_edges = float(np.var(np.diff(privacy_data))) if len(privacy_data) > 1 else 0

            # Privacy mode should produce LESS edge content (sigma=30 blur)
            assert privacy_edges < normal_edges, (
                f"Privacy should be blurrier: privacy={privacy_edges:.1f} vs normal={normal_edges:.1f}"
            )
