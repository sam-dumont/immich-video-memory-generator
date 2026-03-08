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
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# Audio event categories and their weights for scoring
# Higher weight = more interesting for memory videos
AUDIO_EVENT_WEIGHTS = {
    # Laughter (highly desirable)
    "Laughter": 1.0,
    "Giggle": 0.95,
    "Chuckle, chortle": 0.9,
    "Baby laughter": 1.0,
    "Child speech, kid speaking": 0.85,
    # Positive sounds
    "Cheering": 0.8,
    "Clapping": 0.7,
    "Applause": 0.7,
    "Crowd": 0.5,
    "Whoop": 0.75,
    # Baby sounds
    "Baby cry, infant cry": 0.6,  # Can be memorable but not always positive
    "Babbling": 0.7,
    # Speech (moderate - indicates conversation)
    "Speech": 0.4,
    "Conversation": 0.5,
    "Narration, monologue": 0.3,
    "Singing": 0.8,
    "Children shouting": 0.6,
    # Music (can be good)
    "Music": 0.4,
    "Musical instrument": 0.5,
    # Nature sounds (atmospheric)
    "Bird": 0.3,
    "Ocean": 0.3,
    "Wind": 0.2,
    "Water": 0.3,
    # Negative/neutral (lower priority)
    "Silence": 0.0,
    "White noise": 0.0,
    "Static": 0.0,
    "Noise": 0.1,
}

# Events that shouldn't be cut during (provide smooth boundaries)
PROTECTED_EVENTS = {
    "Laughter",
    "Giggle",
    "Chuckle, chortle",
    "Baby laughter",
    "Speech",
    "Singing",
    "Cheering",
    "Applause",
}


@dataclass
class AudioEvent:
    """A detected audio event in a video segment."""

    event_class: str
    start_time: float
    end_time: float
    confidence: float

    @property
    def duration(self) -> float:
        return self.end_time - self.start_time

    @property
    def weight(self) -> float:
        """Get the interest weight for this event type."""
        return AUDIO_EVENT_WEIGHTS.get(self.event_class, 0.2)

    @property
    def is_protected(self) -> bool:
        """Check if this event should not be cut during."""
        return self.event_class in PROTECTED_EVENTS


@dataclass
class AudioAnalysisResult:
    """Result of audio content analysis for a video segment."""

    events: list[AudioEvent] = field(default_factory=list)
    audio_score: float = 0.0  # Overall score (0-1)
    has_laughter: bool = False
    has_speech: bool = False
    has_music: bool = False
    energy_profile: list[float] = field(default_factory=list)  # Energy over time
    protected_ranges: list[tuple[float, float]] = field(
        default_factory=list
    )  # Ranges to avoid cutting

    def get_safe_cut_points(self, min_gap: float = 0.3, max_gap: float = 2.0) -> list[float]:
        """Get time points that are safe for cutting (not during protected events).

        Args:
            min_gap: Minimum gap between cut points.
            max_gap: Maximum gap between cut points.

        Returns:
            List of safe cut point times.
        """
        if not self.protected_ranges:
            return []

        safe_points = []
        last_point = 0.0

        for start, end in sorted(self.protected_ranges):
            # Add cut point just before the protected range starts
            if start - last_point >= min_gap:
                safe_points.append(start - 0.1)

            # Add cut point just after the protected range ends
            if end > last_point:
                safe_points.append(end + 0.1)
                last_point = end

        return safe_points


