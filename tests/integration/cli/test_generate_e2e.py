"""End-to-end integration tests for the full generate pipeline.

Requires real Immich + FFmpeg. These tests fetch actual videos from Immich,
run the full analysis + assembly pipeline, and verify the output video
with ffprobe and pixel-level assertions.

Run: make test-integration-cli
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import pytest

from tests.integration.cli.test_generate import requires_immich

pytestmark = [pytest.mark.integration]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ffprobe_json(path: Path) -> dict:
    """Run ffprobe and return parsed JSON with all streams + format."""
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    return json.loads(result.stdout)


def _has_stream(probe: dict, codec_type: str) -> bool:
    return any(s.get("codec_type") == codec_type for s in probe.get("streams", []))


def _get_duration(probe: dict) -> float:
    return float(probe.get("format", {}).get("duration", 0))


def _get_resolution(probe: dict) -> tuple[int, int]:
    for s in probe.get("streams", []):
        if s.get("codec_type") == "video":
            return int(s["width"]), int(s["height"])
    return 0, 0


def _extract_frame_rgb(
    video: Path, time_pos: float, width: int = 160, height: int = 90
) -> np.ndarray:
    """Extract a frame at the given time position as a small RGB array."""
    result = subprocess.run(
        [
            "ffmpeg",
            "-ss",
            str(time_pos),
            "-i",
            str(video),
            "-vf",
            f"scale={width}:{height}",
            "-vframes",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "pipe:1",
        ],
        capture_output=True,
        timeout=15,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode(errors="replace")
        raise RuntimeError(f"Frame extraction failed: {stderr[-300:]}")
    return np.frombuffer(result.stdout, dtype=np.uint8).reshape(height, width, 3)


@pytest.fixture(scope="module")
def render(tmp_path_factory: pytest.TempPathFactory):
    """Render each distinct request once for the module.

    WHY a module directory: the integration teardown deletes files over 10MB from
    each test's own tmp_path, which would take a shared render with it.
    """
    root = tmp_path_factory.mktemp("e2e-renders")
    renders: dict[tuple, Path] = {}

    def rendered(**request) -> Path:
        key = tuple(sorted(request.items()))
        if key not in renders:
            workdir = root / f"render-{abs(hash(key))}"
            workdir.mkdir(exist_ok=True)
            renders[key] = _generate_memory(workdir, **request)
        return renders[key]

    return rendered


def _generate_memory(
    workdir: Path,
    *,
    year: int,
    month: int | None = None,
    enable_titles: bool = False,
    transition: str = "cut",
    target_duration: int = 15,
) -> Path:
    """Render one memory from the real library through `immich-memories generate`.

    The reader is pinned to rules so the suite never asks a model, and upload is
    pinned off so nothing is written back to Immich. Returns the output path.
    """
    from unittest.mock import patch

    from click.testing import CliRunner

    from immich_memories.cli import main
    from immich_memories.config_loader import Config

    config = Config.from_yaml(Config.get_default_path())
    config.title_screens.enabled = enable_titles
    config.editorial.reader = "rules"
    config.upload.enabled = False

    args = ["generate", "--year", str(year), "--duration", str(int(target_duration))]
    args += ["--memory-type", "monthly_highlights", "--month", str(month)] if month else []
    args += ["--transition", transition, "--resolution", "720p", "--no-music"]
    args += ["--output", str(workdir / "e2e.mp4")]
    # WHY: the owner's config, with the two overrides above, instead of the file on disk
    with patch("immich_memories.cli.get_config", return_value=config):
        result = CliRunner().invoke(main, args)
    assert result.exit_code == 0, result.output[-2000:]
    rendered = sorted(workdir.rglob("*.mp4"))
    assert len(rendered) == 1, f"expected one film in {workdir}, found {rendered}"
    return rendered[0]


# ---------------------------------------------------------------------------
# Tests: Basic pipeline validity
# ---------------------------------------------------------------------------


@requires_immich
class TestPipelineOutput:
    """Verify the pipeline produces a valid, non-empty video."""

    def test_output_exists_and_has_size(self, render):
        """Pipeline produces a file > 1KB."""
        result = render(year=2025, month=6)
        assert result.exists(), "Output file was not created"
        assert result.stat().st_size > 1000, (
            f"Output file only {result.stat().st_size} bytes — likely empty/corrupt"
        )

    def test_has_video_stream(self, render):
        """Output contains a video stream."""
        result = render(year=2025, month=6)
        probe = _ffprobe_json(result)
        assert _has_stream(probe, "video"), "No video stream in output"

    def test_has_audio_stream(self, render):
        """Output contains an audio stream (clip audio, even without music)."""
        result = render(year=2025, month=6)
        probe = _ffprobe_json(result)
        assert _has_stream(probe, "audio"), "No audio stream — assembly concat will fail downstream"

    def test_duration_reasonable(self, render):
        """Output duration is at least 3 seconds (not truncated)."""
        result = render(year=2025, month=6)
        probe = _ffprobe_json(result)
        duration = _get_duration(probe)
        assert duration > 3.0, f"Duration only {duration:.1f}s — pipeline may have truncated"


# ---------------------------------------------------------------------------
# Tests: Pixel-level content validation
# ---------------------------------------------------------------------------


@requires_immich
class TestPipelinePixels:
    """Verify the output video has actual visual content."""

    def test_mid_frame_not_black(self, render):
        """Mid-frame should have real content, not solid black."""
        result = render(year=2025, month=6)
        probe = _ffprobe_json(result)
        mid_time = _get_duration(probe) / 2.0
        frame = _extract_frame_rgb(result, mid_time)

        mean = float(frame.mean())
        # WHY: A black frame has mean ~0. Real video content is typically > 30.
        assert mean > 10.0, f"Mid-frame looks black (mean={mean:.1f})"

    def test_mid_frame_has_variation(self, render):
        """Mid-frame should have pixel variation (not solid color)."""
        result = render(year=2025, month=6)
        probe = _ffprobe_json(result)
        mid_time = _get_duration(probe) / 2.0
        frame = _extract_frame_rgb(result, mid_time)

        std = float(frame.std())
        # WHY: A solid color frame has std ~0. Real video has texture/edges.
        assert std > 5.0, f"Mid-frame is solid color (std={std:.1f})"

    def test_first_and_last_frames_differ(self, render):
        """First and last frames should be visually different (not frozen)."""
        result = render(year=2025, month=6)
        probe = _ffprobe_json(result)
        duration = _get_duration(probe)

        first = _extract_frame_rgb(result, 0.5)
        last = _extract_frame_rgb(result, max(0.5, duration - 0.5))

        diff = float(np.abs(first.astype(float) - last.astype(float)).mean())
        # WHY: If first == last, video may be a single frozen frame looped.
        assert diff > 3.0, f"First and last frames look identical (diff={diff:.1f})"


# ---------------------------------------------------------------------------
# Tests: Title screens (the most regression-prone path)
# ---------------------------------------------------------------------------


@requires_immich
class TestPipelineWithTitles:
    """Verify title screen rendering in the full pipeline.

    Title screens have been the source of most regressions (Noah crash,
    divider count changes, pink HDR). These tests enable titles and verify
    the output includes them.
    """

    def test_title_fade_from_white_at_start(self, render):
        """First frame should be near-white (title fade-from-white)."""
        result = render(year=2025, month=6, enable_titles=True, target_duration=30)
        first_frame = _extract_frame_rgb(result, 0.05)
        mean = float(first_frame.mean())
        # WHY: Title screens start with fade-from-white. First frame should
        # be bright. If titles aren't rendering, first frame is dark content.
        assert mean > 150, (
            f"First frame mean {mean:.0f} — expected bright (fade-from-white). "
            f"Title screen may not be rendering."
        )

    def test_ending_fade_to_white_at_end(self, render):
        """Last frame should be near-white (ending fade-to-white)."""
        result = render(year=2025, month=6, enable_titles=True, target_duration=30)
        probe = _ffprobe_json(result)
        duration = _get_duration(probe)
        last_frame = _extract_frame_rgb(result, max(0.1, duration - 0.1))
        mean = float(last_frame.mean())
        # WHY: Ending screen fades to white. Last frame should be bright.
        # If ending isn't rendering, last frame is dark content.
        assert mean > 150, (
            f"Last frame mean {mean:.0f} — expected bright (fade-to-white). "
            f"Ending screen may not be rendering."
        )

    def test_title_text_visible_in_first_seconds(self, render):
        """Frame at ~2s (mid-title) should show text on dark background."""
        result = render(year=2025, month=6, enable_titles=True, target_duration=30)
        # WHY: Title screen is 3.5s. At 2s, fade-from-white is done,
        # text should be visible on dark cinematic background.
        frame = _extract_frame_rgb(result, 2.0)

        # Center band (where text renders) should have more variation
        # than corners (pure background)
        h, w = frame.shape[:2]
        center = frame[int(h * 0.3) : int(h * 0.7), :]
        corner = frame[: int(h * 0.1), : int(w * 0.1)]

        center_range = float(center.max()) - float(center.min())
        corner_range = float(corner.max()) - float(corner.min())

        assert center_range > corner_range, (
            f"Title text not visible: center range ({center_range:.0f}) "
            f"should exceed corner range ({corner_range:.0f})"
        )


# ---------------------------------------------------------------------------
# Tests: Transition and resolution variations
# ---------------------------------------------------------------------------


@requires_immich
class TestPipelineVariations:
    """Test different pipeline configurations produce valid output."""

    def test_crossfade_transition(self, render):
        """Crossfade transition produces valid output."""
        result = render(year=2025, month=6, transition="crossfade")
        probe = _ffprobe_json(result)
        assert _has_stream(probe, "video")
        assert _get_duration(probe) > 2.0

    def test_single_month_has_content(self, render):
        """Single month produces video with real content."""
        result = render(year=2025, month=6)
        probe = _ffprobe_json(result)
        assert _has_stream(probe, "video")

        mid_time = _get_duration(probe) / 2.0
        frame = _extract_frame_rgb(result, mid_time)
        assert float(frame.mean()) > 10.0, "Single month output looks black"
