"""Title rendering drill-down: profiles each sub-step individually.

Breaks the title pipeline into measurable phases to identify
exactly where time is spent. Covers both content-backed (slow-mo)
and gradient-only modes. Run with: make benchmark-titles

Results are comparable across Mac Metal / Linux NVIDIA / Linux CPU.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import pytest

from tests.integration.assembly.perf_utils import PerfResult, measure_resources, save_results
from tests.integration.conftest import requires_ffmpeg

logger = logging.getLogger("test.perf.titles")

pytestmark = [pytest.mark.integration, requires_ffmpeg, pytest.mark.perf]

_title_results: list[PerfResult] = []
_substep_timings: dict[str, float] = {}


@pytest.fixture(scope="module")
def pre_rendered_clip(fixtures_dir) -> Path:
    """Pre-render a 720p clip for title background (reused across tests)."""
    import subprocess

    out = fixtures_dir / "title_bg_clip.mp4"
    if out.exists():
        return out
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=3",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
            "-c:a", "aac", "-b:a", "64k", "-shortest",
            str(out),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )  # fmt: skip
    return out


# ---------------------------------------------------------------------------
# Content-backed mode (slow-mo + Taichi render)
# ---------------------------------------------------------------------------


class TestSlowmoBackgroundReader:
    """Profile SlowmoBackgroundReader: FFmpeg extraction + Catmull-Rom."""

    def test_slowmo_init(self, pre_rendered_clip):
        """Time: FFmpeg frame extraction + numpy array construction."""
        from immich_memories.titles.content_background import SlowmoBackgroundReader

        with measure_resources("slowmo_init", resolution="720p") as result:
            reader = SlowmoBackgroundReader(
                pre_rendered_clip, 1280, 720, 30.0, title_duration=3.5, source_seconds=0.5
            )

        assert reader.is_active, "SlowmoBackgroundReader failed to initialize"
        n_frames = len(reader._source_frames)

        logger.info(f"SUBSTEP: slowmo_init = {result.wall_seconds:.2f}s ({n_frames} frames)")
        _substep_timings["content_backed__slowmo_init"] = result.wall_seconds
        _title_results.append(result)
        reader.close()

    def test_catmull_rom_interpolation(self, pre_rendered_clip):
        """Time: 105 frames of Catmull-Rom cubic interpolation."""
        from immich_memories.titles.content_background import SlowmoBackgroundReader

        reader = SlowmoBackgroundReader(
            pre_rendered_clip, 1280, 720, 30.0, title_duration=3.5, source_seconds=0.5
        )
        assert reader.is_active

        total_frames = reader._total_output_frames
        frame_times: list[float] = []

        with measure_resources("catmull_rom_105", resolution="720p") as result:
            for _ in range(total_frames):
                t0 = time.monotonic()
                frame = reader.read_frame()
                frame_times.append(time.monotonic() - t0)
                assert frame is not None

        avg_ms = (sum(frame_times) / len(frame_times)) * 1000
        p95_ms = sorted(frame_times)[int(len(frame_times) * 0.95)] * 1000

        logger.info(
            f"SUBSTEP: catmull_rom = {result.wall_seconds:.2f}s "
            f"({total_frames} frames, avg={avg_ms:.1f}ms, p95={p95_ms:.1f}ms)"
        )
        _substep_timings["content_backed__catmull_rom"] = result.wall_seconds
        _substep_timings["content_backed__catmull_rom_avg_ms"] = avg_ms
        _substep_timings["content_backed__catmull_rom_p95_ms"] = p95_ms
        _title_results.append(result)
        reader.close()


class TestTaichiContentBacked:
    """Profile Taichi rendering with content-backed background."""

    def test_taichi_render_content_backed(self, pre_rendered_clip):
        """Time: per-frame Taichi render with slow-mo bg (blur + vignette + bokeh + text)."""
        from immich_memories.titles.content_background import SlowmoBackgroundReader
        from immich_memories.titles.renderer_taichi import TaichiTitleConfig, TaichiTitleRenderer

        reader = SlowmoBackgroundReader(
            pre_rendered_clip, 1280, 720, 30.0, title_duration=3.5, source_seconds=0.5
        )

        config = TaichiTitleConfig(
            width=1280,
            height=720,
            fps=30.0,
            duration=3.5,
            background_reader=reader,
            bg_color1="#1A1A2E",
            bg_color2="#16213E",
            text_color="#FFFFFF",
            blur_radius=int(720 * 0.10),
            enable_bokeh=True,
        )

        renderer = TaichiTitleRenderer(config=config)
        total_frames = renderer.total_frames
        frame_times: list[float] = []

        with measure_resources("taichi_content_backed", resolution="720p") as result:
            for i in range(total_frames):
                t0 = time.monotonic()
                frame = renderer.render_frame(i, "Test Title 2025", "A Subtitle")
                frame_times.append(time.monotonic() - t0)
                assert frame is not None
                assert frame.shape[0] == 720

        avg_ms = (sum(frame_times) / len(frame_times)) * 1000
        p95_ms = sorted(frame_times)[int(len(frame_times) * 0.95)] * 1000
        first_ms = frame_times[0] * 1000

        logger.info(
            f"SUBSTEP: taichi_content_backed = {result.wall_seconds:.2f}s "
            f"({total_frames} frames, avg={avg_ms:.1f}ms, p95={p95_ms:.1f}ms, "
            f"first={first_ms:.1f}ms)"
        )
        _substep_timings["content_backed__taichi_render"] = result.wall_seconds
        _substep_timings["content_backed__taichi_avg_ms"] = avg_ms
        _substep_timings["content_backed__taichi_p95_ms"] = p95_ms
        _substep_timings["content_backed__taichi_first_ms"] = first_ms
        _title_results.append(result)
        reader.close()

    def test_full_title_content_backed(self, pre_rendered_clip, tmp_path):
        """Time: full content-backed title (slowmo + render + encode).

        Exercises: TitleScreenGenerator, RenderingService, SlowmoBackgroundReader,
        TaichiTitleRenderer, encoding pipeline, text_builder.
        """
        from immich_memories.titles.generator import TitleScreenConfig, TitleScreenGenerator

        config = TitleScreenConfig(
            enabled=True,
            title_duration=3.5,
            ending_duration=0.0,
            orientation="landscape",
            resolution="720p",
            fps=30.0,
            hdr=False,
            title_override="Performance Test 2025",
            subtitle_override="Content Backed",
        )
        generator = TitleScreenGenerator(config=config, output_dir=tmp_path / "titles")

        with measure_resources("full_title_content_backed", resolution="720p") as result:
            screen = generator.generate_title_screen(
                year=2025,
                content_clip_path=pre_rendered_clip,
            )

        assert screen.path.exists()
        assert screen.screen_type == "title"
        size_mb = screen.path.stat().st_size / (1024 * 1024)
        assert size_mb > 0.01, f"Title output suspiciously small: {size_mb:.3f}MB"

        # Verify rendered video is correct resolution
        from tests.integration.conftest import ffprobe_json, has_stream

        probe = ffprobe_json(screen.path)
        assert has_stream(probe, "video")
        for s in probe.get("streams", []):
            if s.get("codec_type") == "video":
                assert int(s["width"]) == 1280, f"Expected 1280, got {s['width']}"
                assert int(s["height"]) == 720, f"Expected 720, got {s['height']}"

        logger.info(
            f"SUBSTEP: full_title_content_backed = {result.wall_seconds:.2f}s ({size_mb:.1f}MB)"
        )
        _substep_timings["content_backed__full_title"] = result.wall_seconds
        _substep_timings["content_backed__output_mb"] = size_mb
        _title_results.append(result)


# ---------------------------------------------------------------------------
# Gradient-only mode (no content clip, pure GPU rendering)
# ---------------------------------------------------------------------------


class TestTaichiGradient:
    """Profile Taichi rendering with gradient-only background (no SlowmoReader)."""

    def test_taichi_render_gradient_only(self):
        """Time: per-frame Taichi render with animated gradient (no content clip)."""
        from immich_memories.titles.renderer_taichi import TaichiTitleConfig, TaichiTitleRenderer

        config = TaichiTitleConfig(
            width=1280,
            height=720,
            fps=30.0,
            duration=3.5,
            background_reader=None,
            background_image=None,
            bg_color1="#1A1A2E",
            bg_color2="#16213E",
            gradient_type="linear",
            gradient_angle=135.0,
            gradient_rotation=10.0,
            color_pulse_amount=0.03,
            text_color="#FFFFFF",
            blur_radius=20,
            enable_bokeh=True,
            vignette_strength=0.3,
            vignette_pulse=0.05,
        )

        renderer = TaichiTitleRenderer(config=config)
        total_frames = renderer.total_frames
        frame_times: list[float] = []

        with measure_resources("taichi_gradient_only", resolution="720p") as result:
            for i in range(total_frames):
                t0 = time.monotonic()
                frame = renderer.render_frame(i, "Gradient Title 2025", "No Content Clip")
                frame_times.append(time.monotonic() - t0)
                assert frame is not None
                assert frame.shape[0] == 720

        avg_ms = (sum(frame_times) / len(frame_times)) * 1000
        p95_ms = sorted(frame_times)[int(len(frame_times) * 0.95)] * 1000
        first_ms = frame_times[0] * 1000

        logger.info(
            f"SUBSTEP: taichi_gradient = {result.wall_seconds:.2f}s "
            f"({total_frames} frames, avg={avg_ms:.1f}ms, p95={p95_ms:.1f}ms, "
            f"first={first_ms:.1f}ms)"
        )
        _substep_timings["gradient__taichi_render"] = result.wall_seconds
        _substep_timings["gradient__taichi_avg_ms"] = avg_ms
        _substep_timings["gradient__taichi_p95_ms"] = p95_ms
        _substep_timings["gradient__taichi_first_ms"] = first_ms
        _title_results.append(result)

    def test_full_title_gradient_only(self, tmp_path):
        """Time: full gradient-only title (render + encode, no SlowmoReader).

        Exercises: TitleScreenGenerator gradient path, RenderingService,
        TaichiTitleRenderer without background_reader, encoding pipeline.
        """
        from immich_memories.titles.generator import TitleScreenConfig, TitleScreenGenerator

        config = TitleScreenConfig(
            enabled=True,
            title_duration=3.5,
            ending_duration=0.0,
            orientation="landscape",
            resolution="720p",
            fps=30.0,
            hdr=False,
            style_mode="auto",
            title_override="Gradient Only 2025",
            subtitle_override="No Content Clip",
        )
        generator = TitleScreenGenerator(config=config, output_dir=tmp_path / "titles")

        with measure_resources("full_title_gradient_only", resolution="720p") as result:
            screen = generator.generate_title_screen(
                year=2025,
                content_clip_path=None,
            )

        assert screen.path.exists()
        assert screen.screen_type == "title"
        size_mb = screen.path.stat().st_size / (1024 * 1024)
        assert size_mb > 0.01, f"Gradient title too small: {size_mb:.3f}MB"

        from tests.integration.conftest import ffprobe_json, has_stream

        probe = ffprobe_json(screen.path)
        assert has_stream(probe, "video")
        for s in probe.get("streams", []):
            if s.get("codec_type") == "video":
                assert int(s["width"]) == 1280
                assert int(s["height"]) == 720

        logger.info(f"SUBSTEP: full_title_gradient = {result.wall_seconds:.2f}s ({size_mb:.1f}MB)")
        _substep_timings["gradient__full_title"] = result.wall_seconds
        _substep_timings["gradient__output_mb"] = size_mb
        _title_results.append(result)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def test_save_title_drilldown(tmp_path):
    """Save title drilldown results + derived overhead analysis."""
    if not _title_results:
        pytest.skip("No title drilldown results collected")

    logger.info("=" * 70)
    logger.info("TITLE RENDERING DRILL-DOWN")
    logger.info("=" * 70)

    logger.info("\n--- CONTENT-BACKED MODE ---")
    for key in sorted(k for k in _substep_timings if k.startswith("content_backed__")):
        val = _substep_timings[key]
        name = key.replace("content_backed__", "")
        unit = "ms" if key.endswith("_ms") else "MB" if key.endswith("_mb") else "s"
        logger.info(f"  {name:35s} = {val:>8.2f} {unit}")

    logger.info("\n--- GRADIENT-ONLY MODE ---")
    for key in sorted(k for k in _substep_timings if k.startswith("gradient__")):
        val = _substep_timings[key]
        name = key.replace("gradient__", "")
        unit = "ms" if key.endswith("_ms") else "MB" if key.endswith("_mb") else "s"
        logger.info(f"  {name:35s} = {val:>8.2f} {unit}")

    # Content-backed breakdown
    cb_catmull = _substep_timings.get("content_backed__catmull_rom", 0)
    cb_taichi = _substep_timings.get("content_backed__taichi_render", 0)
    cb_full = _substep_timings.get("content_backed__full_title", 0)
    cb_overhead = max(0, cb_full - cb_taichi)

    # Gradient breakdown
    gr_taichi = _substep_timings.get("gradient__taichi_render", 0)
    gr_full = _substep_timings.get("gradient__full_title", 0)
    gr_overhead = max(0, gr_full - gr_taichi)

    logger.info("\n--- DERIVED BREAKDOWN ---")
    logger.info("Content-backed (one screen):")
    logger.info(f"  Catmull-Rom interpolation:  {cb_catmull:>6.1f}s  (included in taichi_render)")
    logger.info(f"  Taichi render (incl bg):    {cb_taichi:>6.1f}s")
    logger.info(f"  Encoding + I/O overhead:    {cb_overhead:>6.1f}s")
    logger.info(f"  TOTAL:                      {cb_full:>6.1f}s")

    logger.info("Gradient-only (one screen):")
    logger.info(f"  Taichi render:              {gr_taichi:>6.1f}s")
    logger.info(f"  Encoding + I/O overhead:    {gr_overhead:>6.1f}s")
    logger.info(f"  TOTAL:                      {gr_full:>6.1f}s")

    if gr_taichi > 0:
        slowmo_overhead = cb_taichi - gr_taichi
        logger.info(f"\nContent-backed overhead vs gradient: +{slowmo_overhead:.1f}s per screen")
    logger.info("=" * 70)

    project_root = Path(__file__).resolve().parents[3]
    save_results(_title_results, project_root / "tests" / "perf-results-titles.json")

    import json

    substep_path = project_root / "tests" / "perf-results-titles-substeps.json"
    substep_path.write_text(
        json.dumps(
            {"timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"), "substeps": _substep_timings},
            indent=2,
        )
        + "\n"
    )
    logger.info(f"Results saved to perf-results-titles.json and {substep_path.name}")
