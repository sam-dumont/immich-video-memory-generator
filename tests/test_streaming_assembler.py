"""Unit tests for streaming assembler components."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import pytest


def _has_ffmpeg() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5)  # noqa: S603, S607
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


requires_ffmpeg = pytest.mark.skipif(not _has_ffmpeg(), reason="FFmpeg not available")


@requires_ffmpeg
class TestFrameDecoder:
    def test_yields_frames_with_correct_shape(self, tmp_path: object) -> None:
        """FrameDecoder should yield numpy arrays of (height, width, 3)."""
        from pathlib import Path

        from immich_memories.processing.streaming_assembler import FrameDecoder

        tmp = Path(str(tmp_path))

        # Create a tiny test clip via FFmpeg
        clip = tmp / "test.mp4"
        subprocess.run(  # noqa: S603, S607
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=320x240:rate=10:duration=0.5",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                str(clip),
            ],
            check=True,
            capture_output=True,
            timeout=10,
        )

        decoder = FrameDecoder(clip, width=320, height=240, fps=10)
        frames = list(decoder)

        assert len(frames) >= 4  # 0.5s * 10fps = 5 frames (allow off-by-one)
        for frame in frames:
            assert frame.shape == (240, 320, 3)
            assert frame.dtype == np.uint8


@requires_ffmpeg
class TestStreamingEncoder:
    def test_encodes_frames_to_valid_mp4(self, tmp_path: object) -> None:
        """StreamingEncoder should produce a valid MP4 from numpy frames."""
        from pathlib import Path

        from immich_memories.processing.streaming_assembler import StreamingEncoder

        tmp = Path(str(tmp_path))
        output = tmp / "test_output.mp4"
        width, height, fps = 320, 240, 10
        n_frames = 10

        encoder = StreamingEncoder(
            output,
            width,
            height,
            fps,
            encoder_args=[
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-crf",
                "28",
                "-pix_fmt",
                "yuv420p",
            ],
        )
        encoder.start()
        for i in range(n_frames):
            # Gradient frame — different each frame for visual verification
            frame = np.full((height, width, 3), fill_value=i * 25, dtype=np.uint8)
            encoder.write_frame(frame)
        encoder.finish()

        assert output.exists()
        assert output.stat().st_size > 0

        # Verify with ffprobe
        probe = json.loads(
            subprocess.run(  # noqa: S603, S607
                [
                    "ffprobe",
                    "-v",
                    "quiet",
                    "-print_format",
                    "json",
                    "-show_format",
                    "-show_streams",
                    str(output),
                ],
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout
        )
        video_streams = [s for s in probe["streams"] if s["codec_type"] == "video"]
        assert len(video_streams) == 1
        assert float(probe["format"]["duration"]) > 0.5


class TestFrameBlender:
    def test_crossfade_blend_produces_interpolated_frames(self) -> None:
        """Blending two frames at alpha=0.5 should average pixel values."""
        from immich_memories.processing.streaming_assembler import blend_crossfade

        frame_a = np.full((4, 4, 3), 100, dtype=np.uint8)
        frame_b = np.full((4, 4, 3), 200, dtype=np.uint8)
        out = np.zeros_like(frame_a)
        temp = np.zeros_like(frame_a)

        blend_crossfade(frame_a, frame_b, alpha=0.5, out=out, temp=temp)

        # (100 * 0.5 + 200 * 0.5) = 150
        assert np.all(out == 150)

    def test_crossfade_alpha_zero_is_frame_a(self) -> None:
        """Alpha=0 should return frame_a unchanged."""
        from immich_memories.processing.streaming_assembler import blend_crossfade

        frame_a = np.full((4, 4, 3), 100, dtype=np.uint8)
        frame_b = np.full((4, 4, 3), 200, dtype=np.uint8)
        out = np.zeros_like(frame_a)
        temp = np.zeros_like(frame_a)

        blend_crossfade(frame_a, frame_b, alpha=0.0, out=out, temp=temp)
        assert np.all(out == 100)

    def test_crossfade_alpha_one_is_frame_b(self) -> None:
        """Alpha=1 should return frame_b unchanged."""
        from immich_memories.processing.streaming_assembler import blend_crossfade

        frame_a = np.full((4, 4, 3), 100, dtype=np.uint8)
        frame_b = np.full((4, 4, 3), 200, dtype=np.uint8)
        out = np.zeros_like(frame_a)
        temp = np.zeros_like(frame_a)

        blend_crossfade(frame_a, frame_b, alpha=1.0, out=out, temp=temp)
        assert np.all(out == 200)


@requires_ffmpeg
class TestStreamingAssemble:
    def test_assembles_two_clips_with_crossfade(self, tmp_path: object) -> None:
        """assemble_streaming should produce valid output from two clips."""
        from pathlib import Path

        from immich_memories.processing.assembly_config import AssemblyClip
        from immich_memories.processing.streaming_assembler import assemble_streaming

        tmp = Path(str(tmp_path))

        # Generate two tiny test clips with audio
        clips = []
        for i in range(2):
            p = tmp / f"clip_{i}.mp4"
            subprocess.run(  # noqa: S603, S607
                [
                    "ffmpeg",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    f"testsrc2=size=320x240:rate=10:duration=2:alpha={80 + i * 80}",
                    "-f",
                    "lavfi",
                    "-i",
                    f"sine=frequency={440 + i * 220}:duration=2",
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
                    str(p),
                ],
                check=True,
                capture_output=True,
                timeout=10,
            )
            clips.append(AssemblyClip(path=p, duration=2.0, asset_id=f"test-{i}"))

        output = tmp / "output.mp4"
        assemble_streaming(
            clips=clips,
            transitions=["fade"],
            output_path=output,
            width=320,
            height=240,
            fps=10,
            fade_duration=0.3,
            encoder_args=[
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-crf",
                "28",
                "-pix_fmt",
                "yuv420p",
            ],
        )

        assert output.exists()
        # Duration should be ~3.7s (2+2-0.3 crossfade)
        probe = json.loads(
            subprocess.run(  # noqa: S603, S607
                [
                    "ffprobe",
                    "-v",
                    "quiet",
                    "-print_format",
                    "json",
                    "-show_format",
                    str(output),
                ],
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout
        )
        duration = float(probe["format"]["duration"])
        assert 3.0 < duration < 4.5

    def test_assembles_with_cut_transition(self, tmp_path: object) -> None:
        """Cut transitions should concatenate without blending."""
        from pathlib import Path

        from immich_memories.processing.assembly_config import AssemblyClip
        from immich_memories.processing.streaming_assembler import assemble_streaming

        tmp = Path(str(tmp_path))

        clips = []
        for i in range(2):
            p = tmp / f"clip_{i}.mp4"
            subprocess.run(  # noqa: S603, S607
                [
                    "ffmpeg",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc2=size=320x240:rate=10:duration=1",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "ultrafast",
                    str(p),
                ],
                check=True,
                capture_output=True,
                timeout=10,
            )
            clips.append(AssemblyClip(path=p, duration=1.0, asset_id=f"test-{i}"))

        output = tmp / "output_cut.mp4"
        assemble_streaming(
            clips=clips,
            transitions=["cut"],
            output_path=output,
            width=320,
            height=240,
            fps=10,
            fade_duration=0.3,
            encoder_args=[
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-crf",
                "28",
                "-pix_fmt",
                "yuv420p",
            ],
        )

        assert output.exists()
        probe = json.loads(
            subprocess.run(  # noqa: S603, S607
                [
                    "ffprobe",
                    "-v",
                    "quiet",
                    "-print_format",
                    "json",
                    "-show_format",
                    str(output),
                ],
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout
        )
        duration = float(probe["format"]["duration"])
        # Two 1s clips with cut = ~2s (no overlap)
        assert 1.5 < duration < 2.5


@requires_ffmpeg
class TestAudioHandling:
    def test_extract_and_mix_audio(self, tmp_path: object) -> None:
        """extract_and_mix_audio should produce a valid audio file with crossfade."""
        from pathlib import Path

        from immich_memories.processing.assembly_config import AssemblyClip
        from immich_memories.processing.streaming_assembler import extract_and_mix_audio

        tmp = Path(str(tmp_path))

        # Create clips with audio
        clips = []
        for i in range(2):
            p = tmp / f"clip_{i}.mp4"
            subprocess.run(  # noqa: S603, S607
                [
                    "ffmpeg",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc2=size=320x240:rate=10:duration=2",
                    "-f",
                    "lavfi",
                    "-i",
                    f"sine=frequency={440 + i * 220}:duration=2",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "ultrafast",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "64k",
                    "-shortest",
                    str(p),
                ],
                check=True,
                capture_output=True,
                timeout=10,
            )
            clips.append(AssemblyClip(path=p, duration=2.0, asset_id=f"test-{i}"))

        audio_out = tmp / "mixed_audio.m4a"
        extract_and_mix_audio(
            clips=clips,
            transitions=["fade"],
            output_path=audio_out,
            fade_duration=0.3,
        )

        assert audio_out.exists()
        probe = json.loads(
            subprocess.run(  # noqa: S603, S607
                [
                    "ffprobe",
                    "-v",
                    "quiet",
                    "-print_format",
                    "json",
                    "-show_format",
                    "-show_streams",
                    str(audio_out),
                ],
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout
        )
        audio_streams = [s for s in probe["streams"] if s["codec_type"] == "audio"]
        assert len(audio_streams) >= 1


@requires_ffmpeg
class TestFullStreamingPipeline:
    def test_full_pipeline_produces_video_with_audio(self, tmp_path: object) -> None:
        """Full streaming pipeline should produce MP4 with both video and audio."""
        from pathlib import Path

        from immich_memories.processing.assembly_config import AssemblyClip
        from immich_memories.processing.streaming_assembler import streaming_assemble_full

        tmp = Path(str(tmp_path))

        clips = []
        for i in range(3):
            p = tmp / f"clip_{i}.mp4"
            subprocess.run(  # noqa: S603, S607
                [
                    "ffmpeg",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    f"testsrc2=size=320x240:rate=10:duration=2:alpha={60 + i * 60}",
                    "-f",
                    "lavfi",
                    "-i",
                    f"sine=frequency={330 + i * 110}:duration=2",
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
                    str(p),
                ],
                check=True,
                capture_output=True,
                timeout=10,
            )
            clips.append(AssemblyClip(path=p, duration=2.0, asset_id=f"test-{i}"))

        output = tmp / "final.mp4"
        streaming_assemble_full(
            clips=clips,
            transitions=["fade", "cut"],
            output_path=output,
            width=320,
            height=240,
            fps=10,
            fade_duration=0.3,
            encoder_args=[
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-crf",
                "28",
                "-pix_fmt",
                "yuv420p",
            ],
        )

        assert output.exists()
        probe = json.loads(
            subprocess.run(  # noqa: S603, S607
                [
                    "ffprobe",
                    "-v",
                    "quiet",
                    "-print_format",
                    "json",
                    "-show_format",
                    "-show_streams",
                    str(output),
                ],
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout
        )

        stream_types = {s["codec_type"] for s in probe["streams"]}
        assert "video" in stream_types
        assert "audio" in stream_types
        assert float(probe["format"]["duration"]) > 3.0


# ---------------------------------------------------------------------------
# Regression tests — verify feature parity with old filter graph pipeline.
# These would have caught the gaps in the initial streaming migration.
# ---------------------------------------------------------------------------


class TestFrameDecoderFilterChain:
    """Verify FrameDecoder builds the correct FFmpeg filter chain."""

    def test_default_filter_includes_pts_and_timebase(self) -> None:
        """PTS reset and timebase are critical for multi-clip concat."""
        from pathlib import Path

        from immich_memories.processing.streaming_assembler import FrameDecoder

        decoder = FrameDecoder(Path("/fake.mp4"), width=1920, height=1080, fps=30)
        vf = decoder._build_vf()

        assert "setpts=PTS-STARTPTS" in vf
        assert "settb=1/30" in vf
        assert "setsar=1" in vf

    def test_rotation_90_includes_transpose(self) -> None:
        """90° rotation must apply transpose=1 before scale."""
        from pathlib import Path

        from immich_memories.processing.streaming_assembler import FrameDecoder

        decoder = FrameDecoder(Path("/fake.mp4"), width=1920, height=1080, fps=30, rotation=90)
        vf = decoder._build_vf()

        assert "transpose=1" in vf
        # Transpose must come before scale
        assert vf.index("transpose=1") < vf.index("scale=")

    def test_rotation_180_includes_hflip_vflip(self) -> None:
        from pathlib import Path

        from immich_memories.processing.streaming_assembler import FrameDecoder

        decoder = FrameDecoder(Path("/fake.mp4"), width=1920, height=1080, fps=30, rotation=180)
        vf = decoder._build_vf()
        assert "hflip,vflip" in vf

    def test_rotation_270_includes_transpose_2(self) -> None:
        from pathlib import Path

        from immich_memories.processing.streaming_assembler import FrameDecoder

        decoder = FrameDecoder(Path("/fake.mp4"), width=1920, height=1080, fps=30, rotation=270)
        vf = decoder._build_vf()
        assert "transpose=2" in vf

    def test_privacy_blur_includes_gblur(self) -> None:
        """Privacy mode must apply heavy gaussian blur."""
        from pathlib import Path

        from immich_memories.processing.streaming_assembler import FrameDecoder

        decoder = FrameDecoder(
            Path("/fake.mp4"), width=1920, height=1080, fps=30, privacy_blur=True
        )
        vf = decoder._build_vf()
        assert "gblur=sigma=80" in vf

    def test_hdr_not_in_decoder(self) -> None:
        """HDR conversion must NOT be in the decoder — it happens on the encoder side.

        Applying format=p010le in the decoder would do HLG→p010le→rgb24
        (lossy tone-map), then the encoder tags SDR data as HLG = yellow tint.
        """
        from pathlib import Path

        from immich_memories.processing.streaming_assembler import FrameDecoder

        decoder = FrameDecoder(
            Path("/fake.mp4"),
            width=1920,
            height=1080,
            fps=30,
            hdr_conversion="zscale=t=arib-std-b67:tin=smpte2084",
            colorspace_filter=",setparams=colorspace=bt2020nc",
            output_pix_fmt="p010le",
        )
        vf = decoder._build_vf()

        # HDR filters must NOT be in the decoder filter chain
        assert "format=p010le" not in vf
        assert "zscale" not in vf
        assert "setparams" not in vf

    def test_no_rotation_when_zero(self) -> None:
        """rotation=0 should NOT add any transpose filter."""
        from pathlib import Path

        from immich_memories.processing.streaming_assembler import FrameDecoder

        decoder = FrameDecoder(Path("/fake.mp4"), width=1920, height=1080, fps=30, rotation=0)
        vf = decoder._build_vf()
        assert "transpose" not in vf
        assert "hflip" not in vf


class TestAudioFilterChain:
    """Verify audio filter graph includes loudnorm and privacy muffle."""

    def test_loudnorm_included_when_normalize_true(self) -> None:
        from immich_memories.processing.assembly_config import AssemblyClip
        from immich_memories.processing.streaming_assembler import _build_audio_filter_graph

        clips = [
            AssemblyClip(path=Path("/a.mp4"), duration=3.0),
            AssemblyClip(path=Path("/b.mp4"), duration=3.0),
        ]
        graph = _build_audio_filter_graph(clips, ["fade"], 0.5, normalize_audio=True)
        assert "loudnorm=I=-16:TP=-1.5:LRA=11" in graph

    def test_loudnorm_excluded_when_normalize_false(self) -> None:
        from immich_memories.processing.assembly_config import AssemblyClip
        from immich_memories.processing.streaming_assembler import _build_audio_filter_graph

        clips = [
            AssemblyClip(path=Path("/a.mp4"), duration=3.0),
            AssemblyClip(path=Path("/b.mp4"), duration=3.0),
        ]
        graph = _build_audio_filter_graph(clips, ["fade"], 0.5, normalize_audio=False)
        assert "loudnorm" not in graph

    def test_privacy_muffle_included(self) -> None:
        from immich_memories.processing.assembly_config import AssemblyClip
        from immich_memories.processing.streaming_assembler import _build_audio_filter_graph

        clips = [
            AssemblyClip(path=Path("/a.mp4"), duration=3.0),
            AssemblyClip(path=Path("/b.mp4"), duration=3.0),
        ]
        graph = _build_audio_filter_graph(clips, ["fade"], 0.5, privacy_mode=True)
        assert "lowpass=f=200" in graph

    def test_title_screen_gets_null_audio(self) -> None:
        from immich_memories.processing.assembly_config import AssemblyClip
        from immich_memories.processing.streaming_assembler import _build_audio_filter_graph

        clips = [
            AssemblyClip(path=Path("/title.mp4"), duration=3.0, is_title_screen=True),
            AssemblyClip(path=Path("/b.mp4"), duration=3.0),
        ]
        graph = _build_audio_filter_graph(clips, ["fade"], 0.5)
        assert "anullsrc" in graph

    def test_loudnorm_not_applied_to_title_screens(self) -> None:
        from immich_memories.processing.assembly_config import AssemblyClip
        from immich_memories.processing.streaming_assembler import _build_audio_filter_graph

        clips = [
            AssemblyClip(path=Path("/title.mp4"), duration=3.0, is_title_screen=True),
            AssemblyClip(path=Path("/b.mp4"), duration=3.0),
        ]
        graph = _build_audio_filter_graph(clips, ["fade"], 0.5, normalize_audio=True)
        # Title screen (a0) should use anullsrc, not loudnorm
        # Content clip (a1) should have loudnorm
        parts = graph.split(";")
        title_part = [p for p in parts if "[a0]" in p][0]
        content_part = [p for p in parts if "[a1]" in p][0]
        assert "loudnorm" not in title_part
        assert "loudnorm" in content_part


class TestMakeDecoderIntegration:
    """Verify _make_decoder wires clip metadata to FrameDecoder correctly."""

    def test_rotation_override_passed_through(self) -> None:
        from immich_memories.processing.assembly_config import AssemblyClip
        from immich_memories.processing.streaming_assembler import _make_decoder

        clip = AssemblyClip(path=Path("/clip.mp4"), duration=5.0, rotation_override=90)
        decoder = _make_decoder(clip, 0, 1920, 1080, 30)

        assert decoder._rotation == 90
        assert "transpose=1" in decoder._build_vf()

    def test_privacy_mode_applied_to_non_title(self) -> None:
        from immich_memories.processing.assembly_config import AssemblyClip
        from immich_memories.processing.streaming_assembler import _make_decoder

        clip = AssemblyClip(path=Path("/clip.mp4"), duration=5.0)
        decoder = _make_decoder(clip, 0, 1920, 1080, 30, privacy_mode=True)

        assert decoder._privacy_blur is True
        assert "gblur=sigma=80" in decoder._build_vf()

    def test_privacy_mode_not_applied_to_title_screen(self) -> None:
        from immich_memories.processing.assembly_config import AssemblyClip
        from immich_memories.processing.streaming_assembler import _make_decoder

        clip = AssemblyClip(path=Path("/title.mp4"), duration=3.0, is_title_screen=True)
        decoder = _make_decoder(clip, 0, 1920, 1080, 30, privacy_mode=True)

        assert decoder._privacy_blur is False
        assert "gblur" not in decoder._build_vf()
