"""Clip extraction and trimming."""

from __future__ import annotations

import logging
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from immich_memories.config import get_config
from immich_memories.processing.hardware import (
    HWAccelCapabilities,
    detect_hardware_acceleration,
    get_ffmpeg_encoder,
    get_ffmpeg_hwaccel_args,
)

logger = logging.getLogger(__name__)

# Transition buffer: extra footage before/after each segment for smooth fades
# This allows crossfade transitions without cutting into the main content
TRANSITION_BUFFER = 0.5  # seconds

# Tolerance for checking if we're at video boundaries (to account for float precision)
BOUNDARY_TOLERANCE = 0.1  # seconds

# Cache hardware capabilities
_hw_caps: HWAccelCapabilities | None = None


def _get_hw_caps() -> HWAccelCapabilities:
    """Get cached hardware capabilities."""
    global _hw_caps
    if _hw_caps is None:
        _hw_caps = detect_hardware_acceleration()
    return _hw_caps


@dataclass
class ClipSegment:
    """A segment of a video clip to use."""

    source_path: Path
    start_time: float
    end_time: float
    asset_id: str
    score: float = 0.0
    output_path: Path | None = None

    @property
    def duration(self) -> float:
        """Get segment duration."""
        return self.end_time - self.start_time

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "source_path": str(self.source_path),
            "start_time": self.start_time,
            "end_time": self.end_time,
            "asset_id": self.asset_id,
            "score": self.score,
            "output_path": str(self.output_path) if self.output_path else None,
        }


@dataclass
class ClipTransitionInfo:
    """Information about a clip for transition planning.

    This is used to pre-decide transitions BEFORE extraction,
    so we know where to add buffer footage.
    """

    asset_id: str
    start_time: float  # Segment start within the source video
    end_time: float  # Segment end within the source video
    video_duration: float  # Total source video duration
    is_title_screen: bool = False

    @property
    def can_buffer_start(self) -> bool:
        """Check if we have enough footage before start for a buffer."""
        return self.start_time >= TRANSITION_BUFFER - BOUNDARY_TOLERANCE

    @property
    def can_buffer_end(self) -> bool:
        """Check if we have enough footage after end for a buffer."""
        return (self.video_duration - self.end_time) >= TRANSITION_BUFFER - BOUNDARY_TOLERANCE


@dataclass
class TransitionPlan:
    """Pre-decided transitions and buffer requirements for a set of clips.

    Attributes:
        transitions: List of transition types between clips ("fade" or "cut").
                    Length is len(clips) - 1.
        buffer_start: List of booleans indicating if each clip needs start buffer.
        buffer_end: List of booleans indicating if each clip needs end buffer.
    """

    transitions: list[str]
    buffer_start: list[bool]
    buffer_end: list[bool]


