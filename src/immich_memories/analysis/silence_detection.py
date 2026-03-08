"""Silence detection for smart segment boundary adjustment.

Uses audio energy analysis to find natural break points (silence gaps)
to avoid cutting mid-sentence in video segments.
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)


def _extract_audio_with_ffmpeg(video_path: Path) -> tuple[np.ndarray, int] | None:
    """Extract audio using ffmpeg with explicit stream selection.

    This handles iPhone videos with spatial audio (apac codec) by
    explicitly selecting only the first audio stream (AAC).

    Args:
        video_path: Path to video file.

    Returns:
        Tuple of (audio_array, sample_rate) or None if extraction fails.
    """
    try:
        # Use ffmpeg to extract only the first audio stream as raw PCM
        # -map 0:a:0 selects only the first audio stream, ignoring spatial audio
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name

        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-map",
            "0:a:0",  # First audio stream only (skip spatial audio)
            "-ac",
            "1",  # Convert to mono
            "-ar",
            "16000",  # 16kHz sample rate (sufficient for silence detection)
            "-acodec",
            "pcm_s16le",
            "-loglevel",
            "error",
            tmp_path,
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

        if result.returncode != 0:
            logger.debug(f"ffmpeg audio extraction failed: {result.stderr}")
            return None

        # Read the WAV file
        import wave

        with wave.open(tmp_path, "rb") as wav:
            sample_rate = wav.getframerate()
            n_frames = wav.getnframes()
            audio_data = wav.readframes(n_frames)

        # Convert to numpy array
        audio_array = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32)
        audio_array /= 32768.0  # Normalize to [-1, 1]

        # Cleanup temp file
        Path(tmp_path).unlink(missing_ok=True)

        return audio_array, sample_rate

    except Exception as e:
        logger.debug(f"Audio extraction error: {e}")
        return None


def detect_silence_gaps(
    video_path: Path,
    threshold_db: float = -30.0,
    min_silence_duration: float = 0.3,
    window_size: float = 0.1,
) -> list[tuple[float, float]]:
    """Detect silence gaps in a video's audio track.

    Uses ffmpeg with explicit stream selection to handle iPhone videos
    with spatial audio (apac codec) that moviepy can't process.

    Args:
        video_path: Path to video file.
        threshold_db: Volume threshold in dB below which is considered silence.
        min_silence_duration: Minimum silence duration to register (seconds).
        window_size: Analysis window size in seconds.

    Returns:
        List of (start, end) tuples for silence gaps.
    """
    # Try ffmpeg-based extraction first (handles iPhone spatial audio)
    result = _extract_audio_with_ffmpeg(video_path)

    if result is not None:
        audio_array, sample_rate = result
        return _analyze_audio_for_silence(
            audio_array, sample_rate, threshold_db, min_silence_duration, window_size
        )

    # Fall back to moviepy if ffmpeg extraction fails
    logger.debug("Falling back to moviepy for audio extraction")
    return _detect_silence_gaps_moviepy(video_path, threshold_db, min_silence_duration, window_size)


def _analyze_audio_for_silence(
    audio_array: np.ndarray,
    sample_rate: int,
    threshold_db: float,
    min_silence_duration: float,
    window_size: float,
) -> list[tuple[float, float]]:
    """Analyze audio array for silence gaps.

    Args:
        audio_array: Audio samples as numpy array.
        sample_rate: Sample rate in Hz.
        threshold_db: Volume threshold in dB.
        min_silence_duration: Minimum silence duration.
        window_size: Analysis window size.

    Returns:
        List of (start, end) tuples for silence gaps.
    """
    samples_per_window = int(sample_rate * window_size)
    num_windows = len(audio_array) // samples_per_window
    duration = len(audio_array) / sample_rate

    silence_gaps = []
    silence_start = None

    for i in range(num_windows):
        start_sample = i * samples_per_window
        end_sample = start_sample + samples_per_window
        window = audio_array[start_sample:end_sample]

        # Calculate RMS energy
        rms = np.sqrt(np.mean(window**2))

        # Convert to dB (with small epsilon to avoid log(0))
        db = 20 * np.log10(rms + 1e-10)

        time_pos = i * window_size

        if db < threshold_db:
            # In silence
            if silence_start is None:
                silence_start = time_pos
        else:
            # Sound detected
            if silence_start is not None:
                silence_duration = time_pos - silence_start
                if silence_duration >= min_silence_duration:
                    silence_gaps.append((silence_start, time_pos))
                silence_start = None

    # Handle trailing silence
    if silence_start is not None:
        silence_duration = duration - silence_start
        if silence_duration >= min_silence_duration:
            silence_gaps.append((silence_start, duration))

    logger.debug(f"Found {len(silence_gaps)} silence gaps")
    return silence_gaps


def _detect_silence_gaps_moviepy(
    video_path: Path,
    threshold_db: float,
    min_silence_duration: float,
    window_size: float,
) -> list[tuple[float, float]]:
    """Detect silence gaps using moviepy (legacy fallback).

    Args:
        video_path: Path to video file.
        threshold_db: Volume threshold in dB.
        min_silence_duration: Minimum silence duration.
        window_size: Analysis window size.

    Returns:
        List of (start, end) tuples for silence gaps.
    """
    try:
        try:
            from moviepy.editor import VideoFileClip
        except ImportError:
            from moviepy import VideoFileClip
    except ImportError:
        logger.debug("moviepy not available for silence detection")
        return []

    try:
        with VideoFileClip(str(video_path)) as video:
            if video.audio is None:
                logger.debug(f"No audio track in {video_path}")
                return []

            # Get audio parameters
            fps = video.audio.fps

            # Extract audio as numpy array
            audio_array = video.audio.to_soundarray(fps=fps)

            # Convert to mono if stereo
            if len(audio_array.shape) > 1:
                audio_array = np.mean(audio_array, axis=1)

            return _analyze_audio_for_silence(
                audio_array, int(fps), threshold_db, min_silence_duration, window_size
            )

    except Exception as e:
        logger.debug(f"moviepy silence detection failed: {e}")
        return []


def find_nearest_silence(
    time_pos: float,
    silence_gaps: list[tuple[float, float]],
    max_adjustment: float = 1.0,
) -> float | None:
    """Find the nearest silence gap boundary to a given time position.

    Args:
        time_pos: Time position to adjust.
        silence_gaps: List of (start, end) silence gap tuples.
        max_adjustment: Maximum time adjustment allowed (seconds).

    Returns:
        Adjusted time position, or None if no suitable silence found.
    """
    if not silence_gaps:
        return None

    best_pos = None
    best_distance = float("inf")

    for gap_start, gap_end in silence_gaps:
        # Check distance to gap start
        dist_to_start = abs(time_pos - gap_start)
        if dist_to_start < best_distance and dist_to_start <= max_adjustment:
            best_distance = dist_to_start
            best_pos = gap_start

        # Check distance to gap end
        dist_to_end = abs(time_pos - gap_end)
        if dist_to_end < best_distance and dist_to_end <= max_adjustment:
            best_distance = dist_to_end
            best_pos = gap_end

        # Also consider middle of gap for longer silences
        if gap_end - gap_start > 0.5:
            gap_mid = (gap_start + gap_end) / 2
            dist_to_mid = abs(time_pos - gap_mid)
            if dist_to_mid < best_distance and dist_to_mid <= max_adjustment:
                best_distance = dist_to_mid
                best_pos = gap_mid

    return best_pos


def adjust_segment_to_silence(
    start: float,
    end: float,
    silence_gaps: list[tuple[float, float]],
    max_adjustment: float = 1.0,
    min_duration: float = 2.0,
) -> tuple[float, float]:
    """Adjust segment boundaries to align with silence gaps.

    Tries to snap start/end to natural break points while maintaining
    minimum duration.

    Args:
        start: Original start time.
        end: Original end time.
        silence_gaps: List of silence gap (start, end) tuples.
        max_adjustment: Maximum adjustment per boundary (seconds).
        min_duration: Minimum segment duration to maintain.

    Returns:
        Tuple of (adjusted_start, adjusted_end).
    """
    if not silence_gaps:
        return start, end

    original_duration = end - start

    # Try to adjust start to nearest silence
    new_start = find_nearest_silence(start, silence_gaps, max_adjustment)
    if new_start is not None:
        start = new_start

    # Try to adjust end to nearest silence
    new_end = find_nearest_silence(end, silence_gaps, max_adjustment)
    if new_end is not None:
        end = new_end

    # Ensure minimum duration is maintained
    if end - start < min_duration:
        # Prefer to extend rather than shrink
        if original_duration >= min_duration:
            # Try to restore original duration
            mid = (start + end) / 2
            half_duration = original_duration / 2
            start = mid - half_duration
            end = mid + half_duration
        else:
            # Extend to minimum
            mid = (start + end) / 2
            start = mid - min_duration / 2
            end = mid + min_duration / 2

    return start, end
