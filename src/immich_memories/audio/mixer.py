"""Audio mixing with intelligent ducking and music looping."""

from __future__ import annotations

import contextlib
import logging
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from immich_memories.security import validate_audio_path, validate_video_path

logger = logging.getLogger(__name__)


def _db_to_linear(db: float, min_val: float = 1.0, max_val: float = 64.0) -> float:
    """Convert dB to linear scale for FFmpeg parameters.

    FFmpeg's sidechaincompress 'makeup' parameter expects linear scale (1-64).
    Formula: linear = 10^(dB/20)

    Args:
        db: Value in decibels
        min_val: Minimum allowed linear value
        max_val: Maximum allowed linear value

    Returns:
        Linear value clamped to [min_val, max_val]
    """
    linear = 10 ** (db / 20)
    return max(min_val, min(max_val, linear))


@dataclass
class DuckingConfig:
    """Configuration for audio ducking (lowering music when speech is present)."""

    # Threshold for sidechain compression (0.0-1.0)
    # Lower values = more sensitive to speech
    threshold: float = 0.02

    # Compression ratio (how much to lower music)
    # Higher = more aggressive ducking (4.0 = moderate, smoother than 6.0)
    ratio: float = 4.0

    # Attack time in milliseconds (how fast ducking kicks in)
    # 100ms = smooth fade down, not too abrupt
    attack_ms: float = 100.0

    # Release time in milliseconds (how fast music comes back)
    # 2500ms = music stays ducked during natural speech pauses (word -> pause -> word)
    # This prevents the "pumping" effect between words
    release_ms: float = 2500.0

    # Makeup gain in dB (boost after compression)
    makeup_db: float = 0.0

    # Music volume reduction in dB (base level before ducking)
    music_volume_db: float = -6.0


@dataclass
class MixConfig:
    """Configuration for audio mixing."""

    ducking: DuckingConfig
    fade_in_seconds: float = 2.0
    fade_out_seconds: float = 3.0
    music_starts_at: float = 0.0  # Seconds into video
    normalize_audio: bool = True


def get_audio_duration(audio_path: Path) -> float:
    """Get the duration of an audio file in seconds.

    Args:
        audio_path: Path to the audio file

    Returns:
        Duration in seconds
    """
    # Validate path before subprocess call
    validated_path = validate_audio_path(audio_path, must_exist=True)
    return _get_duration_unchecked(validated_path)


def get_video_duration(video_path: Path) -> float:
    """Get the duration of a video file in seconds.

    Args:
        video_path: Path to the video file

    Returns:
        Duration in seconds
    """
    # Validate as video, then use audio duration probe (same ffprobe command)
    validated_path = validate_video_path(video_path, must_exist=True)
    # Re-use the audio duration function with validated path
    return _get_duration_unchecked(validated_path)


def _get_duration_unchecked(path: Path) -> float:
    """Get duration without validation (internal use after validation)."""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return float(result.stdout.strip())
    except (subprocess.CalledProcessError, ValueError) as e:
        logger.error(f"Could not get duration: {e}")
        return 0.0


def loop_audio_to_duration(
    audio_path: Path,
    target_duration: float,
    output_path: Path | None = None,
    _crossfade_seconds: float = 2.0,  # noqa: ARG001 - reserved for future use
) -> Path:
    """Loop an audio file to reach target duration with crossfade.

    Args:
        audio_path: Path to the source audio
        target_duration: Target duration in seconds
        output_path: Output path (auto-generated if None)
        crossfade_seconds: Crossfade duration between loops

    Returns:
        Path to the looped audio file
    """
    if output_path is None:
        # Use NamedTemporaryFile for secure temp file creation (no race condition)
        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", prefix="looped_", delete=False)
        tmp.close()
        output_path = Path(tmp.name)

    audio_duration = get_audio_duration(audio_path)

    if audio_duration <= 0:
        raise ValueError(f"Could not determine duration of {audio_path}")

    # If audio is already long enough, just trim it
    if audio_duration >= target_duration:
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(audio_path),
            "-t",
            str(target_duration),
            "-acodec",
            "libmp3lame",
            "-q:a",
            "2",
            str(output_path),
        ]
        subprocess.run(cmd, capture_output=True, check=True, timeout=600)
        return output_path

    # Calculate how many loops we need
    num_loops = int(target_duration / audio_duration) + 2

    # Use FFmpeg's aloop filter for seamless looping
    # Then apply crossfade between loops using acrossfade
    filter_complex = (
        f"[0:a]aloop=loop={num_loops}:size=2e9[looped];"
        f"[looped]atrim=0:{target_duration}[trimmed];"
        f"[trimmed]afade=t=in:st=0:d=1,afade=t=out:st={target_duration - 2}:d=2[out]"
    )

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(audio_path),
        "-filter_complex",
        filter_complex,
        "-map",
        "[out]",
        "-acodec",
        "libmp3lame",
        "-q:a",
        "2",
        str(output_path),
    ]

    try:
        subprocess.run(cmd, capture_output=True, check=True, timeout=600)
    except subprocess.CalledProcessError as e:
        logger.error(f"Loop failed: {e.stderr.decode() if e.stderr else e}")
        raise

    return output_path