def plan_transitions(
    clips: list[ClipTransitionInfo],
    transition_mode: str = "smart",
    transition_duration: float = TRANSITION_BUFFER,
) -> TransitionPlan:
    """Pre-decide transitions based on buffer availability.

    This function determines which transitions should be crossfades vs cuts,
    taking into account whether there's enough footage for buffers.

    Rules:
    - Title screens always get fade transitions (both in and out)
    - If a clip starts at 0 (beginning of video), incoming transition must be CUT
    - If a clip ends at video end, outgoing transition must be CUT
    - For SMART mode: 70% crossfade / 30% cut, respecting above constraints
    - For CROSSFADE mode: all crossfades where possible, CUT where not
    - For CUT mode: all cuts (no buffers needed)

    Args:
        clips: List of clip info for transition planning.
        transition_mode: "smart", "crossfade", or "cut".
        transition_duration: Duration of crossfade transitions.

    Returns:
        TransitionPlan with transitions and buffer requirements.
    """
    import random

    num_clips = len(clips)
    if num_clips == 0:
        return TransitionPlan(transitions=[], buffer_start=[], buffer_end=[])

    if num_clips == 1:
        return TransitionPlan(transitions=[], buffer_start=[False], buffer_end=[False])

    transitions: list[str] = []
    buffer_start: list[bool] = [False] * num_clips
    buffer_end: list[bool] = [False] * num_clips

    # For CUT mode, no transitions need buffers
    if transition_mode == "cut":
        transitions = ["cut"] * (num_clips - 1)
        return TransitionPlan(
            transitions=transitions,
            buffer_start=buffer_start,
            buffer_end=buffer_end,
        )

    consecutive_fades = 0
    consecutive_cuts = 0

    for i in range(num_clips - 1):
        clip_before = clips[i]
        clip_after = clips[i + 1]

        # Check buffer availability
        can_fade_out = clip_before.can_buffer_end
        can_fade_in = clip_after.can_buffer_start

        # Title screens always get fades (they have synthetic content, so always have "buffer")
        if clip_before.is_title_screen or clip_after.is_title_screen:
            # Title screens can always fade
            transitions.append("fade")
            if not clip_before.is_title_screen:
                buffer_end[i] = can_fade_out  # Only buffer real clips
            if not clip_after.is_title_screen:
                buffer_start[i + 1] = can_fade_in
            consecutive_fades += 1
            consecutive_cuts = 0
            continue

        # If either side can't buffer, must use cut
        if not can_fade_out or not can_fade_in:
            transitions.append("cut")
            consecutive_cuts += 1
            consecutive_fades = 0
            logger.debug(
                f"Transition {i}->{i + 1}: forced CUT (buffer unavailable: "
                f"out={can_fade_out}, in={can_fade_in})"
            )
            continue

        # Both sides can buffer - decide based on mode
        if transition_mode == "crossfade":
            # Always crossfade when possible
            use_fade = True
        else:
            # SMART mode: 70% crossfade, 30% cut with consecutive limits
            use_fade = random.random() < 0.7

            # Force cut if too many consecutive fades
            if consecutive_fades >= 3:
                use_fade = False

            # Force fade if too many consecutive cuts
            if consecutive_cuts >= 2:
                use_fade = True

        if use_fade:
            transitions.append("fade")
            buffer_end[i] = True
            buffer_start[i + 1] = True
            consecutive_fades += 1
            consecutive_cuts = 0
        else:
            transitions.append("cut")
            consecutive_cuts += 1
            consecutive_fades = 0

    logger.info(
        f"Transition plan ({transition_mode}): "
        f"{transitions.count('fade')} crossfades, {transitions.count('cut')} cuts"
    )

    return TransitionPlan(
        transitions=transitions,
        buffer_start=buffer_start,
        buffer_end=buffer_end,
    )


