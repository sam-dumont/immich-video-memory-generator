"""Tests for VAD-derived speech regions.

`TestSilenceGaps` and `TestSileroSpeechDetector` use synthetic audio only, no
mocks. `TestSileroSpeechDetectorMocked` mocks the `silero_vad` module boundary
(via `sys.modules`, same idiom as the existing `panns_inference` unavailable
test in tests/test_audio.py) to exercise the load/detect success paths without
requiring the real package -- silero-vad hard-depends on torch+torchaudio, so
it isn't installed in the coverage-producing CI job.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from immich_memories.speech.models import SpeechRegion
from immich_memories.speech.vad import extract_audio_16k, silence_gaps


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


class TestSileroSpeechDetector:
    def test_pure_silence_yields_no_regions(self):
        from immich_memories.speech.vad import SileroSpeechDetector

        detector = SileroSpeechDetector()
        if not detector.available:
            import pytest

            pytest.skip("silero-vad not installed")

        silence = np.zeros(16000 * 3, dtype=np.float32)

        assert detector.detect(silence, 16000) == []

    def test_unavailable_detector_returns_empty(self):
        from immich_memories.speech.vad import SileroSpeechDetector

        detector = SileroSpeechDetector()
        detector._available = False

        assert detector.detect(np.zeros(16000, dtype=np.float32), 16000) == []


class TestSileroSpeechDetectorMocked:
    """Boundary-mocked load/detect success paths (no real silero-vad/torch needed)."""

    def test_load_import_error_reports_unavailable(self):
        from immich_memories.speech.vad import SileroSpeechDetector

        # WHY: forces `from silero_vad import ...` to raise ImportError so
        # _load()'s except branch is exercised even in environments where
        # silero-vad happens to be installed (same idiom as the existing
        # `patch.dict("sys.modules", {"panns_inference": None})` test in
        # tests/test_audio.py).
        detector = SileroSpeechDetector()

        with patch.dict("sys.modules", {"silero_vad": None}):
            assert detector.available is False

    def test_load_success_caches_model_and_reports_available(self):
        from immich_memories.speech.vad import SileroSpeechDetector

        # WHY: mocks the `silero_vad` import boundary so the success path in
        # _load() is exercised without the real package (which hard-depends
        # on torch+torchaudio and isn't installed in the coverage CI job).
        fake_module = SimpleNamespace(load_silero_vad=lambda **_kwargs: "fake-model")
        detector = SileroSpeechDetector()

        with patch.dict("sys.modules", {"silero_vad": fake_module}):
            assert detector.available is True

        assert detector._model == "fake-model"

    def test_detect_success_builds_regions_from_timestamps(self):
        from immich_memories.speech.vad import SileroSpeechDetector

        detector = SileroSpeechDetector()
        detector._available = True
        detector._model = "fake-model"

        # WHY: mocks the `silero_vad` import boundary so detect()'s
        # timestamp-to-SpeechRegion conversion is exercised without the real
        # package/model.
        fake_module = SimpleNamespace(
            get_speech_timestamps=lambda *_args, **_kwargs: [
                {"start": 0.5, "end": 1.5},
                {"start": 2.0, "end": 3.0},
            ]
        )

        with patch.dict("sys.modules", {"silero_vad": fake_module}):
            regions = detector.detect(np.zeros(16000, dtype=np.float32), 16000)

        assert regions == [SpeechRegion(0.5, 1.5), SpeechRegion(2.0, 3.0)]


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

    def test_ffmpeg_failure_returns_none(self, tmp_path: Path):
        # WHY: replaces the real ffmpeg subprocess to force the
        # CalledProcessError branch without needing a corrupt real file.
        with patch(
            "immich_memories.speech.vad.subprocess.run",
            side_effect=subprocess.CalledProcessError(1, ["ffmpeg"]),
        ):
            result = extract_audio_16k(tmp_path / "clip.mov")

        assert result is None