def mix_audio_with_ducking(
    video_path: Path,
    music_path: Path,
    output_path: Path,
    config: MixConfig | None = None,
) -> Path:
    """Mix background music with video, ducking music when speech is present.

    Uses FFmpeg's sidechaincompress filter to automatically lower music
    volume when the video's audio is louder (speech, sound effects, etc.).

    Args:
        video_path: Path to the video file
        music_path: Path to the background music
        output_path: Path for the output video
        config: Mixing configuration

    Returns:
        Path to the output video
    """
    if config is None:
        config = MixConfig(ducking=DuckingConfig())

    ducking = config.ducking
    video_duration = get_video_duration(video_path)

    # First, ensure music is long enough
    music_duration = get_audio_duration(music_path)
    music_to_use = music_path

    if music_duration < video_duration:
        logger.info(f"Looping music from {music_duration:.1f}s to {video_duration:.1f}s")
        # Use NamedTemporaryFile for secure temp file creation (no race condition)
        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", prefix="looped_music_", delete=False)
        tmp.close()
        looped_music = Path(tmp.name)
        music_to_use = loop_audio_to_duration(music_path, video_duration, looped_music)

    # Build the complex filter for ducking
    filter_parts = _build_ducking_filter(config, ducking, video_duration)
    filter_complex = ";".join(filter_parts)

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(music_to_use),
        "-filter_complex",
        filter_complex,
        "-map",
        "0:v",  # Keep original video
        "-map",
        "[mixed]",  # Use mixed audio
        "-c:v",
        "copy",  # Don't re-encode video
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        str(output_path),
    ]

    try:
        logger.info("Mixing audio with ducking...")
        subprocess.run(cmd, capture_output=True, check=True, timeout=600)
        logger.info(f"Audio mixed successfully: {output_path}")
    except subprocess.CalledProcessError as e:
        logger.error(f"Audio mixing failed: {e.stderr.decode() if e.stderr else e}")
        raise
    finally:
        # Cleanup looped music if we created it
        if music_to_use != music_path and music_to_use.exists():
            with contextlib.suppress(OSError):
                music_to_use.unlink()

    return output_path


def _build_ducking_filter(
    config: MixConfig, ducking: DuckingConfig, video_duration: float
) -> list[str]:
    """Build FFmpeg filter parts for audio ducking."""
    filter_parts = []

    # Prepare music: trim to video length, apply volume, add fades
    music_filter = f"[1:a]atrim=0:{video_duration}"
    music_filter += f",volume={ducking.music_volume_db}dB"

    if config.fade_in_seconds > 0:
        music_filter += f",afade=t=in:st={config.music_starts_at}:d={config.fade_in_seconds}"

    if config.fade_out_seconds > 0:
        fade_start = video_duration - config.fade_out_seconds
        music_filter += f",afade=t=out:st={fade_start}:d={config.fade_out_seconds}"

    music_filter += "[music]"
    filter_parts.append(music_filter)

    # Prepare video audio (normalize if requested)
    # WHY: Split into two copies — FFmpeg 6.x doesn't allow consuming a label twice.
    # [va] feeds sidechaincompress (as sidechain input), [vamix] feeds amix.
    # WHY: apad+atrim forces audio to exact video duration. The mux step before
    # this already pads, but some FFmpeg versions/codecs don't honor padded AAC
    # duration on re-decode — the stream decodes shorter than container metadata.
    pad = f"apad=whole_dur={video_duration},atrim=0:{video_duration},"
    if config.normalize_audio:
        filter_parts.append(f"[0:a]{pad}loudnorm=I=-16:TP=-1.5:LRA=11,asplit=2[va][vamix]")
    else:
        filter_parts.append(f"[0:a]{pad}asplit=2[va][vamix]")

    # Apply sidechain compression: duck music when video audio is present
    sidechain_filter = (
        f"[music][va]sidechaincompress="
        f"threshold={ducking.threshold}:"
        f"ratio={ducking.ratio}:"
        f"attack={ducking.attack_ms}:"
        f"release={ducking.release_ms}:"
        f"makeup={_db_to_linear(ducking.makeup_db):.2f}"
        f"[ducked_music]"
    )
    filter_parts.extend(
        (
            sidechain_filter,
            # WHY: duration=longest + final apad/atrim = belt-and-suspenders for
            # correct output duration. amix duration=longest should produce full
            # length, but some FFmpeg versions write incorrect stream duration
            # metadata. The final apad/atrim guarantees both the actual samples
            # AND the metadata match video_duration.
            "[vamix][ducked_music]amix=inputs=2:duration=longest:dropout_transition=2,"
            f"apad=whole_dur={video_duration},atrim=0:{video_duration}[mixed]",
        )
    )

    return filter_parts