class ClipExtractor:
    """Extract and process video clips."""

    def __init__(self, output_dir: Path | None = None):
        """Initialize the clip extractor.

        Args:
            output_dir: Directory for extracted clips.
        """
        if output_dir is None:
            output_dir = Path(tempfile.gettempdir()) / "immich_memories" / "clips"
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def extract(
        self,
        segment: ClipSegment,
        reencode: bool = False,
        progress_callback: Callable[[float], None] | None = None,
        with_buffer: bool = False,
        buffer_seconds: float = TRANSITION_BUFFER,
        buffer_start: bool | None = None,
        buffer_end: bool | None = None,
    ) -> Path:
        """Extract a clip segment from a video.

        Args:
            segment: Clip segment to extract.
            reencode: If True, re-encode the video (slower but more compatible).
            progress_callback: Optional callback for progress updates.
            with_buffer: If True, add buffer footage before/after for transitions.
                        This is a legacy parameter - prefer using buffer_start/buffer_end.
            buffer_seconds: Amount of buffer to add (default 0.5s each side).
            buffer_start: If True, add buffer at start (for incoming crossfade).
                         Overrides with_buffer for start side.
            buffer_end: If True, add buffer at end (for outgoing crossfade).
                       Overrides with_buffer for end side.

        Returns:
            Path to the extracted clip.
        """
        if not segment.source_path.exists():
            raise FileNotFoundError(f"Source video not found: {segment.source_path}")

        # Determine per-side buffer requirements
        # If buffer_start/buffer_end are explicitly set, use them
        # Otherwise fall back to with_buffer for both sides (legacy behavior)
        add_start_buffer = buffer_start if buffer_start is not None else with_buffer
        add_end_buffer = buffer_end if buffer_end is not None else with_buffer

        # Apply buffer for smooth transitions if requested
        if add_start_buffer or add_end_buffer:
            video_duration = get_video_duration(segment.source_path)

            if add_start_buffer:
                buffered_start = max(0, segment.start_time - buffer_seconds)
            else:
                buffered_start = segment.start_time

            if add_end_buffer:
                buffered_end = (
                    min(video_duration, segment.end_time + buffer_seconds)
                    if video_duration > 0
                    else segment.end_time + buffer_seconds
                )
            else:
                buffered_end = segment.end_time

            # Create a buffered segment for extraction
            buffered_segment = ClipSegment(
                source_path=segment.source_path,
                start_time=buffered_start,
                end_time=buffered_end,
                asset_id=segment.asset_id,
                score=segment.score,
            )
            # Include buffer flags in filename to differentiate cached versions
            buffer_suffix = f"_b{int(add_start_buffer)}{int(add_end_buffer)}"
            output_filename = (
                f"{segment.asset_id}_{buffered_start:.1f}_{buffered_end:.1f}{buffer_suffix}.mp4"
            )
            extraction_segment = buffered_segment
        else:
            output_filename = (
                f"{segment.asset_id}_{segment.start_time:.1f}_{segment.end_time:.1f}.mp4"
            )
            extraction_segment = segment

        output_path = self.output_dir / output_filename

        if output_path.exists():
            logger.debug(f"Clip already exists: {output_path}")
            segment.output_path = output_path
            return output_path

        if reencode:
            self._extract_with_reencode(extraction_segment, output_path, progress_callback)
        else:
            self._extract_copy(extraction_segment, output_path)

        segment.output_path = output_path
        return output_path

    def _extract_copy(self, segment: ClipSegment, output_path: Path) -> None:
        """Extract clip using stream copy (fast, no quality loss).

        Args:
            segment: Clip segment to extract.
            output_path: Path for output file.
        """
        cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            str(segment.start_time),
            "-i",
            str(segment.source_path),
            "-t",
            str(segment.duration),
            "-c",
            "copy",
            "-avoid_negative_ts",
            "make_zero",
            str(output_path),
        ]

        logger.debug(f"Running: {' '.join(cmd)}")

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            logger.error(f"FFmpeg error: {result.stderr}")
            raise RuntimeError(f"Failed to extract clip: {result.stderr}")

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

        # Determine codec based on config
        codec = "h264" if config.output.codec in ("h264", "h265") else "h264"

        # Build command with hardware acceleration if available
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
        if hw_caps and hw_caps.has_encoding:
            encoder, encoder_args = get_ffmpeg_encoder(
                hw_caps,
                codec=codec,
                preset=config.hardware.encoder_preset,
            )
            cmd.extend(["-c:v", encoder])
            cmd.extend(encoder_args)

            # For hardware encoders, use quality-based rate control
            # Different encoders use different quality parameters
            if "nvenc" in encoder:
                cmd.extend(["-cq", str(config.output.crf)])
            elif "videotoolbox" in encoder:
                # VideoToolbox uses -q:v for quality (already set in encoder_args)
                pass
            elif "vaapi" in encoder or "qsv" in encoder:
                cmd.extend(["-global_quality", str(config.output.crf)])
            else:
                cmd.extend(["-crf", str(config.output.crf)])

            logger.info(f"Using hardware encoder: {encoder}")
        else:
            # Fallback to software encoding
            cmd.extend(["-c:v", "libx264"])
            cmd.extend(["-preset", "medium"])
            cmd.extend(["-crf", str(config.output.crf)])
            logger.info("Using software encoder: libx264")

        # Audio encoding (always software, very fast)
        cmd.extend(["-c:a", "aac", "-b:a", "128k"])

        # Output options
        cmd.extend(["-movflags", "+faststart"])
        cmd.append(str(output_path))

        if progress_callback:
            cmd.insert(1, "-progress")
            cmd.insert(2, "pipe:1")

        logger.debug(f"Running: {' '.join(cmd)}")

        if progress_callback:
            # Run with progress monitoring
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
                # If hardware encoding failed, retry with software
                if hw_caps and hw_caps.has_encoding and "nvenc" in stderr.lower():
                    logger.warning("Hardware encoding failed, falling back to software")
                    return self._extract_with_reencode(
                        segment, output_path, progress_callback, use_hw_accel=False
                    )
                raise RuntimeError(f"Failed to extract clip: {stderr}")
        else:
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                # If hardware encoding failed, retry with software
                if hw_caps and hw_caps.has_encoding:
                    logger.warning("Hardware encoding failed, falling back to software")
                    return self._extract_with_reencode(
                        segment, output_path, progress_callback, use_hw_accel=False
                    )
                raise RuntimeError(f"Failed to extract clip: {result.stderr}")

    def batch_extract(
        self,
        segments: list[ClipSegment],
        reencode: bool = False,
        progress_callback: Callable[[int, int], None] | None = None,
        with_buffer: bool = False,
        buffer_seconds: float = TRANSITION_BUFFER,
        transition_plan: TransitionPlan | None = None,
    ) -> list[Path]:
        """Extract multiple clip segments.

        Args:
            segments: List of segments to extract.
            reencode: Whether to re-encode clips.
            progress_callback: Callback with (current, total) counts.
            with_buffer: If True, add buffer footage for transitions (legacy).
            buffer_seconds: Amount of buffer to add (default 0.5s each side).
            transition_plan: Pre-decided transition plan with per-clip buffer flags.
                            If provided, overrides with_buffer.

        Returns:
            List of paths to extracted clips.
        """
        results = []

        for i, segment in enumerate(segments):
            try:
                # Use transition plan for per-clip buffer if available
                if transition_plan is not None:
                    buffer_start = transition_plan.buffer_start[i]
                    buffer_end = transition_plan.buffer_end[i]
                    path = self.extract(
                        segment,
                        reencode=reencode,
                        buffer_seconds=buffer_seconds,
                        buffer_start=buffer_start,
                        buffer_end=buffer_end,
                    )
                else:
                    path = self.extract(
                        segment,
                        reencode=reencode,
                        with_buffer=with_buffer,
                        buffer_seconds=buffer_seconds,
                    )
                results.append(path)
            except Exception as e:
                logger.error(f"Failed to extract segment: {e}")
                continue

            if progress_callback:
                progress_callback(i + 1, len(segments))

        return results


