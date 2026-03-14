"""Ending screen generation mixin for TitleScreenGenerator.

Extracts the ending video creation logic (fade-to-white with FFmpeg streaming)
into a mixin class to keep the main generator file under 500 lines.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .encoding import _get_gpu_encoder_args

logger = logging.getLogger(__name__)


class EndingScreenMixin:
    """Mixin providing ending video generation for TitleScreenGenerator.

    Requires the host class to have:
        - self.style: TitleStyle instance
    """

    def _create_ending_video(
        self,
        output_path: Path,
        fade_to_color: tuple[int, int, int],
        width: int,
        height: int,
        duration: float,
        fps: float,
        hdr: bool = True,
    ) -> None:
        """Create ending video with fade to specified color.

        Memory-optimized: streams frames directly to FFmpeg instead of
        saving to disk. This reduces memory usage from ~2-3GB to ~100MB
        for 4K video.

        Args:
            output_path: Output video path.
            fade_to_color: Color to fade to (typically white).
            width: Video width.
            height: Video height.
            duration: Video duration.
            fps: Frames per second.
        """
        import subprocess

        try:
            from PIL import Image
        except ImportError:
            raise ImportError("PIL/Pillow is required for ending screen generation")

        from .backgrounds import create_background_for_style

        total_frames = int(duration * fps)
        fade_start_frame = int(1.5 * fps)  # Start fade at 1.5s
        fade_duration_frames = total_frames - fade_start_frame

        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Build FFmpeg command — same pattern as taichi_video.py (intro)
        # to ensure identical HLG HDR output. No zscale filter needed;
        # just tag the output as HLG like the intro does.
        encoder_args = _get_gpu_encoder_args(hdr=hdr)
        cmd = [
            "ffmpeg",
            "-y",
            # Input: raw RGB frames from stdin
            "-f",
            "rawvideo",
            "-vcodec",
            "rawvideo",
            "-s",
            f"{width}x{height}",
            "-pix_fmt",
            "rgb24",
            "-r",
            str(fps),
            "-i",
            "pipe:0",
            # Add silent audio track (required for assembly compatibility)
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=r=48000:cl=stereo:d={duration}",
            # Video encoding - GPU accelerated with 10-bit HLG
            *encoder_args,
            # Audio encoding
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-shortest",
            "-movflags",
            "+faststart",
            str(output_path),
        ]

        # Create base background (same as title but no text)
        base_bg = create_background_for_style(
            width,
            height,
            self.style.background_type,
            self.style.background_colors,
            self.style.background_angle,
        )

        # Ensure RGB mode for raw video encoding
        if base_bg.mode != "RGB":
            base_bg = base_bg.convert("RGB")

        # Create solid color frame for fade target
        solid_color = Image.new("RGB", (width, height), fade_to_color)

        # Cache base background bytes (reused for non-fade frames)
        base_bg_bytes = base_bg.tobytes()

        # Start FFmpeg process
        process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        try:
            for i in range(total_frames):
                if i < fade_start_frame:
                    # Before fade - reuse cached base background bytes
                    assert process.stdin is not None
                    process.stdin.write(base_bg_bytes)
                else:
                    # During fade - blend and write immediately
                    fade_progress = (i - fade_start_frame) / fade_duration_frames
                    fade_progress = min(1.0, fade_progress)

                    # Use smooth easing
                    fade_progress = fade_progress**0.5  # Ease out

                    # Blend images
                    frame = Image.blend(base_bg, solid_color, fade_progress)
                    assert process.stdin is not None
                    process.stdin.write(frame.tobytes())
                    del frame  # Immediate cleanup

            assert process.stdin is not None
            process.stdin.close()
            _, stderr = process.communicate()

            if process.returncode != 0:
                raise RuntimeError(f"FFmpeg failed: {stderr.decode()[-500:]}")

        except BrokenPipeError:
            _, stderr = process.communicate()
            raise RuntimeError(f"FFmpeg pipe broken: {stderr.decode()[-500:]}")
