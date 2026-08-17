"""The confidence gate around transcription, and the region cache that feeds it."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import numpy as np

from immich_memories.analysis.speech_analysis import SpeechAnalysisService, voiced_seconds
from immich_memories.config_models import AudioContentConfig, SpeechConfig, TranscriptionConfig
from immich_memories.speech.models import SpeechRegion
from immich_memories.speech.transcription import Transcript


def test_voiced_seconds_counts_only_the_overlap_with_the_segment():
    regions = [SpeechRegion(start=0.0, end=2.0), SpeechRegion(start=5.0, end=6.0)]

    assert voiced_seconds(regions, 1.0, 5.5) == 1.5


def test_voiced_seconds_is_zero_without_regions():
    assert voiced_seconds([], 0.0, 10.0) == 0.0


def test_regions_are_detected_once_per_video():
    """Two candidates in one video must not each run the VAD model."""
    service = SpeechAnalysisService(
        audio_content_config=AudioContentConfig(),
        speech_config=SpeechConfig(enabled=True),
    )

    # WHY: replaces extract_audio_16k, the FFmpeg boundary, so no real file is needed.
    with (
        patch(
            "immich_memories.analysis.speech_analysis.extract_audio_16k",
            return_value=np.zeros(16000 * 8, dtype=np.float32),
        ) as extract,
        # WHY: replaces the FireRedVAD ONNX model so the call count is observable.
        patch.object(
            service._speech_detector,
            "detect",
            return_value=[SpeechRegion(start=1.0, end=3.0)],
        ) as detect,
    ):
        first = service.detect_regions_cached(Path("/fake.mov"))
        second = service.detect_regions_cached(Path("/fake.mov"))

    assert first == second
    assert detect.call_count == 1
    assert extract.call_count == 1


class RecordingTranscriber:
    """# WHY: replaces the whisper.cpp model so the gate's decision to call or not
    call it is observable without running inference."""

    def __init__(self, result: Transcript | None = None):
        self.result = result or Transcript(text="bonjour", language="fr", confidence=0.9)
        self.calls: list[int] = []

    def transcribe(self, audio):
        self.calls.append(len(audio))
        return self.result


def _service(transcriber, **overrides) -> SpeechAnalysisService:
    return SpeechAnalysisService(
        audio_content_config=AudioContentConfig(),
        speech_config=SpeechConfig(enabled=True),
        transcription_config=TranscriptionConfig(enabled=True, languages=["fr"], **overrides),
        transcriber=transcriber,
    )


def _patched(service, regions):
    # WHY: replaces extract_audio_16k, the FFmpeg boundary.
    audio = patch(
        "immich_memories.analysis.speech_analysis.extract_audio_16k",
        return_value=np.zeros(16000 * 10, dtype=np.float32),
    )
    # WHY: replaces the FireRedVAD ONNX model with fixed regions.
    detect = patch.object(service._speech_detector, "detect", return_value=regions)
    return audio, detect


def test_thin_voice_activity_never_reaches_the_model():
    """The pre-ASR gate is the whole point: half a real library has no VAD regions."""
    transcriber = RecordingTranscriber()
    service = _service(transcriber, min_voiced_seconds=1.0)
    audio, detect = _patched(service, [SpeechRegion(start=2.0, end=2.4)])

    with audio, detect:
        result = service.transcribe_segment(Path("/fake.mov"), 2.0, 5.0)

    assert result is None
    assert transcriber.calls == [], "whisper must not be called at all"


def test_sufficient_voice_activity_transcribes_the_segment_slice():
    transcriber = RecordingTranscriber()
    service = _service(transcriber, min_voiced_seconds=1.0)
    audio, detect = _patched(service, [SpeechRegion(start=2.0, end=4.0)])

    with audio, detect:
        result = service.transcribe_segment(Path("/fake.mov"), 2.0, 5.0)

    assert result is not None
    assert result.text == "bonjour"
    assert transcriber.calls == [16000 * 3], "must receive the 3s slice, not the whole video"


def test_no_transcriber_means_no_transcription():
    """Disabled transcription yields no transcriber, so nothing is attempted.

    Note `transcriber=None` does NOT mean "no transcriber" -- it means "select one
    from config", which on a machine with the extra installed builds a real one and
    lazily loads whisper weights. Any test that does not want a model must disable
    transcription in config or inject a double.
    """
    service = SpeechAnalysisService(
        audio_content_config=AudioContentConfig(),
        speech_config=SpeechConfig(enabled=True),
        transcription_config=TranscriptionConfig(enabled=False, languages=["fr"]),
    )
    audio, detect = _patched(service, [SpeechRegion(start=0.0, end=8.0)])

    with audio, detect:
        assert service.transcribe_segment(Path("/fake.mov"), 0.0, 5.0) is None


def test_transcription_is_disabled_when_speech_is_disabled():
    """The VAD regions are the gate, so no VAD means no safe transcription."""
    transcriber = RecordingTranscriber()
    service = SpeechAnalysisService(
        audio_content_config=AudioContentConfig(),
        speech_config=SpeechConfig(enabled=False),
        transcription_config=TranscriptionConfig(enabled=True, languages=["fr"]),
        transcriber=transcriber,
    )

    with patch(
        # WHY: replaces extract_audio_16k, the FFmpeg boundary.
        "immich_memories.analysis.speech_analysis.extract_audio_16k",
        return_value=np.zeros(16000 * 10, dtype=np.float32),
    ):
        result = service.transcribe_segment(Path("/fake.mov"), 0.0, 5.0)

    assert result is None
    assert transcriber.calls == []


def test_a_failing_model_leaves_the_segment_untranscribed():
    class ExplodingTranscriber:
        """# WHY: replaces the whisper.cpp model to exercise the failure path."""

        def transcribe(self, audio):
            raise RuntimeError("model died")

    service = _service(ExplodingTranscriber())
    audio, detect = _patched(service, [SpeechRegion(start=0.0, end=8.0)])

    with audio, detect:
        assert service.transcribe_segment(Path("/fake.mov"), 0.0, 5.0) is None