def extract_clip(
    source_path: Path,
    start_time: float,
    end_time: float,
    output_path: Path | None = None,
    reencode: bool = False,
    buffer_start: bool = False,
    buffer_end: bool = False,
    buffer_seconds: float = TRANSITION_BUFFER,
) -> Path:
    """Convenience function to extract a single clip.

    Args:
        source_path: Path to source video.
        start_time: Start time in seconds.
        end_time: End time in seconds.
        output_path: Optional output path.
        reencode: Whether to re-encode.
        buffer_start: If True, add buffer at start for incoming crossfade.
        buffer_end: If True, add buffer at end for outgoing crossfade.
        buffer_seconds: Amount of buffer to add (default 0.5s).

    Returns:
        Path to extracted clip.
    """
    import hashlib

    # Calculate actual extraction times with buffers
    actual_start = start_time
    actual_end = end_time

    if buffer_start or buffer_end:
        video_duration = get_video_duration(source_path)

        if buffer_start:
            actual_start = max(0, start_time - buffer_seconds)

        if buffer_end:
            actual_end = (
                min(video_duration, end_time + buffer_seconds)
                if video_duration > 0
                else end_time + buffer_seconds
            )

    if output_path is None:
        output_dir = Path(tempfile.gettempdir()) / "immich_memories" / "clips"
        output_dir.mkdir(parents=True, exist_ok=True)
        # Include source path hash to avoid collisions when multiple clips have same times
        source_hash = hashlib.md5(str(source_path).encode()).hexdigest()[:8]
        # Include buffer flags in filename
        buffer_suffix = (
            f"_b{int(buffer_start)}{int(buffer_end)}" if (buffer_start or buffer_end) else ""
        )
        enc_suffix = "_enc" if reencode else ""
        output_path = (
            output_dir
            / f"clip_{source_hash}_{actual_start:.1f}_{actual_end:.1f}{buffer_suffix}{enc_suffix}.mp4"
        )

    segment = ClipSegment(
        source_path=Path(source_path),
        start_time=actual_start,
        end_time=actual_end,
        asset_id="temp",
    )

    extractor = ClipExtractor(output_path.parent)
    segment.output_path = output_path

    if output_path.exists():
        logger.debug(f"Clip already exists: {output_path}")
        return output_path

    if reencode:
        extractor._extract_with_reencode(segment, output_path, None)
    else:
        extractor._extract_copy(segment, output_path)

    return output_path


