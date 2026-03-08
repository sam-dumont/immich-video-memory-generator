"""YAMNet-based audio classification mixin.

Provides YAMNet model loading, GPU configuration, and ML-based
audio event classification for AudioContentAnalyzer.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    pass

from immich_memories.audio.audio_models import AudioAnalysisResult, AudioEvent

logger = logging.getLogger(__name__)


class YAMNetAnalysisMixin:
    """Mixin providing YAMNet-based audio classification methods."""

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

    def _classify_yamnet_frame(
        self,
        class_name: str,
        top_score: float,
    ) -> tuple[bool, str | None]:
        """Classify a single YAMNet frame and determine if it meets the threshold.

        Args:
            class_name: Detected class name.
            top_score: Confidence score for the top class.

        Returns:
            Tuple of (meets_threshold, category) where category is one of
            "laughter", "speech", "music", or None.
        """
        class_lower = class_name.lower()
        is_laughter_class = any(x in class_lower for x in ["laugh", "giggle", "chuckle", "chortle"])
        is_baby_positive = "baby" in class_lower and "cry" not in class_lower

        effective_threshold = (
            self.laughter_confidence
            if (is_laughter_class or is_baby_positive)
            else self.min_confidence
        )

        if top_score < effective_threshold:
            return False, None

        category = None
        if is_laughter_class or is_baby_positive:
            category = "laughter"
            if top_score < self.min_confidence:
                logger.debug(f"Detected subtle laughter: {class_name} ({top_score:.2f})")
        elif "speech" in class_lower or "talk" in class_lower:
            category = "speech"
        elif "music" in class_lower:
            category = "music"

        return True, category

    def _collect_yamnet_events(
        self, scores: np.ndarray, audio_length: int
    ) -> tuple[list[AudioEvent], list[float], bool, bool, bool]:
        """Process YAMNet score frames into events and flags.

        Args:
            scores: YAMNet score array (frames x classes).
            audio_length: Length of the audio array (for final event end time).

        Returns:
            Tuple of (events, energy_profile, has_laughter, has_speech, has_music).
        """
        frame_duration = 0.96
        events: list[AudioEvent] = []
        energy_profile: list[float] = []
        has_laughter = False
        has_speech = False
        has_music = False

        current_event: str | None = None
        current_start = 0.0

        for i, frame_scores in enumerate(scores):
            time_pos = i * frame_duration

            top_idx = np.argmax(frame_scores)
            top_score = frame_scores[top_idx]
            class_name = self._class_names[top_idx] if self._class_names else "Unknown"

            energy_profile.append(float(np.sum(frame_scores)))

            meets_threshold, category = self._classify_yamnet_frame(class_name, top_score)

            if category == "laughter":
                has_laughter = True
            elif category == "speech":
                has_speech = True
            elif category == "music":
                has_music = True

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
                    end_time=float(audio_length / 16000),
                    confidence=float(last_scores[np.argmax(last_scores)]),
                )
            )

        return events, energy_profile, has_laughter, has_speech, has_music

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

            events, energy_profile, has_laughter, has_speech, has_music = (
                self._collect_yamnet_events(scores, len(audio_array))
            )

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
                has_music=has_music,
                energy_profile=energy_profile,
                protected_ranges=protected_ranges,
            )

        except Exception as e:
            logger.warning(f"YAMNet analysis failed: {e}")
            return self._analyze_with_energy(audio_array, sample_rate, max_duration)
