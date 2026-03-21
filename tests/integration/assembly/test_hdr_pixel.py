"""Integration tests for HDR color preservation through the streaming assembler.

Verifies at the PIXEL LEVEL that:
1. HDR TV-range values survive the rawvideo pipe (no full-range crush)
2. SDR clips in HDR output don't get a red tint (sRGB→HLG conversion works)
3. Output metadata correctly signals HLG to downstream players

Uses small synthetic clips (320x240, 1 second, 10 fps) for speed.
Run with: make test-integration-assembly
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest

from tests.integration.conftest import requires_ffmpeg

pytestmark = [pytest.mark.integration, requires_ffmpeg]


# ---------------------------------------------------------------------------
# Skip guard: libx265 + zscale are required for HDR encode/conversion
# ---------------------------------------------------------------------------


def _has_libx265() -> bool:
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return "libx265" in result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _has_zscale() -> bool:
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-filters"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return "zscale" in result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


requires_libx265 = pytest.mark.skipif(not _has_libx265(), reason="libx265 not available")
requires_zscale = pytest.mark.skipif(not _has_zscale(), reason="zscale filter not available")

# Small resolution for fast tests
WIDTH, HEIGHT, FPS, DURATION = 320, 240, 10, 1

HLG_ENCODER_ARGS = [
    "-c:v",
    "libx265",
    "-crf",
    "18",
    "-preset",
    "ultrafast",
    "-pix_fmt",
    "yuv420p10le",
    "-colorspace",
    "bt2020nc",
    "-color_primaries",
    "bt2020",
    "-color_trc",
    "arib-std-b67",
    "-x265-params",
    "hdr-opt=1:repeat-headers=1:colorprim=bt2020:transfer=arib-std-b67:colormatrix=bt2020nc",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class _FakeClip:
    """Minimal clip object matching what assemble_streaming expects."""

    path: Path
    duration: float
    is_title_screen: bool = False
    rotation_override: int | None = None
    asset_id: str = ""


def _make_hlg_clip(out: Path) -> Path:
    """Create a synthetic HLG clip with mid-gray (10-bit TV range ~512)."""
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=0x808080:s={WIDTH}x{HEIGHT}:r={FPS}:d={DURATION},format=yuv420p10le",
            "-c:v",
            "libx265",
            "-preset",
            "ultrafast",
            "-crf",
            "1",
            "-pix_fmt",
            "yuv420p10le",
            "-colorspace",
            "bt2020nc",
            "-color_primaries",
            "bt2020",
            "-color_trc",
            "arib-std-b67",
            "-x265-params",
            "hdr-opt=1:repeat-headers=1:colorprim=bt2020:transfer=arib-std-b67:colormatrix=bt2020nc",
            str(out),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    return out


def _make_sdr_clip(out: Path) -> Path:
    """Create a synthetic SDR clip with color bars (yuv420p, bt709)."""
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size={WIDTH}x{HEIGHT}:rate={FPS}:duration={DURATION}",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "1",
            str(out),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    return out


def _extract_first_frame_rgb(video_path: Path) -> np.ndarray:
    """Extract the first frame as raw RGB24 pixels via FFmpeg."""
    result = subprocess.run(
        [
            "ffmpeg",
            "-i",
            str(video_path),
            "-vf",
            "select=eq(n\\,0)",
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
    return np.frombuffer(result.stdout, dtype=np.uint8).reshape(HEIGHT, WIDTH, 3)


def _extract_first_frame_yuv10(video_path: Path) -> dict[str, np.ndarray]:
    """Extract the first frame as raw yuv420p10le and split into Y, U, V planes.

    Returns dict with 'y' (H, W), 'u' (H/2, W/2), 'v' (H/2, W/2) as uint16.
    """
    result = subprocess.run(
        [
            "ffmpeg",
            "-i",
            str(video_path),
            "-vf",
            "select=eq(n\\,0)",
            "-vframes",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "yuv420p10le",
            "pipe:1",
        ],
        capture_output=True,
        timeout=15,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode(errors="replace")
        raise RuntimeError(f"YUV extraction failed: {stderr[-300:]}")

    raw = np.frombuffer(result.stdout, dtype=np.uint16)
    y_size = WIDTH * HEIGHT
    uv_size = (WIDTH // 2) * (HEIGHT // 2)

    y_plane = raw[:y_size].reshape(HEIGHT, WIDTH)
    u_plane = raw[y_size : y_size + uv_size].reshape(HEIGHT // 2, WIDTH // 2)
    v_plane = raw[y_size + uv_size : y_size + 2 * uv_size].reshape(HEIGHT // 2, WIDTH // 2)
    return {"y": y_plane, "u": u_plane, "v": v_plane}


def _ffprobe_video_stream(path: Path) -> dict:
    """Get the first video stream metadata via ffprobe."""
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_streams",
            "-select_streams",
            "v:0",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    data = json.loads(result.stdout)
    streams = data.get("streams", [])
    if not streams:
        raise ValueError(f"No video stream in {path}")
    return streams[0]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@requires_libx265
class TestHDRPassthrough:
    """Verify HDR pixel values survive the rawvideo pipe without range crush."""

    def test_hdr_passthrough_preserves_color_range(self, tmp_path: Path) -> None:
        """Mid-gray HLG clip → streaming assembler → output Y luma should stay in TV range.

        10-bit TV range mid-gray: Y ≈ 502 (64 + (940-64)/2).
        If range metadata is lost, the encoder interprets TV-range data as full-range,
        crushing values — Y would shift significantly from the source.
        """
        from immich_memories.processing.streaming_assembler import assemble_streaming

        source = _make_hlg_clip(tmp_path / "hlg_source.mp4")

        # Extract source Y values as our ground truth
        source_yuv = _extract_first_frame_yuv10(source)
        source_y_center = int(source_yuv["y"][HEIGHT // 2, WIDTH // 2])

        output = tmp_path / "hlg_passthrough.mp4"
        clip = _FakeClip(path=source, duration=float(DURATION))

        assemble_streaming(
            clips=[clip],
            transitions=[],
            output_path=output,
            width=WIDTH,
            height=HEIGHT,
            fps=FPS,
            fade_duration=0.0,
            encoder_args=HLG_ENCODER_ARGS,
            hdr_type="hlg",
            scale_mode="black",
        )

        assert output.exists(), "Output file was not created"

        # Extract output Y values
        output_yuv = _extract_first_frame_yuv10(output)
        output_y_center = int(output_yuv["y"][HEIGHT // 2, WIDTH // 2])

        # WHY: ±40 tolerance accounts for double encode quantization (source CRF 1
        # → decode → rawvideo pipe → re-encode CRF 18). Normal drift is ~25-30.
        # If color range is lost (TV→full misinterpretation), the shift would be
        # 80+ values — well outside this tolerance.
        assert abs(output_y_center - source_y_center) <= 40, (
            f"Y luma drifted too far: source={source_y_center}, output={output_y_center}. "
            f"Likely color range metadata was lost in the rawvideo pipe."
        )

        # Also verify the value is in TV range (64-940 for 10-bit),
        # not near 0 or 1023 which would indicate range crush
        assert 200 < output_y_center < 800, (
            f"Y luma {output_y_center} is outside expected TV range for mid-gray. "
            f"Expected ~502 (10-bit TV mid-gray)."
        )

    def test_hdr_chroma_preserved(self, tmp_path: Path) -> None:
        """Chroma (U, V) should stay near neutral (512) for gray input.

        Gray has no color → U and V should be at the neutral point (~512 for 10-bit).
        If color range is wrong, chroma shifts cause color cast.
        """
        from immich_memories.processing.streaming_assembler import assemble_streaming

        source = _make_hlg_clip(tmp_path / "hlg_chroma_src.mp4")
        output = tmp_path / "hlg_chroma_out.mp4"
        clip = _FakeClip(path=source, duration=float(DURATION))

        assemble_streaming(
            clips=[clip],
            transitions=[],
            output_path=output,
            width=WIDTH,
            height=HEIGHT,
            fps=FPS,
            fade_duration=0.0,
            encoder_args=HLG_ENCODER_ARGS,
            hdr_type="hlg",
            scale_mode="black",
        )

        output_yuv = _extract_first_frame_yuv10(output)
        u_center = int(output_yuv["u"][HEIGHT // 4, WIDTH // 4])
        v_center = int(output_yuv["v"][HEIGHT // 4, WIDTH // 4])

        # Neutral chroma for 10-bit: 512. Allow ±20 for encode quantization.
        assert abs(u_center - 512) <= 20, (
            f"U chroma drifted from neutral: {u_center} (expected ~512). Color cast detected."
        )
        assert abs(v_center - 512) <= 20, (
            f"V chroma drifted from neutral: {v_center} (expected ~512). Color cast detected."
        )


@requires_libx265
@requires_zscale
class TestSDRInHDROutput:
    """Verify SDR clips converted to HLG don't get a red/wrong tint."""

    def test_sdr_clip_in_hdr_output_not_red_tinted(self, tmp_path: Path) -> None:
        """SDR clip through HDR pipeline should produce reasonable colors, not red-shifted.

        The bug: SDR full-range data piped as yuv420p10le without sRGB→HLG conversion
        gets interpreted as TV-range HLG = red tint (V channel way above neutral).
        """
        from immich_memories.processing.streaming_assembler import assemble_streaming

        source = _make_sdr_clip(tmp_path / "sdr_source.mp4")
        output = tmp_path / "sdr_in_hlg.mp4"
        clip = _FakeClip(path=source, duration=float(DURATION))

        assemble_streaming(
            clips=[clip],
            transitions=[],
            output_path=output,
            width=WIDTH,
            height=HEIGHT,
            fps=FPS,
            fade_duration=0.0,
            encoder_args=HLG_ENCODER_ARGS,
            hdr_type="hlg",
            scale_mode="black",
        )

        assert output.exists()

        # Extract as RGB to check for red tint directly
        frame = _extract_first_frame_rgb(output)
        center = frame[HEIGHT // 2, WIDTH // 2]
        r, g, b = int(center[0]), int(center[1]), int(center[2])

        # WHY: Red tint means R channel is way higher than G and B.
        # For testsrc2 center area, colors should be roughly balanced.
        # A 40+ point R bias over both G and B = red tint regression.
        r_bias_over_g = r - g
        r_bias_over_b = r - b

        assert not (r_bias_over_g > 40 and r_bias_over_b > 40), (
            f"Red tint detected: RGB=({r}, {g}, {b}). "
            f"R exceeds G by {r_bias_over_g} and B by {r_bias_over_b}. "
            f"SDR→HLG conversion likely missing or broken."
        )

    def test_sdr_in_hdr_output_not_washed_out(self, tmp_path: Path) -> None:
        """SDR clip in HDR output should retain contrast, not be washed out.

        If range conversion is wrong, all values compress into a narrow band
        (low contrast / washed out).
        """
        from immich_memories.processing.streaming_assembler import assemble_streaming

        source = _make_sdr_clip(tmp_path / "sdr_contrast_src.mp4")
        output = tmp_path / "sdr_contrast_hlg.mp4"
        clip = _FakeClip(path=source, duration=float(DURATION))

        assemble_streaming(
            clips=[clip],
            transitions=[],
            output_path=output,
            width=WIDTH,
            height=HEIGHT,
            fps=FPS,
            fade_duration=0.0,
            encoder_args=HLG_ENCODER_ARGS,
            hdr_type="hlg",
            scale_mode="black",
        )

        # Extract YUV to check luma range across the frame
        output_yuv = _extract_first_frame_yuv10(output)
        y_plane = output_yuv["y"]
        y_min, y_max = int(y_plane.min()), int(y_plane.max())
        y_range = y_max - y_min

        # testsrc2 has high contrast (white text, colored bars, dark areas).
        # After SDR→HLG conversion, we expect a reasonable dynamic range.
        # Washed out = range < 100 (everything compressed near mid-gray).
        assert y_range > 100, (
            f"Output looks washed out: Y range is only {y_range} "
            f"(min={y_min}, max={y_max}). Expected >100 for testsrc2 content."
        )


@requires_libx265
class TestHDROutputMetadata:
    """Verify output file has correct HLG color metadata for player compatibility."""

    def test_hdr_output_metadata_correct(self, tmp_path: Path) -> None:
        """Output should signal HLG via color_transfer, color_primaries, and colorspace."""
        from immich_memories.processing.streaming_assembler import assemble_streaming

        source = _make_hlg_clip(tmp_path / "hlg_meta_src.mp4")
        output = tmp_path / "hlg_metadata.mp4"
        clip = _FakeClip(path=source, duration=float(DURATION))

        assemble_streaming(
            clips=[clip],
            transitions=[],
            output_path=output,
            width=WIDTH,
            height=HEIGHT,
            fps=FPS,
            fade_duration=0.0,
            encoder_args=HLG_ENCODER_ARGS,
            hdr_type="hlg",
            scale_mode="black",
        )

        stream = _ffprobe_video_stream(output)

        # pix_fmt: must be 10-bit
        pix_fmt = stream.get("pix_fmt", "")
        assert pix_fmt in ("yuv420p10le", "p010le", "yuv420p10"), (
            f"Expected 10-bit pixel format, got '{pix_fmt}'"
        )

        # color_transfer: HLG = arib-std-b67
        color_trc = stream.get("color_transfer", "")
        assert color_trc == "arib-std-b67", (
            f"Expected color_transfer='arib-std-b67' (HLG), got '{color_trc}'"
        )

        # color_primaries: bt2020
        color_primaries = stream.get("color_primaries", "")
        assert color_primaries == "bt2020", (
            f"Expected color_primaries='bt2020', got '{color_primaries}'"
        )

        # colorspace: bt2020nc
        colorspace = stream.get("color_space", "")
        assert colorspace == "bt2020nc", f"Expected color_space='bt2020nc', got '{colorspace}'"

    def test_hdr_output_color_range_tagged_tv(self, tmp_path: Path) -> None:
        """Output should be tagged as TV (limited) range, not full/PC range."""
        from immich_memories.processing.streaming_assembler import assemble_streaming

        source = _make_hlg_clip(tmp_path / "hlg_range_src.mp4")
        output = tmp_path / "hlg_range_out.mp4"
        clip = _FakeClip(path=source, duration=float(DURATION))

        assemble_streaming(
            clips=[clip],
            transitions=[],
            output_path=output,
            width=WIDTH,
            height=HEIGHT,
            fps=FPS,
            fade_duration=0.0,
            encoder_args=HLG_ENCODER_ARGS,
            hdr_type="hlg",
            scale_mode="black",
        )

        stream = _ffprobe_video_stream(output)

        # WHY: x265 with proper color metadata should tag output as TV/limited range.
        # If the rawvideo input doesn't declare -color_range tv, the encoder may
        # tag output as full range (pc) which confuses HDR-capable displays.
        color_range = stream.get("color_range", "")
        assert color_range in ("tv", "limited"), (
            f"Expected color_range='tv' (limited), got '{color_range}'. "
            f"Full range tagging on HDR content causes display issues."
        )
