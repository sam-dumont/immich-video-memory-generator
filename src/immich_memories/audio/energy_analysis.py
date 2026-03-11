"""Energy-based audio analysis mixin.

Provides fallback audio event detection using RMS energy heuristics
when PANNs is not available.
"""

from __future__ import annotations

import logging

import numpy as np

from immich_memories.audio.audio_models import AudioAnalysisResult, AudioEvent

logger = logging.getLogger(__name__)


class EnergyAnalysisMixin:
    """Mixin providing energy-based audio analysis methods."""

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
