"""Audio content analysis for detecting laughter, speech, and interesting sounds.

Uses audio event classification to:
1. Detect interesting audio moments (laughter, baby sounds, cheering)
2. Score segments based on audio content
3. Provide natural audio boundaries (don't cut mid-laugh)
"""

from __future__ import annotations

import gc
import logging
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from immich_memories.audio.audio_models import (
    AUDIO_EVENT_WEIGHTS,
    PROTECTED_EVENTS,
    AudioAnalysisResult,
    AudioEvent,
    adjust_boundaries_for_audio,
    classify_audio_event,
    get_audio_content_score,
)

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


def _classify_energy_event(
    is_very_high: bool,
    event_duration: float,
    has_laughter: bool,
    has_speech: bool,
) -> tuple[str, bool, bool]:
    """Classify an energy burst into Laughter, Speech, or Sound.

    Returns (event_class, updated_has_laughter, updated_has_speech).
    """
    if is_very_high and event_duration < 3.0:
        return "Laughter", True, has_speech
    if event_duration > 1.0:
        return "Speech", has_laughter, True
    return "Sound", has_laughter, has_speech


class AudioContentAnalyzer:
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

    # =========================================================================
    # PANNs Analysis (from PANNsAnalysisMixin)
    # =========================================================================

    def _check_panns_available(self) -> bool:
        """Check if PANNs is available and load the model."""
        if self._panns_available is not None:
            return self._panns_available

        try:
            from panns_inference import SoundEventDetection, labels

            self._panns_model = SoundEventDetection(checkpoint_path=None, device="cpu")
            self._class_names = labels
            self._panns_available = True
            logger.info("PANNs audio classification available (%d classes)", len(labels))
            return True

        except Exception as e:
            logger.warning(f"PANNs audio classification not available: {e}")
            logger.warning(
                "Falling back to energy-based detection (less accurate for speech/laughter)"
            )
            self._panns_available = False
            return False

    def cleanup(self) -> None:
        """Release PANNs model to free memory."""
        if self._panns_model is not None:
            del self._panns_model
            self._panns_model = None
            self._panns_available = None
            self._class_names = None
            gc.collect()
            logger.debug("PANNs cleanup complete")

    def _classify_frame(
        self,
        class_name: str,
        top_score: float,
    ) -> tuple[bool, str | None]:
        """Classify a single audio frame and check confidence threshold.

        Args:
            class_name: Detected AudioSet class name.
            top_score: Confidence score for the top class.

        Returns:
            Tuple of (meets_threshold, category).
        """
        category = classify_audio_event(class_name)

        # Laughter and baby sounds use a lower threshold (they're often quieter)
        is_soft_category = category in ("laughter", "baby")
        effective_threshold = self.laughter_confidence if is_soft_category else self.min_confidence

        if top_score < effective_threshold:
            return False, None

        if is_soft_category and top_score < self.min_confidence:
            logger.debug(f"Detected subtle {category}: {class_name} ({top_score:.2f})")

        return True, category

    def _collect_events(
        self,
        scores: np.ndarray,
        class_names: list[str],
        frame_duration: float,
        audio_length_samples: int,
    ) -> tuple[list[AudioEvent], list[float], set[str]]:
        """Process score frames into events, energy profile, and category flags.

        Args:
            scores: Score array of shape (frames, classes).
            class_names: List of AudioSet class labels.
            frame_duration: Duration of each frame in seconds.
            audio_length_samples: Audio length in samples (for final event end time).

        Returns:
            Tuple of (events, energy_profile, detected_categories).
        """
        events: list[AudioEvent] = []
        energy_profile: list[float] = []
        detected_categories: set[str] = set()

        current_event: str | None = None
        current_start = 0.0

        for i, frame_scores in enumerate(scores):
            time_pos = i * frame_duration

            top_idx = int(np.argmax(frame_scores))
            top_score = float(frame_scores[top_idx])
            class_name = class_names[top_idx] if top_idx < len(class_names) else "Unknown"

            energy_profile.append(float(np.sum(frame_scores)))

            meets_threshold, category = self._classify_frame(class_name, top_score)

            if category:
                detected_categories.add(category)

            if meets_threshold and current_event != class_name:
                if current_event is not None:
                    events.append(
                        AudioEvent(
                            event_class=current_event,
                            start_time=current_start,
                            end_time=time_pos,
                            confidence=top_score,
                        )
                    )
                current_event = class_name
                current_start = time_pos
            elif not meets_threshold and current_event is not None:
                events.append(
                    AudioEvent(
                        event_class=current_event,
                        start_time=current_start,
                        end_time=time_pos,
                        confidence=top_score,
                    )
                )
                current_event = None

        # End final event
        if current_event is not None:
            last_scores = scores[-1]
            events.append(
                AudioEvent(
                    event_class=current_event,
                    start_time=current_start,
                    end_time=audio_length_samples / 32000,
                    confidence=float(last_scores[int(np.argmax(last_scores))]),
                )
            )

        return events, energy_profile, detected_categories

    def _analyze_with_panns(
        self, audio_array: np.ndarray, sample_rate: int, max_duration: float
    ) -> AudioAnalysisResult:
        """Analyze audio using PANNs classification.

        Args:
            audio_array: Audio samples (mono, float32, normalized to [-1, 1]).
            sample_rate: Sample rate in Hz.
            max_duration: Maximum duration to clamp event timestamps.

        Returns:
            AudioAnalysisResult with classified events.
        """
        try:
            # Resample if needed (PANNs expects 32kHz)
            if sample_rate != 32000:
                ratio = 32000 / sample_rate
                new_length = int(len(audio_array) * ratio)
                audio_array = np.interp(
                    np.linspace(0, len(audio_array), new_length),
                    np.arange(len(audio_array)),
                    audio_array,
                )

            # PANNs expects (batch, samples) shape
            audio_batch = audio_array[np.newaxis, :]

            # Run PANNs SED inference -> (1, time_steps, 527)
            framewise_output = self._panns_model.inference(audio_batch)

            # Squeeze batch dim -> (time_steps, 527)
            scores = framewise_output[0]
            num_frames = scores.shape[0]
            audio_duration = len(audio_array) / 32000
            frame_duration = audio_duration / num_frames if num_frames > 0 else 1.0

            events, energy_profile, detected_categories = self._collect_events(
                scores, self._class_names or [], frame_duration, len(audio_array)
            )

            audio_score = self._calculate_audio_score(events)

            # Extract boolean flags from categories for backward compatibility
            has_laughter = "laughter" in detected_categories or "baby" in detected_categories
            has_speech = "speech" in detected_categories
            has_music = "music" in detected_categories or "singing" in detected_categories

            # Find protected ranges, clamped to max_duration
            protected_ranges = [
                (
                    min(e.start_time, max_duration),
                    min(e.end_time, max_duration),
                )
                for e in events
                if e.is_protected and e.start_time < max_duration
            ]

            return AudioAnalysisResult(
                events=events,
                audio_score=audio_score,
                has_laughter=has_laughter,
                has_speech=has_speech,
                has_music=has_music,
                detected_categories=detected_categories,
                energy_profile=energy_profile,
                protected_ranges=protected_ranges,
            )

        except Exception as e:
            logger.warning(f"PANNs analysis failed: {e}")
            return self._analyze_with_energy(audio_array, sample_rate, max_duration)

    # =========================================================================
    # Energy Analysis (from EnergyAnalysisMixin)
    # =========================================================================

    def _analyze_with_energy(
        self, audio_array: np.ndarray, sample_rate: int, max_duration: float
    ) -> AudioAnalysisResult:
        """Analyze audio using energy-based heuristics.

        This is a fallback when PANNs is not available.
        Uses audio energy patterns to detect interesting moments.

        Args:
            audio_array: Audio samples.
            sample_rate: Sample rate in Hz.
            max_duration: Maximum duration to clamp event timestamps.

        Returns:
            AudioAnalysisResult with detected events.
        """
        samples_per_window = int(sample_rate * self.window_size)
        num_windows = len(audio_array) // samples_per_window

        events = []
        energy_profile = []

        # Analyze energy in windows
        energies = []
        for i in range(num_windows):
            start_sample = i * samples_per_window
            end_sample = start_sample + samples_per_window
            window = audio_array[start_sample:end_sample]

            # Calculate RMS energy
            rms = np.sqrt(np.mean(window**2))
            energies.append(rms)
            energy_profile.append(float(rms))

        if not energies:
            return AudioAnalysisResult(energy_profile=energy_profile)

        # Calculate statistics
        mean_energy = np.mean(energies)
        std_energy = np.std(energies)

        # Detect high-energy moments (potential laughter, speech, etc.)
        high_energy_threshold = mean_energy + 1.5 * std_energy
        very_high_threshold = mean_energy + 2.5 * std_energy

        current_event_start = None
        current_is_very_high = False

        has_laughter = False
        has_speech = False

        for i, energy in enumerate(energies):
            time_pos = i * self.window_size

            if energy > high_energy_threshold:
                if current_event_start is None:
                    current_event_start = time_pos
                    current_is_very_high = energy > very_high_threshold
                elif energy > very_high_threshold:
                    current_is_very_high = True
            elif current_event_start is not None:
                event_class, has_laughter, has_speech = _classify_energy_event(
                    current_is_very_high, time_pos - current_event_start, has_laughter, has_speech
                )
                events.append(
                    AudioEvent(
                        event_class=event_class,
                        start_time=current_event_start,
                        end_time=time_pos,
                        confidence=0.6 if current_is_very_high else 0.4,
                    )
                )
                current_event_start = None
                current_is_very_high = False

        # End final event
        if current_event_start is not None:
            time_pos = len(energies) * self.window_size
            event_class, has_laughter, has_speech = _classify_energy_event(
                current_is_very_high, time_pos - current_event_start, has_laughter, has_speech
            )
            events.append(
                AudioEvent(
                    event_class=event_class,
                    start_time=current_event_start,
                    end_time=time_pos,
                    confidence=0.5,
                )
            )

        # Calculate score
        audio_score = self._calculate_audio_score(events)

        # Find protected ranges, clamped to max_duration
        protected_ranges = [
            (
                min(e.start_time, max_duration),
                min(e.end_time, max_duration),
            )
            for e in events
            if e.is_protected and e.start_time < max_duration
        ]

        return AudioAnalysisResult(
            events=events,
            audio_score=audio_score,
            has_laughter=has_laughter,
            has_speech=has_speech,
            has_music=False,  # Can't detect with energy alone
            energy_profile=energy_profile,
            protected_ranges=protected_ranges,
        )
