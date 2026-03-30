"""Tests for segment-wise waveform reversal (privacy audio)."""

from __future__ import annotations

import numpy as np

from immich_memories.processing.privacy_audio import reverse_speech_segments


class TestReverseSegments:
    """Core reversal logic produces unintelligible but audible output."""

    def test_output_same_shape_as_input(self):
        audio = np.random.randn(48000, 2).astype(np.float32)
        result = reverse_speech_segments(audio, sample_rate=48000)
        assert result.shape == audio.shape

    def test_output_same_shape_mono(self):
        audio = np.random.randn(48000).astype(np.float32)
        result = reverse_speech_segments(audio, sample_rate=48000)
        assert result.shape == audio.shape

    def test_not_identical_to_input(self):
        """Reversal must actually change the signal."""
        audio = np.sin(np.linspace(0, 100 * np.pi, 48000))
        audio = np.column_stack([audio, audio])
        result = reverse_speech_segments(audio, sample_rate=48000)
        assert not np.allclose(result, audio)

    def test_preserves_energy(self):
        """Output RMS should be within 20% of input — reversal doesn't mute."""
        audio = np.random.randn(96000, 2).astype(np.float32)
        result = reverse_speech_segments(audio, sample_rate=48000)
        input_rms = np.sqrt(np.mean(audio**2))
        output_rms = np.sqrt(np.mean(result**2))
        assert output_rms > input_rms * 0.8, "Output too quiet — reversal should preserve energy"

    def test_empty_audio_returns_empty(self):
        audio = np.array([], dtype=np.float32)
        result = reverse_speech_segments(audio, sample_rate=48000)
        assert len(result) == 0

    def test_short_audio_shorter_than_segment(self):
        """Audio shorter than one segment still gets reversed."""
        audio = np.random.randn(100, 2).astype(np.float32)
        result = reverse_speech_segments(audio, sample_rate=48000)
        assert result.shape == audio.shape

    def test_segment_boundaries_no_clicks(self):
        """Adjacent segment boundaries should be smooth (no large jumps)."""
        # Generate a smooth signal
        t = np.linspace(0, 2 * np.pi * 10, 48000)
        audio = np.sin(t)[:, np.newaxis].repeat(2, axis=1).astype(np.float32)

        result = reverse_speech_segments(audio, sample_rate=48000, segment_ms=200, overlap_ms=10)

        # Check for clicks: max sample-to-sample diff should be reasonable
        diffs = np.abs(np.diff(result, axis=0))
        max_diff = diffs.max()
        # A click would show as a diff > 1.0 on normalized audio
        assert max_diff < 1.5, f"Potential click detected: max diff = {max_diff}"

    def test_custom_segment_length(self):
        audio = np.random.randn(48000, 2).astype(np.float32)
        result_100 = reverse_speech_segments(audio, sample_rate=48000, segment_ms=100)
        result_400 = reverse_speech_segments(audio, sample_rate=48000, segment_ms=400)
        # Different segment sizes should produce different results
        assert not np.allclose(result_100, result_400)

    def test_correlation_drops_with_reversal(self):
        """Cross-correlation between input and output should be low.

        This is a proxy for intelligibility destruction: if the output
        is highly correlated with the input, the reversal isn't working.
        """
        np.random.seed(42)
        audio = np.random.randn(48000).astype(np.float32)
        result = reverse_speech_segments(audio, sample_rate=48000)
        correlation = np.abs(np.corrcoef(audio, result)[0, 1])
        assert correlation < 0.5, f"Too correlated: {correlation:.2f}"