class AudioContentAnalyzer:
    """Analyze audio content in videos for scoring and boundary detection.

    Supports two modes:
    1. YAMNet-based classification (requires tensorflow/tensorflow_hub)
    2. Energy-based heuristics (fallback, always available)
    """

    def __init__(
        self,
        use_yamnet: bool = True,
        sample_rate: int = 16000,
        window_size: float = 0.5,
        min_confidence: float = 0.3,
        laughter_confidence: float = 0.2,  # Lower threshold for laughter (subtle sounds)
    ):
        """Initialize the audio content analyzer.

        Args:
            use_yamnet: Try to use YAMNet for classification (falls back if unavailable).
            sample_rate: Audio sample rate for analysis.
            window_size: Analysis window size in seconds.
            min_confidence: Minimum confidence threshold for event detection.
            laughter_confidence: Lower threshold for laughter/baby sounds (often quieter).
        """
        self.use_yamnet = use_yamnet
        self.sample_rate = sample_rate
        self.window_size = window_size
        self.min_confidence = min_confidence
        self.laughter_confidence = laughter_confidence

        self._yamnet_model = None
        self._yamnet_available = None
        self._class_names = None

    def _check_yamnet_available(self) -> bool:
        """Check if YAMNet is available and configure GPU if available."""
        if self._yamnet_available is not None:
            return self._yamnet_available

        try:
            import tensorflow as tf
            import tensorflow_hub as hub

            # Configure GPU based on hardware settings
            self._configure_tensorflow_gpu(tf)

            # Try to load YAMNet model
            self._yamnet_model = hub.load("https://tfhub.dev/google/yamnet/1")

            # Load class names
            class_map_path = self._yamnet_model.class_map_path().numpy().decode("utf-8")
            with open(class_map_path) as f:
                self._class_names = [line.strip().split(",")[2] for line in f][1:]

            self._yamnet_available = True
            logger.info("YAMNet audio classification available")
            return True

        except Exception as e:
            logger.warning(f"YAMNet audio classification not available: {e}")
            logger.warning(
                "Falling back to energy-based detection (less accurate for speech/laughter)"
            )
            self._yamnet_available = False
            return False

    def _configure_tensorflow_gpu(self, tf) -> None:
        """Configure TensorFlow GPU settings based on hardware config.

        Args:
            tf: TensorFlow module.
        """
        try:
            from immich_memories.config import get_config

            config = get_config()
            hw = config.hardware

            if not hw.enabled or not hw.gpu_analysis:
                # Disable GPU, use CPU only
                tf.config.set_visible_devices([], "GPU")
                logger.debug("TensorFlow: GPU disabled by config, using CPU")
                return

            # Get available GPUs
            gpus = tf.config.list_physical_devices("GPU")
            if not gpus:
                logger.debug("TensorFlow: No GPUs available")
                return

            # Configure GPU memory growth (prevents TF from allocating all GPU memory)
            for gpu in gpus:
                try:
                    tf.config.experimental.set_memory_growth(gpu, True)
                except RuntimeError as e:
                    logger.debug(f"Could not set memory growth: {e}")

            # Set memory limit if configured
            if hw.gpu_memory_limit > 0:
                try:
                    tf.config.set_logical_device_configuration(
                        gpus[hw.device_index],
                        [tf.config.LogicalDeviceConfiguration(memory_limit=hw.gpu_memory_limit)],
                    )
                    logger.debug(f"TensorFlow: GPU memory limit set to {hw.gpu_memory_limit}MB")
                except Exception as e:
                    logger.debug(f"Could not set GPU memory limit: {e}")

            # Select specific GPU if multi-GPU system
            if hw.device_index > 0 and len(gpus) > hw.device_index:
                try:
                    tf.config.set_visible_devices([gpus[hw.device_index]], "GPU")
                    logger.debug(f"TensorFlow: Using GPU {hw.device_index}")
                except Exception as e:
                    logger.debug(f"Could not select GPU {hw.device_index}: {e}")

            # Log GPU info
            if gpus:
                gpu_name = "unknown"
                try:
                    # Try to get GPU name
                    from tensorflow.python.client import device_lib

                    devices = device_lib.list_local_devices()
                    for d in devices:
                        if d.device_type == "GPU":
                            gpu_name = d.physical_device_desc
                            break
                except Exception:
                    pass
                logger.info(f"TensorFlow: Using GPU for audio analysis ({gpu_name})")

        except Exception as e:
            logger.debug(f"TensorFlow GPU configuration failed: {e}")

    def cleanup(self) -> None:
        """Release TensorFlow resources to free memory.

        Call this after completing audio analysis to free GPU memory
        and TensorFlow model caches.
        """
        import gc

        if self._yamnet_model is not None:
            del self._yamnet_model
            self._yamnet_model = None
            self._yamnet_available = None
            self._class_names = None

            # Clear TensorFlow session to release GPU memory
            try:
                import tensorflow as tf

                tf.keras.backend.clear_session()
                logger.debug("TensorFlow session cleared")
            except Exception as e:
                logger.debug(f"Could not clear TensorFlow session: {e}")

            gc.collect()
            logger.debug("AudioContentAnalyzer cleanup complete")

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

        # Try YAMNet first if enabled
        if self.use_yamnet and self._check_yamnet_available():
            return self._analyze_with_yamnet(audio_array, sample_rate, max_duration)

        # Fall back to energy-based analysis
        return self._analyze_with_energy(audio_array, sample_rate, max_duration)

    def _analyze_with_yamnet(
        self, audio_array: np.ndarray, sample_rate: int, max_duration: float
    ) -> AudioAnalysisResult:
        """Analyze audio using YAMNet classification.

        Args:
            audio_array: Audio samples.
            sample_rate: Sample rate in Hz.
            max_duration: Maximum duration to clamp event timestamps.

        Returns:
            AudioAnalysisResult with classified events.
        """
        try:
            # Resample if needed (YAMNet expects 16kHz)
            if sample_rate != 16000:
                # Simple resampling
                ratio = 16000 / sample_rate
                new_length = int(len(audio_array) * ratio)
                audio_array = np.interp(
                    np.linspace(0, len(audio_array), new_length),
                    np.arange(len(audio_array)),
                    audio_array,
                )

            # Run YAMNet inference
            scores, embeddings, spectrogram = self._yamnet_model(audio_array)
            scores = scores.numpy()

            # Each score row is 0.96 seconds of audio
            frame_duration = 0.96
            events = []
            energy_profile = []

            has_laughter = False
            has_speech = False
            has_music = False

            # Track continuous events
            current_event = None
            current_start = 0.0

            for i, frame_scores in enumerate(scores):
                time_pos = i * frame_duration

                # Get top class
                top_idx = np.argmax(frame_scores)
                top_score = frame_scores[top_idx]
                class_name = self._class_names[top_idx] if self._class_names else "Unknown"

                # Track energy (sum of all scores as proxy)
                energy_profile.append(float(np.sum(frame_scores)))

                # Check for laughter/baby sounds with lower threshold (they're often quieter)
                class_lower = class_name.lower()
                is_laughter_class = any(
                    x in class_lower for x in ["laugh", "giggle", "chuckle", "chortle"]
                )
                is_baby_positive = "baby" in class_lower and "cry" not in class_lower

                # Use lower threshold for laughter/baby sounds
                effective_threshold = (
                    self.laughter_confidence
                    if (is_laughter_class or is_baby_positive)
                    else self.min_confidence
                )

                if top_score >= effective_threshold:
                    # Check for specific categories
                    if is_laughter_class or is_baby_positive:
                        has_laughter = True
                        if top_score < self.min_confidence:
                            # Log when we detect subtle laughter
                            logger.debug(
                                f"Detected subtle laughter: {class_name} ({top_score:.2f})"
                            )
                    if "speech" in class_lower or "talk" in class_lower:
                        has_speech = True
                    if "music" in class_lower:
                        has_music = True

                    # Track event continuity
                    if current_event == class_name:
                        # Continue current event
                        pass
                    else:
                        # End previous event
                        if current_event is not None:
                            events.append(
                                AudioEvent(
                                    event_class=current_event,
                                    start_time=float(current_start),
                                    end_time=float(time_pos),
                                    confidence=float(top_score),  # Convert numpy to Python float
                                )
                            )
                        # Start new event
                        current_event = class_name
                        current_start = time_pos
                else:
                    # End current event if below threshold
                    if current_event is not None:
                        events.append(
                            AudioEvent(
                                event_class=current_event,
                                start_time=float(current_start),
                                end_time=float(time_pos),
                                confidence=float(top_score),  # Convert numpy to Python float
                            )
                        )
                        current_event = None

            # End final event
            if current_event is not None:
                events.append(
                    AudioEvent(
                        event_class=current_event,
                        start_time=float(current_start),
                        end_time=float(len(audio_array) / 16000),
                        confidence=float(frame_scores[np.argmax(frame_scores)]),
                    )
                )

            # Calculate overall score
            audio_score = self._calculate_audio_score(events)

            # Find protected ranges (events that shouldn't be cut during)
            # Clamp to max_duration to avoid timestamps beyond video end
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
                energy_profile=energy_profile,
                protected_ranges=protected_ranges,
            )

        except Exception as e:
            logger.warning(f"YAMNet analysis failed: {e}")
            return self._analyze_with_energy(audio_array, sample_rate, max_duration)

    def _analyze_with_energy(
        self, audio_array: np.ndarray, sample_rate: int, max_duration: float
    ) -> AudioAnalysisResult:
        """Analyze audio using energy-based heuristics.

        This is a fallback when YAMNet is not available.
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
            else:
                if current_event_start is not None:
                    # End event
                    event_duration = time_pos - current_event_start

                    # Classify based on energy patterns
                    # Short high-energy bursts are often laughter
                    if current_is_very_high and event_duration < 3.0:
                        event_class = "Laughter"
                        has_laughter = True
                    elif event_duration > 1.0:
                        event_class = "Speech"
                        has_speech = True
                    else:
                        event_class = "Sound"

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
            event_duration = time_pos - current_event_start

            if current_is_very_high and event_duration < 3.0:
                event_class = "Laughter"
                has_laughter = True
            elif event_duration > 1.0:
                event_class = "Speech"
                has_speech = True
            else:
                event_class = "Sound"

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


def adjust_boundaries_for_audio(
    start: float,
    end: float,
    audio_result: AudioAnalysisResult,
    max_adjustment: float = 0.5,
) -> tuple[float, float]:
    """Adjust segment boundaries to avoid cutting during protected audio events.

    Args:
        start: Original start time.
        end: Original end time.
        audio_result: Audio analysis result with protected ranges.
        max_adjustment: Maximum adjustment per boundary in seconds.

    Returns:
        Tuple of (adjusted_start, adjusted_end).
    """
    if not audio_result.protected_ranges:
        return start, end

    new_start = start
    new_end = end

    for range_start, range_end in audio_result.protected_ranges:
        # Check if start is inside a protected range
        if range_start < start < range_end:
            # Move start to after the protected range
            if range_end - start <= max_adjustment:
                new_start = range_end + 0.1
            # Or move start to before the protected range
            elif start - range_start <= max_adjustment:
                new_start = range_start - 0.1

        # Check if end is inside a protected range
        if range_start < end < range_end:
            # Move end to after the protected range
            if range_end - end <= max_adjustment:
                new_end = range_end + 0.1
            # Or move end to before the protected range
            elif end - range_start <= max_adjustment:
                new_end = range_start - 0.1

    # Ensure valid range
    if new_end <= new_start:
        return start, end  # Revert if adjustment made range invalid

    return max(0, new_start), new_end


def get_audio_content_score(video_path: Path) -> float:
    """Quick function to get audio content score for a video.

    Args:
        video_path: Path to video file.

    Returns:
        Audio score between 0 and 1.
    """
    analyzer = AudioContentAnalyzer(use_yamnet=True)
    result = analyzer.analyze(video_path)
    return result.audio_score