def get_video_duration(video_path: Path) -> float:
    """Get the duration of a video file.

    Args:
        video_path: Path to the video file.

    Returns:
        Duration in seconds.
    """
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(video_path),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        logger.error(f"FFprobe error: {result.stderr}")
        return 0.0

    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def get_video_info(video_path: Path) -> dict:
    """Get detailed video information.

    Args:
        video_path: Path to the video file.

    Returns:
        Dictionary with video metadata.
    """
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,r_frame_rate,codec_name,bit_rate,color_space,color_transfer,color_primaries,bits_per_raw_sample",
        "-show_entries",
        "format=duration,size,bit_rate",
        "-of",
        "json",
        str(video_path),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        logger.error(f"FFprobe error: {result.stderr}")
        return {}

    import json

    try:
        data = json.loads(result.stdout)
        stream = data.get("streams", [{}])[0]
        fmt = data.get("format", {})

        # Parse frame rate
        fps = 0.0
        if "r_frame_rate" in stream:
            parts = stream["r_frame_rate"].split("/")
            if len(parts) == 2 and int(parts[1]) > 0:
                fps = int(parts[0]) / int(parts[1])

        # Parse bit depth
        bit_depth = None
        if "bits_per_raw_sample" in stream:
            try:
                bit_depth = int(stream["bits_per_raw_sample"])
            except (ValueError, TypeError):
                pass

        return {
            "width": stream.get("width", 0),
            "height": stream.get("height", 0),
            "fps": fps,
            "codec": stream.get("codec_name", ""),
            "bitrate": int(fmt.get("bit_rate", 0)),
            "duration": float(fmt.get("duration", 0)),
            "size": int(fmt.get("size", 0)),
            # HDR metadata
            "color_space": stream.get("color_space"),
            "color_transfer": stream.get("color_transfer"),
            "color_primaries": stream.get("color_primaries"),
            "bit_depth": bit_depth,
        }
    except (json.JSONDecodeError, ValueError, KeyError) as e:
        logger.error(f"Failed to parse video info: {e}")
        return {}


def _validate_url(url: str) -> str:
    """Validate and sanitize a URL for use with ffprobe.

    Args:
        url: The URL to validate.

    Returns:
        The validated URL.

    Raises:
        ValueError: If the URL is invalid or potentially malicious.
    """
    from urllib.parse import urlparse

    # Check for null bytes
    if "\x00" in url:
        raise ValueError("URL contains null bytes")

    # Parse and validate URL structure
    try:
        parsed = urlparse(url)
    except Exception as e:
        raise ValueError(f"Invalid URL format: {e}") from e

    # Only allow http/https schemes
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Invalid URL scheme: {parsed.scheme}")

    # Ensure the URL has a valid hostname
    if not parsed.netloc:
        raise ValueError("URL missing hostname")

    # Check for suspicious characters that might indicate injection attempts
    # Shell metacharacters that could cause issues even without shell=True
    suspicious_chars = [";", "|", "&", "$", "`", "\n", "\r"]
    for char in suspicious_chars:
        if char in url:
            raise ValueError(f"URL contains suspicious character: {repr(char)}")

    return url


