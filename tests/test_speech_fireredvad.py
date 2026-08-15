"""Tests for the FireRedVAD-backed `SpeechDetector`.

`TestFireRedSpeechDetector` runs the real vendored ONNX model over
`tests/fixtures/speech/synthetic_speech_16k.npy` -- no mocks, skipped when
onnxruntime/kaldi-native-fbank aren't installed. The fixture is synthesised by
`tests/fixtures/speech/generate_synthetic_speech.py`; it contains no recording
of anyone. `TestFireRedSpeechDetectorMocked` mocks the onnxruntime/
kaldi_native_fbank import boundary (same idiom as the `panns_inference`
unavailable test in tests/test_audio.py) to exercise the load/detect paths
without the real packages.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest

from immich_memories.speech.fireredvad import (
    FireRedSpeechDetector,
    _extract_features,
    regions_from_probs,
)
from immich_memories.speech.models import SpeechRegion

FIXTURE = Path(__file__).parent / "fixtures" / "speech" / "synthetic_speech_16k.npy"

# The fixture is two ~1.2 s runs of syllables around a 0.5 s pause; see the
# generator for the exact layout.
PAUSE_START = 1.30
PAUSE_END = 1.70


def _fixture_audio() -> np.ndarray:
    """The fixture as the [-1, 1] float32 the detector's callers pass in."""
    return np.load(FIXTURE).astype(np.float32) / 32768.0


class TestFireRedSpeechDetector:
    """Real model, real feature pipeline -- the only tests that catch scale/CMVN drift."""

    @pytest.fixture(autouse=True)
    def _require_model(self):
        if not FireRedSpeechDetector().available:
            pytest.skip("onnxruntime or kaldi-native-fbank not installed")

    def test_detects_speech_and_keeps_the_pause(self):
        regions = FireRedSpeechDetector().detect(_fixture_audio(), 16000)

        covered = sum(region.duration for region in regions)
        assert covered > 2.0  # of a 3.0 s fixture
        assert not any(region.start < PAUSE_END and region.end > PAUSE_START for region in regions)

    def test_feature_pipeline_matches_the_vendored_models_normalisation(self):
        # These two numbers are the whole point of the fixture. `_INT16_SCALE`
        # and the inlined CMVN tables define an affine map from raw fbank to
        # model input; dropping the int16 scale or regenerating the tables from
        # a different cmvn.ark moves the mean by whole units. Silence alone
        # cannot catch that -- it comes back empty either way.
        features = _extract_features(_fixture_audio(), 16000)

        assert features.shape == (298, 80)
        assert float(features.mean()) == pytest.approx(-2.258, abs=0.02)
        assert float(features.std()) == pytest.approx(3.128, abs=0.02)

    def test_silence_yields_no_regions(self):
        assert FireRedSpeechDetector().detect(np.zeros(16000 * 3, dtype=np.float32), 16000) == []


class TestFireRedSpeechDetectorContract:
    """Guards that hold with or without the optional packages installed."""

    def test_unavailable_detector_returns_empty(self):
        detector = FireRedSpeechDetector()
        detector._available = False

        assert detector.detect(np.zeros(16000, dtype=np.float32), 16000) == []

    def test_wrong_sample_rate_is_rejected(self):
        with pytest.raises(ValueError, match="16000 Hz"):
            FireRedSpeechDetector().detect(np.zeros(48000, dtype=np.float32), 48000)


class TestRegionsFromProbs:
    """Pure hysteresis logic -- no ONNX/fbank boundary involved."""

    def test_all_silent_yields_no_regions(self):
        probs = np.zeros(50, dtype=np.float32)

        assert regions_from_probs(probs, threshold=0.4, min_silence_ms=200) == []

    def test_short_dip_does_not_split_one_utterance(self):
        # 30 speech frames, a 5-frame dip (< 20 frames = 200ms at 10ms/frame),
        # then 30 more speech frames -- must stay one region, not two.
        probs = np.concatenate([np.full(30, 0.9), np.full(5, 0.1), np.full(30, 0.9)]).astype(
            np.float32
        )

        regions = regions_from_probs(probs, threshold=0.4, min_silence_ms=200)

        assert regions == [SpeechRegion(0.0, 0.65)]

    def test_silence_longer_than_min_closes_the_region(self):
        # 20 speech frames, then 25 silent frames (> 200ms) -- region closes
        # at the last speech frame, not extended into the silence.
        probs = np.concatenate([np.full(20, 0.9), np.zeros(25)]).astype(np.float32)

        regions = regions_from_probs(probs, threshold=0.4, min_silence_ms=200)

        assert regions == [SpeechRegion(0.0, 0.20)]

    def test_trailing_speech_without_eof_silence_still_closes(self):
        probs = np.full(15, 0.9, dtype=np.float32)

        regions = regions_from_probs(probs, threshold=0.4, min_silence_ms=200)

        assert regions == [SpeechRegion(0.0, 0.15)]


class TestFireRedSpeechDetectorMocked:
    """Boundary-mocked load/detect paths (no real onnxruntime/kaldi-native-fbank needed)."""

    def test_load_import_error_reports_unavailable(self):
        detector = FireRedSpeechDetector()

        # WHY: forces `import onnxruntime` to raise ImportError so _load()'s
        # except branch is exercised even when onnxruntime happens to be
        # installed.
        with patch.dict("sys.modules", {"onnxruntime": None}):
            assert detector.available is False

    def test_load_success_caches_session_and_reports_available(self):
        detector = FireRedSpeechDetector()

        # WHY: mocks the onnxruntime/kaldi_native_fbank import boundary so
        # _load()'s success path is exercised without the real packages.
        fake_knf = SimpleNamespace()
        fake_ort = SimpleNamespace(InferenceSession=lambda *_a, **_kw: "fake-session")

        with patch.dict("sys.modules", {"kaldi_native_fbank": fake_knf, "onnxruntime": fake_ort}):
            assert detector.available is True

        assert detector._session == "fake-session"

    def test_detect_success_builds_regions_from_probs(self):
        detector = FireRedSpeechDetector(threshold=0.4, min_silence_ms=200)
        detector._available = True

        # WHY: mocks the onnxruntime session's `run()` and kaldi_native_fbank's
        # feature extraction so detect()'s probs-to-region conversion is
        # exercised without the real model/audio pipeline.
        probs = np.zeros((1, 40, 3), dtype=np.float32)
        probs[0, 5:35, 0] = 0.9  # 300ms of speech at 10ms/frame

        class _FakeSession:
            def run(self, _output_names, _inputs):
                return [probs]

        detector._session = _FakeSession()

        fake_fbank = SimpleNamespace(
            num_frames_ready=40,
            accept_waveform=lambda *_a, **_kw: None,
            get_frame=lambda _i: [0.0] * 80,
        )
        fake_knf = SimpleNamespace(
            FbankOptions=lambda: SimpleNamespace(
                frame_opts=SimpleNamespace(), mel_opts=SimpleNamespace()
            ),
            OnlineFbank=lambda _opts: fake_fbank,
        )

        with patch.dict("sys.modules", {"kaldi_native_fbank": fake_knf}):
            regions = detector.detect(np.zeros(16000, dtype=np.float32), 16000)

        assert len(regions) == 1
        assert regions[0].start == pytest.approx(0.05)
        assert regions[0].end == pytest.approx(0.35)
