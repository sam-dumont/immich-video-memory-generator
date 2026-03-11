"""PANNs-based audio classification mixin.

Replaces TensorFlow/YAMNet with PyTorch/PANNs for audio event detection.
PANNs (Pretrained Audio Neural Networks) uses the same AudioSet ontology
(527 classes) but runs on PyTorch — no TensorFlow dependency required.
"""

from __future__ import annotations

import gc
import logging

import numpy as np

from immich_memories.audio.audio_models import (
    AudioAnalysisResult,
    AudioEvent,
    classify_audio_event,
)

logger = logging.getLogger(__name__)


class PANNsAnalysisMixin:
    """Mixin providing PANNs-based audio classification methods."""

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

            if meets_threshold:
                if current_event != class_name:
                    if current_event is not None:
                        events.append(
                            AudioEvent(
                                event_class=current_event,
                                start_time=float(current_start),
                                end_time=float(time_pos),
                                confidence=float(top_score),
                            )
                        )
                    current_event = class_name
                    current_start = time_pos
            elif current_event is not None:
                events.append(
                    AudioEvent(
                        event_class=current_event,
                        start_time=float(current_start),
                        end_time=float(time_pos),
                        confidence=float(top_score),
                    )
                )
                current_event = None

        # End final event
        if current_event is not None:
            last_scores = scores[-1]
            events.append(
                AudioEvent(
                    event_class=current_event,
                    start_time=float(current_start),
                    end_time=float(audio_length_samples / 32000),
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

            # Run PANNs SED inference → (1, time_steps, 527)
            framewise_output = self._panns_model.inference(audio_batch)

            # Squeeze batch dim → (time_steps, 527)
            scores = framewise_output[0]
            num_frames = scores.shape[0]
            audio_duration = len(audio_array) / 32000
            frame_duration = audio_duration / num_frames if num_frames > 0 else 1.0

            events, energy_profile, detected_categories = self._collect_events(
                scores, self._class_names, frame_duration, len(audio_array)
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
