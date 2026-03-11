"""Audio content analysis for detecting laughter, speech, and interesting sounds.

Uses audio event classification to:
1. Detect interesting audio moments (laughter, baby sounds, cheering)
2. Score segments based on audio content
3. Provide natural audio boundaries (don't cut mid-laugh)
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from immich_memories.audio.audio_models import (
    AUDIO_EVENT_WEIGHTS,
    PROTECTED_EVENTS,
    AudioAnalysisResult,
    AudioEvent,
    adjust_boundaries_for_audio,
    get_audio_content_score,
)
from immich_memories.audio.energy_analysis import EnergyAnalysisMixin
from immich_memories.audio.panns_analysis import PANNsAnalysisMixin

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Re-export public API for backward compatibility
__all__ = [
    "AUDIO_EVENT_WEIGHTS",
    "PROTECTED_EVENTS",
    "AudioEvent",
    "AudioAnalysisResult",
    "AudioContentAnalyzer",
    "adjust_boundaries_for_audio",
    "get_audio_content_score",
]


class AudioContentAnalyzer(PANNsAnalysisMixin, EnergyAnalysisMixin):
    """Analyze audio content in videos for scoring and boundary detection.

    Supports two modes:
    1. PANNs-based classification (requires torch/panns-inference)
    2. Energy-based heuristics (fallback, always available)
    """

    def __init__(
        self,
        use_panns: bool = True,
        sample_rate: int = 32000,
        window_size: float = 0.5,
        min_confidence: float = 0.3,
        laughter_confidence: float = 0.2,  # Lower threshold for laughter (subtle sounds)
        # Backward compat: accept use_yamnet as alias
        use_yamnet: bool | None = None,
    ):
        """Initialize the audio content analyzer.

        Args:
            use_panns: Try to use PANNs for classification (falls back if unavailable).
            sample_rate: Audio sample rate for analysis.
            window_size: Analysis window size in seconds.
            min_confidence: Minimum confidence threshold for event detection.
            laughter_confidence: Lower threshold for laughter/baby sounds (often quieter).
            use_yamnet: Deprecated alias for use_panns.
        """
        self.use_panns = use_yamnet if use_yamnet is not None else use_panns
        self.sample_rate = sample_rate
        self.window_size = window_size
        self.min_confidence = min_confidence
        self.laughter_confidence = laughter_confidence

        self._panns_model = None
        self._panns_available = None
        self._class_names = None

    def _extract_audio(self, video_path: Path) -> tuple[np.ndarray, int] | None:
        """Extract audio from video file.

        Args:
            video_path: Path to video file.

        Returns:
            Tuple of (audio_array, sample_rate) or None if extraction fails.
        """
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                tmp_path = tmp.name

            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                str(video_path),
                "-map",
                "0:a:0",  # First audio stream only
                "-ac",
                "1",  # Mono
                "-ar",
                str(self.sample_rate),
                "-acodec",
                "pcm_s16le",
                "-loglevel",
                "error",
                tmp_path,
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

            if result.returncode != 0:
                logger.debug(f"Audio extraction failed: {result.stderr}")
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

    def analyze(self, video_path: Path, video_duration: float | None = None) -> AudioAnalysisResult:
        """Analyze audio content in a video.

        Args:
            video_path: Path to video file.
            video_duration: Optional video duration to clamp timestamps. If not provided,
                           uses audio length (which may be inaccurate due to resampling).

        Returns:
            AudioAnalysisResult with detected events and scores.
        """
        # Extract audio
        audio_result = self._extract_audio(video_path)
        if audio_result is None:
            return AudioAnalysisResult()

        audio_array, sample_rate = audio_result

        # Calculate audio duration from array (may differ from video duration)
        audio_duration = len(audio_array) / sample_rate
        # Use video duration if provided, otherwise use audio duration
        max_duration = video_duration if video_duration is not None else audio_duration

        if video_duration is not None and abs(audio_duration - video_duration) > 0.5:
            logger.debug(
                f"Audio/video duration mismatch: audio={audio_duration:.2f}s, video={video_duration:.2f}s"
            )

        # Try PANNs first if enabled
        if self.use_panns and self._check_panns_available():
            return self._analyze_with_panns(audio_array, sample_rate, max_duration)

        # Fall back to energy-based analysis
        return self._analyze_with_energy(audio_array, sample_rate, max_duration)

    def _calculate_audio_score(self, events: list[AudioEvent]) -> float:
        """Calculate overall audio score from detected events.

        Args:
            events: List of detected audio events.

        Returns:
            Score between 0 and 1.
        """
        if not events:
            return 0.3  # Baseline score for no detected events

        # Weight events by duration and importance
        total_weighted = 0.0
        total_duration = 0.0

        for event in events:
            weight = event.weight * event.confidence
            total_weighted += weight * event.duration
            total_duration += event.duration

        if total_duration == 0:
            return 0.3

        # Normalize score
        raw_score = total_weighted / total_duration

        # Bonus for laughter
        laughter_events = [e for e in events if "laugh" in e.event_class.lower()]
        if laughter_events:
            laughter_bonus = min(0.3, len(laughter_events) * 0.1)
            raw_score = min(1.0, raw_score + laughter_bonus)

        return min(1.0, max(0.0, raw_score))