def _validate_header(key: str, value: str) -> tuple[str, str]:
    """Validate a header key-value pair for use with ffprobe.

    Args:
        key: The header key.
        value: The header value.

    Returns:
        Tuple of (validated_key, validated_value).

    Raises:
        ValueError: If the header is invalid.
    """
    import re

    # Check for null bytes in key first (security critical)
    if "\x00" in key:
        raise ValueError(f"Header key contains null bytes: {key}")

    # Validate header key - only allow alphanumeric, dash, underscore
    if not re.match(r"^[a-zA-Z0-9_-]+$", key):
        raise ValueError(f"Header key contains invalid characters: {key}")

    # Check for null bytes in value
    if "\x00" in value:
        raise ValueError(f"Header {key} value contains null bytes")

    # Check for newlines that could cause header injection
    if "\n" in value or "\r" in value:
        raise ValueError(f"Header {key} value contains newline characters")

    # Limit header value length to prevent abuse
    max_length = 4096
    if len(value) > max_length:
        raise ValueError(f"Header {key} value exceeds maximum length of {max_length}")

    return key, value


def probe_video_url(url: str, headers: dict[str, str] | None = None) -> dict:
    """Probe video metadata from a URL without downloading the full file.

    Args:
        url: The video URL to probe (must be http/https).
        headers: Optional HTTP headers (e.g., for authentication).

    Returns:
        Dictionary with video metadata including HDR info.
        Empty dict if probing fails or URL is invalid.
    """
    import json

    # Validate URL (security: prevent injection attacks)
    try:
        validated_url = _validate_url(url)
    except ValueError as e:
        logger.error(f"Invalid URL for ffprobe: {e}")
        return {}

    # Build ffprobe command for URL
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,r_frame_rate,codec_name,bit_rate,color_space,color_transfer,color_primaries,bits_per_raw_sample:stream_side_data=rotation",
        "-show_entries",
        "format=duration,size,bit_rate",
        "-of",
        "json",
    ]

    # Add headers if provided (with validation)
    if headers:
        try:
            validated_headers = [_validate_header(k, v) for k, v in headers.items()]
            header_str = "\r\n".join(f"{k}: {v}" for k, v in validated_headers)
            cmd.extend(["-headers", header_str])
        except ValueError as e:
            logger.error(f"Invalid header for ffprobe: {e}")
            return {}

    cmd.append(validated_url)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        if result.returncode != 0:
            # Log error details without exposing sensitive URL parts
            stderr_preview = result.stderr[:200] if result.stderr else "No stderr"
            logger.debug(f"FFprobe stderr: {stderr_preview}")
            logger.error(f"FFprobe failed to probe video URL (exit code {result.returncode})")
            return {}

        data = json.loads(result.stdout)
        stream = data.get("streams", [{}])[0]
        fmt = data.get("format", {})

        # Parse frame rate
        fps = 0.0
        if "r_frame_rate" in stream:
            parts = stream["r_frame_rate"].split("/")
            if len(parts) == 2 and int(parts[1]) > 0:
                fps = int(parts[0]) / int(parts[1])

        # Parse bit depth
        bit_depth = None
        if "bits_per_raw_sample" in stream:
            try:
                bit_depth = int(stream["bits_per_raw_sample"])
            except (ValueError, TypeError):
                pass

        # Parse rotation from side_data_list (iPhone videos often have this)
        rotation = 0
        for side_data in stream.get("side_data_list", []):
            if "rotation" in side_data:
                try:
                    rotation = abs(int(side_data["rotation"]))
                except (ValueError, TypeError):
                    pass
                break

        return {
            "width": stream.get("width", 0),
            "height": stream.get("height", 0),
            "fps": fps,
            "codec": stream.get("codec_name", ""),
            "bitrate": int(fmt.get("bit_rate", 0)) if fmt.get("bit_rate") else 0,
            "duration": float(fmt.get("duration", 0)) if fmt.get("duration") else 0,
            "size": int(fmt.get("size", 0)) if fmt.get("size") else 0,
            # HDR metadata
            "color_space": stream.get("color_space"),
            "color_transfer": stream.get("color_transfer"),
            "color_primaries": stream.get("color_primaries"),
            "bit_depth": bit_depth,
            # Rotation metadata
            "rotation": rotation,
        }
    except subprocess.TimeoutExpired:
        logger.error("FFprobe timeout while probing video URL")
        return {}
    except (json.JSONDecodeError, ValueError, KeyError) as e:
        logger.error(f"Failed to parse video info from URL: {e}")
        return {}
