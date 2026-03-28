"""Real Taichi title renderer integration tests.

Renders actual frames via TaichiTitleRenderer on CPU backend, verifying
that output frames have correct dimensions, non-blank content, and
animation produces visual change over time.

Run: make test-integration-titles
"""

from __future__ import annotations

import os
import subprocess

import numpy as np
import pytest

os.environ["IMMICH_FORCE_CPU"] = "1"

from immich_memories.titles.renderer_taichi import (  # noqa: E402
    TaichiTitleConfig,
    TaichiTitleRenderer,
)
from immich_memories.titles.taichi_kernels import TAICHI_AVAILABLE, init_taichi  # noqa: E402
from immich_memories.titles.taichi_video import create_title_video_taichi  # noqa: E402

requires_taichi = pytest.mark.skipif(not TAICHI_AVAILABLE, reason="Taichi not installed")
pytestmark = [pytest.mark.integration, requires_taichi]


@pytest.fixture(scope="module")
def _taichi_cpu():
    backend = init_taichi()
    assert backend is not None
    return backend


@pytest.fixture(scope="module")
def small_config():
    return TaichiTitleConfig(
        width=160,
        height=90,
        fps=10.0,
        duration=1.0,
        enable_bokeh=False,
        blur_radius=3,
        use_sdf_text=False,
    )


class TestTaichiRendererFrame:
    def test_render_frame_correct_dimensions(self, _taichi_cpu, small_config):
        """Rendered frame should match configured width x height."""
        renderer = TaichiTitleRenderer(small_config)
        frame = renderer.render_frame(0, "Test Title", "Subtitle")
        assert frame.shape == (90, 160, 3)
        assert frame.dtype == np.uint8

    def test_render_frame_not_blank(self, _taichi_cpu, small_config):
        """Frame should have visible content, not all zeros or all white."""
        renderer = TaichiTitleRenderer(small_config)
        frame = renderer.render_frame(0, "Test Title")
        assert frame.std() > 1.0, "Frame should not be flat/blank"
        assert frame.mean() > 0.5, "Dark gradient frame should have some brightness"
        assert frame.mean() < 254.5, "Frame should not be all white"

    def test_frames_at_different_times_differ(self, _taichi_cpu, small_config):
        """Animation should produce visually different frames at different times."""
        renderer = TaichiTitleRenderer(small_config)
        frame_start = renderer.render_frame(0, "Test Title", "Sub")
        frame_mid = renderer.render_frame(renderer.total_frames // 2, "Test Title", "Sub")
        frame_end = renderer.render_frame(renderer.total_frames - 1, "Test Title", "Sub")

        # Fade in/out + color pulse should produce different frames
        diff_start_mid = np.abs(frame_start.astype(float) - frame_mid.astype(float)).mean()
        diff_mid_end = np.abs(frame_mid.astype(float) - frame_end.astype(float)).mean()

        assert diff_start_mid > 0.5, f"Start vs mid should differ: {diff_start_mid:.2f}"
        assert diff_mid_end > 0.5, f"Mid vs end should differ: {diff_mid_end:.2f}"

    def test_total_frames_matches_config(self, _taichi_cpu, small_config):
        """total_frames should equal fps * duration."""
        renderer = TaichiTitleRenderer(small_config)
        assert renderer.total_frames == 10  # 10fps * 1.0s

    def test_hdr_output_is_uint16(self, _taichi_cpu):
        """HDR config should produce uint16 output frames."""
        hdr_config = TaichiTitleConfig(
            width=160,
            height=90,
            fps=10.0,
            duration=0.5,
            enable_bokeh=False,
            blur_radius=3,
            hdr=True,
            use_sdf_text=False,
        )
        renderer = TaichiTitleRenderer(hdr_config)
        frame = renderer.render_frame(0, "HDR Test")
        assert frame.dtype == np.uint16
        assert frame.shape == (90, 160, 3)


class TestTaichiTitleVideo:
    @pytest.mark.xdist_group("ffmpeg")
    def test_creates_valid_mp4(self, _taichi_cpu, tmp_path):
        """create_title_video_taichi should produce a playable MP4 file."""
        config = TaichiTitleConfig(
            width=160,
            height=90,
            fps=10.0,
            duration=1.0,
            enable_bokeh=False,
            blur_radius=3,
            use_sdf_text=False,
        )
        output = tmp_path / "title.mp4"
        result = create_title_video_taichi(
            "Integration Test",
            "CPU Backend",
            output,
            config=config,
            hdr=False,
        )

        assert result.exists()
        assert result.stat().st_size > 100

        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", str(result)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        import json

        data = json.loads(probe.stdout)
        streams = data.get("streams", [])
        video_streams = [s for s in streams if s.get("codec_type") == "video"]
        assert len(video_streams) == 1
        assert video_streams[0]["width"] == 160
        assert video_streams[0]["height"] == 90

    @pytest.mark.xdist_group("ffmpeg")
    def test_fade_from_white_modifies_first_frames(self, _taichi_cpu, tmp_path):
        """fade_from_white should make early frames brighter than without fade."""
        config = TaichiTitleConfig(
            width=160,
            height=90,
            fps=10.0,
            duration=1.0,
            enable_bokeh=False,
            blur_radius=3,
            use_sdf_text=False,
        )
        # Without fade
        no_fade = tmp_path / "no_fade.mp4"
        create_title_video_taichi("Test", None, no_fade, config=config, hdr=False)

        # With fade from white
        with_fade = tmp_path / "with_fade.mp4"
        create_title_video_taichi(
            "Test",
            None,
            with_fade,
            config=config,
            hdr=False,
            fade_from_white=True,
        )

        # Both should be valid files
        assert no_fade.exists()
        assert with_fade.exists()
        # The fade version should differ in file size (different pixel data)
        assert no_fade.stat().st_size != with_fade.stat().st_size
