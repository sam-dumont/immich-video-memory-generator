"""Encoding mixin for ClipExtractor - handles re-encoding and hardware acceleration."""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from immich_memories.config import get_config
from immich_memories.processing.hardware import (
    HWAccelCapabilities,
    detect_hardware_acceleration,
    get_ffmpeg_encoder,
    get_ffmpeg_hwaccel_args,
)

if TYPE_CHECKING:
    from immich_memories.processing.clips import ClipSegment

logger = logging.getLogger(__name__)

# Cache hardware capabilities
_hw_caps: HWAccelCapabilities | None = None


def _get_hw_caps() -> HWAccelCapabilities:
    """Get cached hardware capabilities."""
    global _hw_caps
    if _hw_caps is None:
        _hw_caps = detect_hardware_acceleration()
    return _hw_caps


class ClipEncodingMixin:
    """Mixin providing re-encoding and hardware-accelerated encoding for ClipExtractor."""

    def _build_reencode_command(
        self,
        segment: ClipSegment,
        output_path: Path,
        hw_caps: HWAccelCapabilities | None,
    ) -> list[str]:
        """Build the FFmpeg command for re-encoding a clip.

        Args:
            segment: Clip segment to extract.
            output_path: Path for output file.
            hw_caps: Hardware acceleration capabilities, or None for software-only.

        Returns:
            List of command arguments for FFmpeg.
        """
        config = get_config()
        codec = "h264" if config.output.codec in ("h264", "h265") else "h264"

        cmd = ["ffmpeg", "-y"]

        # Add hardware decode args if available
        if hw_caps and hw_caps.has_decoding and config.hardware.gpu_decode:
            hwaccel_args = get_ffmpeg_hwaccel_args(hw_caps, operation="decode", codec=codec)
            cmd.extend(hwaccel_args)

        # Input seeking and file
        cmd.extend(["-ss", str(segment.start_time)])
        cmd.extend(["-i", str(segment.source_path)])
        cmd.extend(["-t", str(segment.duration)])

        # Get encoder and its args
        self._append_encoder_args(cmd, hw_caps, codec, config)

        # Audio encoding (always software, very fast)
        cmd.extend(["-c:a", "aac", "-b:a", "128k"])

        # Output options
        cmd.extend(["-movflags", "+faststart"])
        cmd.append(str(output_path))

        return cmd

    def _append_encoder_args(
        self,
        cmd: list[str],
        hw_caps: HWAccelCapabilities | None,
        codec: str,
        config: object,
    ) -> None:
        """Append video encoder arguments to the FFmpeg command.

        Args:
            cmd: Command list to append to (mutated in place).
            hw_caps: Hardware acceleration capabilities, or None for software-only.
            codec: Target codec name.
            config: Application configuration.
        """
        if hw_caps and hw_caps.has_encoding:
            encoder, encoder_args = get_ffmpeg_encoder(
                hw_caps,
                codec=codec,
                preset=config.hardware.encoder_preset,
            )
            cmd.extend(["-c:v", encoder])
            cmd.extend(encoder_args)
            self._append_quality_args(cmd, encoder, config.output.crf)
            logger.info(f"Using hardware encoder: {encoder}")
        else:
            cmd.extend(["-c:v", "libx264"])
            cmd.extend(["-preset", "medium"])
            cmd.extend(["-crf", str(config.output.crf)])
            logger.info("Using software encoder: libx264")

    @staticmethod
    def _append_quality_args(cmd: list[str], encoder: str, crf: int) -> None:
        """Append quality-based rate control args for the given encoder.

        Args:
            cmd: Command list to append to (mutated in place).
            encoder: Encoder name (e.g. 'h264_nvenc').
            crf: Quality value.
        """
        if "nvenc" in encoder:
            cmd.extend(["-cq", str(crf)])
        elif "videotoolbox" in encoder:
            # VideoToolbox uses -q:v for quality (already set in encoder_args)
            pass
        elif "vaapi" in encoder or "qsv" in encoder:
            cmd.extend(["-global_quality", str(crf)])
        else:
            cmd.extend(["-crf", str(crf)])

    def _run_with_progress(
        self,
        cmd: list[str],
        segment: ClipSegment,
        progress_callback: Callable[[float], None],
        hw_caps: HWAccelCapabilities | None,
        output_path: Path,
    ) -> None:
        """Run FFmpeg with progress monitoring.

        Args:
            cmd: FFmpeg command to run.
            segment: Clip segment being extracted.
            progress_callback: Callback for progress updates.
            hw_caps: Hardware capabilities (for fallback decision).
            output_path: Output path (for fallback re-encoding).
        """
        cmd.insert(1, "-progress")
        cmd.insert(2, "pipe:1")

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        while process.stdout is not None:
            line = process.stdout.readline()
            if not line and process.poll() is not None:
                break

            if line.startswith("out_time_ms="):
                try:
                    time_ms = int(line.split("=")[1])
                    progress = min(time_ms / (segment.duration * 1_000_000), 1.0)
                    progress_callback(progress)
                except (ValueError, IndexError):
                    pass

        if process.returncode != 0:
            stderr = process.stderr.read() if process.stderr else ""
            if hw_caps and hw_caps.has_encoding and "nvenc" in stderr.lower():
                logger.warning("Hardware encoding failed, falling back to software")
                return self._extract_with_reencode(
                    segment, output_path, progress_callback, use_hw_accel=False
                )
            raise RuntimeError(f"Failed to extract clip: {stderr}")

    def _extract_with_reencode(
        self,
        segment: ClipSegment,
        output_path: Path,
        progress_callback: Callable[[float], None] | None = None,
        use_hw_accel: bool = True,
    ) -> None:
        """Extract clip with re-encoding (slower but ensures compatibility).

        Uses hardware acceleration (NVENC, VideoToolbox, etc.) when available.

        Args:
            segment: Clip segment to extract.
            output_path: Path for output file.
            progress_callback: Optional progress callback.
            use_hw_accel: Whether to use hardware acceleration if available.
        """
        config = get_config()
        hw_caps = _get_hw_caps() if use_hw_accel and config.hardware.enabled else None

        cmd = self._build_reencode_command(segment, output_path, hw_caps)

        logger.debug(f"Running: {' '.join(cmd)}")

        if progress_callback:
            self._run_with_progress(cmd, segment, progress_callback, hw_caps, output_path)
        else:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if result.returncode != 0:
                # If hardware encoding failed, retry with software
                if hw_caps and hw_caps.has_encoding:
                    logger.warning("Hardware encoding failed, falling back to software")
                    return self._extract_with_reencode(
                        segment, output_path, progress_callback, use_hw_accel=False
                    )
                raise RuntimeError(f"Failed to extract clip: {result.stderr}")
