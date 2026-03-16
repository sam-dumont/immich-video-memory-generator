"""Frame-level transition blending with optional GPU acceleration."""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from immich_memories.processing.assembly_config import AssemblySettings
from immich_memories.processing.hdr_utilities import _detect_hdr_type

logger = logging.getLogger(__name__)


class TransitionBlender:
    """Renders crossfade transitions frame-by-frame using PyAV and optional Taichi GPU."""

    def __init__(self, settings: AssemblySettings) -> None:
        self.settings = settings

    def load_transition_frames(
        self,
        seg_a: Path,
        seg_b: Path,
        fade_dur: float,
        target_fps: int,
        pix_fmt: str,
    ):  # type: ignore[return]
        """Load frames from both segments for blending. Returns None on failure."""
        try:
            import av
            import numpy as np

            container_a = av.open(str(seg_a))
            container_b = av.open(str(seg_b))
            stream_a = container_a.streams.video[0]
            width, height = stream_a.width, stream_a.height

            total_frames = max(2, int(fade_dur * target_fps))
            frames_a = [f.to_ndarray(format=pix_fmt) for f in container_a.decode(stream_a)]
            frames_b = [
                f.to_ndarray(format=pix_fmt)
                for f in container_b.decode(container_b.streams.video[0])
            ]
            container_a.close()
            container_b.close()

            total_frames = min(len(frames_a), len(frames_b), total_frames)
            if total_frames < 2:
                return None

            indices_a = np.linspace(0, len(frames_a) - 1, total_frames, dtype=int)
            indices_b = np.linspace(0, len(frames_b) - 1, total_frames, dtype=int)
            return frames_a, frames_b, indices_a, indices_b, width, height, total_frames
        except Exception:
            return None

    def blend_and_encode_frames(
        self,
        frames_a,
        frames_b,
        indices_a,
        indices_b,
        total_frames: int,
        output_path: Path,
        target_fps: int,
        is_hdr: bool,
        width: int,
        height: int,
        use_gpu: bool,
        blend_frames_gpu_fn: Callable[..., Any] | None,
        pix_fmt: str,
    ) -> None:
        """Blend frames and encode to output video via PyAV."""
        import av
        import numpy as np

        dtype = np.uint16 if is_hdr else np.uint8
        output_container = av.open(str(output_path), mode="w")
        output_stream = self._configure_pyav_output_stream(
            output_container, target_fps, is_hdr, width, height, self.settings.output_crf
        )

        for i in range(total_frames):
            alpha = i / (total_frames - 1) if total_frames > 1 else 0.5
            frame_a, frame_b = frames_a[indices_a[i]], frames_b[indices_b[i]]

            if use_gpu and blend_frames_gpu_fn is not None:
                blended = blend_frames_gpu_fn(frame_a, frame_b, alpha)
            else:
                blended = (
                    (1.0 - alpha) * frame_a.astype(np.float32) + alpha * frame_b.astype(np.float32)
                ).astype(dtype)

            av_frame = av.VideoFrame.from_ndarray(blended, format=pix_fmt)
            av_frame.pts = i
            for packet in output_stream.encode(av_frame):
                output_container.mux(packet)

        for packet in output_stream.encode():
            output_container.mux(packet)
        output_container.close()

    def render_transition_framewise(
        self,
        seg_a: Path,
        seg_b: Path,
        output_path: Path,
        fade_dur: float,
        target_fps: int = 60,
    ) -> bool:
        """Render crossfade frame-by-frame. Returns True on success, False if fallback needed."""
        try:
            import av  # noqa: F401
        except ImportError:
            logger.debug("PyAV not available, falling back to xfade")
            return False

        use_gpu = False
        blend_frames_gpu_fn: Callable[..., Any] | None = None
        try:
            from immich_memories.processing.transition_blend import (
                blend_frames_gpu as _blend_frames_gpu,
            )
            from immich_memories.processing.transition_blend import (
                is_gpu_blending_available,
            )

            blend_frames_gpu_fn = _blend_frames_gpu
            use_gpu = is_gpu_blending_available()
        except Exception as e:
            logger.debug(f"GPU blending not available: {e}")

        is_hdr = _detect_hdr_type(seg_a) is not None
        pix_fmt = "rgb48le" if is_hdr else "rgb24"

        frame_data = self.load_transition_frames(seg_a, seg_b, fade_dur, target_fps, pix_fmt)
        if frame_data is None:
            return False

        frames_a, frames_b, indices_a, indices_b, width, height, total_frames = frame_data

        try:
            self.blend_and_encode_frames(
                frames_a,
                frames_b,
                indices_a,
                indices_b,
                total_frames,
                output_path,
                target_fps,
                is_hdr,
                width,
                height,
                use_gpu,
                blend_frames_gpu_fn,
                pix_fmt,
            )
            self.add_audio_crossfade(seg_a, seg_b, output_path, fade_dur)
            return True
        except Exception as e:
            logger.warning(f"Frame-by-frame transition failed: {e}")
            if output_path.exists():
                output_path.unlink()
            return False

    def add_audio_crossfade(
        self,
        seg_a: Path,
        seg_b: Path,
        video_path: Path,
        fade_dur: float,
    ) -> None:
        """Add crossfaded audio from two segments. Three-tier fallback: acrossfade → amix → silence."""
        temp_output = video_path.with_suffix(".tmp.mp4")
        audio_format = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"

        # Try acrossfade first
        filter_complex = (
            f"[1:a]{audio_format}[a1];"
            f"[2:a]{audio_format}[a2];"
            f"[a1][a2]acrossfade=d={fade_dur}:c1=tri:c2=tri,"
            f"atrim=0:{fade_dur},asetpts=PTS-STARTPTS[aout]"
        )
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-i",
            str(seg_a),
            "-i",
            str(seg_b),
            "-filter_complex",
            filter_complex,
            "-map",
            "0:v",
            "-map",
            "[aout]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            "-shortest",
            str(temp_output),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)

        if result.returncode != 0:
            logger.debug(f"acrossfade failed, trying amix: {result.stderr[-200:]}")
            filter_complex_fallback = (
                f"[1:a]{audio_format},afade=t=out:st=0:d={fade_dur}[afade_a];"
                f"[2:a]{audio_format},afade=t=in:st=0:d={fade_dur}[afade_b];"
                f"[afade_a][afade_b]amix=inputs=2:duration=first,"
                f"atrim=0:{fade_dur},asetpts=PTS-STARTPTS[aout]"
            )
            cmd_fallback = [
                "ffmpeg",
                "-y",
                "-i",
                str(video_path),
                "-i",
                str(seg_a),
                "-i",
                str(seg_b),
                "-filter_complex",
                filter_complex_fallback,
                "-map",
                "0:v",
                "-map",
                "[aout]",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-movflags",
                "+faststart",
                "-shortest",
                str(temp_output),
            ]
            result = subprocess.run(cmd_fallback, capture_output=True, text=True, timeout=1800)

            if result.returncode != 0:
                logger.warning(f"Audio crossfade failed, using silence: {result.stderr[-200:]}")
                cmd_silent = [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(video_path),
                    "-f",
                    "lavfi",
                    "-i",
                    f"anullsrc=r=48000:cl=stereo:d={fade_dur}",
                    "-map",
                    "0:v",
                    "-map",
                    "1:a",
                    "-c:v",
                    "copy",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "128k",
                    "-movflags",
                    "+faststart",
                    "-shortest",
                    str(temp_output),
                ]
                result = subprocess.run(cmd_silent, capture_output=True, text=True, timeout=1800)
                if result.returncode != 0:
                    logger.error(f"Failed to add any audio: {result.stderr[-200:]}")
                    return

        if temp_output.exists():
            temp_output.replace(video_path)

    @staticmethod
    def _configure_pyav_output_stream(container, fps, is_hdr, width, height, crf):
        """Configure PyAV output stream for encoding blended frames."""

        codec_name = "libx265" if is_hdr else "libx264"
        stream = container.add_stream(codec_name, rate=fps)
        stream.width = width
        stream.height = height
        stream.pix_fmt = "yuv420p10le" if is_hdr else "yuv420p"
        stream.options = {"crf": str(crf), "preset": "fast"}
        if is_hdr:
            stream.options.update(
                {
                    "x265-params": "colorprim=bt2020:transfer=arib-std-b67:colormatrix=bt2020nc",
                }
            )
        return stream
