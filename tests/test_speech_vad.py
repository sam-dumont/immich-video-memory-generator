"""Tests for VAD-derived speech regions.

`TestSilenceGaps` uses synthetic audio only, no mocks.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
from pydantic import ValidationError

from immich_memories.config_models import SpeechConfig
from immich_memories.speech.models import SpeechRegion
from immich_memories.speech.vad import extract_audio_16k, select_detector, silence_gaps


class TestSilenceGaps:
    def test_gap_between_two_regions(self):
        regions = [SpeechRegion(0.0, 1.0), SpeechRegion(3.0, 4.0)]

        gaps = silence_gaps(regions, duration=5.0)

        assert gaps == [(1.0, 3.0), (4.0, 5.0)]

    def test_leading_and_trailing_gaps_included(self):
        regions = [SpeechRegion(1.0, 2.0)]

        gaps = silence_gaps(regions, duration=4.0)

        assert gaps == [(0.0, 1.0), (2.0, 4.0)]

    def test_no_regions_is_one_whole_gap(self):
        gaps = silence_gaps([], duration=3.0)

        assert gaps == [(0.0, 3.0)]

    def test_unordered_input_is_sorted_before_gap_derivation(self):
        # Regions passed newest-first. Without the sorted() call in
        # silence_gaps, the cursor walk would process (3, 4) before (0, 1)
        # and produce a wrong, unclamped result -- see the module's git
        # history for the exact miscomputation this catches.
        regions = [SpeechRegion(3.0, 4.0), SpeechRegion(0.0, 1.0)]

        gaps = silence_gaps(regions, duration=5.0)

        assert gaps == [(1.0, 3.0), (4.0, 5.0)]

    def test_region_end_beyond_duration_does_not_emit_out_of_bounds_gap(self):
        # A region ending past `duration` (e.g. detected on a longer audio
        # slice) must not produce a gap entirely outside [0, duration].
        regions = [SpeechRegion(2.0, 15.0), SpeechRegion(20.0, 25.0)]

        gaps = silence_gaps(regions, duration=10.0)

        assert gaps == [(0.0, 2.0)]
        for start, end in gaps:
            assert 0.0 <= start <= 10.0
            assert 0.0 <= end <= 10.0


class TestSelectDetector:
    def test_disabled_config_returns_none(self):
        assert select_detector(SpeechConfig(enabled=False)) is None

    def test_enabled_config_returns_a_configured_fireredvad_detector(self):
        from immich_memories.speech.fireredvad import FireRedSpeechDetector

        detector = select_detector(
            SpeechConfig(enabled=True, vad_threshold=0.3, min_silence_ms=150)
        )

        assert isinstance(detector, FireRedSpeechDetector)
        assert detector.threshold == 0.3
        assert detector.min_silence_ms == 150

    def test_out_of_range_threshold_fails_validation(self):
        with pytest.raises(ValidationError):
            SpeechConfig(enabled=True, vad_threshold=1.5)

    def test_default_config_selects_fireredvad(self):
        from immich_memories.speech.fireredvad import FireRedSpeechDetector

        assert isinstance(select_detector(SpeechConfig()), FireRedSpeechDetector)


class TestExtractAudio16k:
    def test_missing_file_returns_none(self, tmp_path: Path):
        # Real ffmpeg invocation (no mock) -- it fails fast on a nonexistent
        # input and the function must degrade to None rather than raising.
        missing = tmp_path / "does-not-exist.mov"

        assert extract_audio_16k(missing) is None

    def test_parses_ffmpeg_stdout_as_mono_float32(self, tmp_path: Path):
        expected = np.array([0.0, 0.25, -0.5, 1.0], dtype=np.float32)
        fake_completed = subprocess.CompletedProcess(
            args=["ffmpeg"], returncode=0, stdout=expected.tobytes(), stderr=b""
        )

        # WHY: replaces the real ffmpeg subprocess so the test doesn't need a
        # real audio file -- only the stdout-parsing contract is under test.
        with patch("immich_memories.speech.vad.subprocess.run", return_value=fake_completed) as run:
            result = extract_audio_16k(tmp_path / "clip.mov")

        assert np.array_equal(result, expected)
        args = run.call_args.args[0]
        assert args[0] == "ffmpeg"
        assert "-map" in args and args[args.index("-map") + 1] == "0:a:0"

    def test_truncated_stdout_returns_none_instead_of_raising(self, tmp_path: Path):
        """A short read must degrade to None, not kill the clip with a ValueError.

        7 bytes is not a whole number of float32s, which is what a pipe cut
        mid-sample looks like.
        """
        truncated = subprocess.CompletedProcess(
            args=["ffmpeg"], returncode=0, stdout=b"\x00" * 7, stderr=b""
        )

        # WHY: replaces the real ffmpeg subprocess to produce a byte count that
        # no real encoder emits but a killed/broken pipe does.
        with patch("immich_memories.speech.vad.subprocess.run", return_value=truncated):
            assert extract_audio_16k(tmp_path / "clip.mov") is None

    def test_ffmpeg_failure_returns_none(self, tmp_path: Path):
        # WHY: replaces the real ffmpeg subprocess to force the
        # CalledProcessError branch without needing a corrupt real file.
        with patch(
            "immich_memories.speech.vad.subprocess.run",
            side_effect=subprocess.CalledProcessError(1, ["ffmpeg"]),
        ):
            result = extract_audio_16k(tmp_path / "clip.mov")

        assert result is None
