"""Tests for the speech/audio-content analysis service."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

from immich_memories.analysis.speech_analysis import (
    SpeechAnalysisService,
    protected_ranges_from_speech,
)
from immich_memories.audio.audio_models import AudioAnalysisResult, AudioEvent
from immich_memories.config_models_analysis import AudioContentConfig, SpeechConfig
from immich_memories.speech.models import SpeechRegion


class TestVadDerivedProtectedRanges:
    """Noisy audio must not collapse into a single protected range."""

    def test_two_utterances_yield_two_protected_ranges(self):
        regions = [SpeechRegion(0.5, 2.0), SpeechRegion(4.0, 6.0)]

        ranges = protected_ranges_from_speech(regions, max_duration=8.0)

        assert len(ranges) == 2
        assert ranges[0] == (0.5, 2.0)
        assert ranges[1] == (4.0, 6.0)

    def test_ranges_are_clamped_to_max_duration(self):
        regions = [SpeechRegion(1.0, 99.0)]

        ranges = protected_ranges_from_speech(regions, max_duration=10.0)

        assert ranges == [(1.0, 10.0)]


class TestSpeechDetectorConstruction:
    """SpeechAnalysisService builds a detector via `select_detector(speech_config)`."""

    def test_enabled_fireredvad_config_constructs_detector(self):
        from immich_memories.speech.fireredvad import FireRedSpeechDetector

        service = SpeechAnalysisService(
            audio_content_config=AudioContentConfig(),
            speech_config=SpeechConfig(enabled=True, vad_threshold=0.6, min_silence_ms=300),
        )

        assert isinstance(service._speech_detector, FireRedSpeechDetector)
        assert service._speech_detector.threshold == 0.6
        assert service._speech_detector.min_silence_ms == 300

    def test_disabled_config_skips_detector(self):
        service = SpeechAnalysisService(
            audio_content_config=AudioContentConfig(),
            speech_config=SpeechConfig(enabled=False),
        )

        assert service._speech_detector is None

    def test_missing_speech_config_defaults_to_fireredvad(self):
        # No speech_config passed -- SpeechConfig() defaults apply.
        from immich_memories.speech.fireredvad import FireRedSpeechDetector

        service = SpeechAnalysisService(audio_content_config=AudioContentConfig())

        assert isinstance(service._speech_detector, FireRedSpeechDetector)


class TestDetectRegionsCachedWithoutRuntime:
    """A detector that cannot run must not cost an ffmpeg extraction per clip."""

    def test_unavailable_runtime_skips_the_per_clip_audio_extraction(self, tmp_path: Path):
        from immich_memories.speech.fireredvad import FireRedSpeechDetector

        # WHY: stands in for FireRedVAD's runtime import, which succeeds here
        # because the speech extra is installed -- this is the bare install.
        with patch.object(FireRedSpeechDetector, "_load", return_value=False):
            service = SpeechAnalysisService(
                audio_content_config=AudioContentConfig(),
                speech_config=SpeechConfig(enabled=True),
            )

        # WHY: the per-clip ffmpeg extraction; not being called is the assertion.
        with patch("immich_memories.analysis.speech_analysis.extract_audio_16k") as extract:
            assert service.detect_regions_cached(tmp_path / "clip.mov") == []

        extract.assert_not_called()


class TestApplyVadRanges:
    """`apply_vad_ranges` replaces PANNs-derived protected ranges with VAD ones."""

    def _service(self, **kwargs) -> SpeechAnalysisService:
        return SpeechAnalysisService(audio_content_config=AudioContentConfig(), **kwargs)

    def test_disabled_speech_config_leaves_result_untouched(self):
        service = self._service(speech_config=SpeechConfig(enabled=False))
        result = AudioAnalysisResult(protected_ranges=[(0.0, 8.0)])

        updated = service.apply_vad_ranges(Path("/fake.mov"), result, video_duration=8.0)

        assert updated.protected_ranges == [(0.0, 8.0)]

    def test_extraction_failure_leaves_result_untouched(self):
        service = self._service()
        service._speech_detector = MagicMock()  # should never be reached
        result = AudioAnalysisResult(protected_ranges=[(0.0, 8.0)])

        # WHY: replaces extract_audio_16k (the FFmpeg boundary) so the "no
        # audio extracted" branch is exercised without a real video file.
        with patch("immich_memories.analysis.speech_analysis.extract_audio_16k", return_value=None):
            updated = service.apply_vad_ranges(Path("/fake.mov"), result, video_duration=8.0)

        assert updated.protected_ranges == [(0.0, 8.0)]
        service._speech_detector.detect.assert_not_called()

    def test_no_speech_regions_leaves_result_untouched(self):
        service = self._service()

        class _EmptyDetector:
            def detect(self, audio, sample_rate):
                return []

        service._speech_detector = _EmptyDetector()
        result = AudioAnalysisResult(protected_ranges=[(0.0, 8.0)])
        audio = np.zeros(16000 * 8, dtype=np.float32)

        with patch(
            "immich_memories.analysis.speech_analysis.extract_audio_16k", return_value=audio
        ):
            updated = service.apply_vad_ranges(Path("/fake.mov"), result, video_duration=8.0)

        assert updated.protected_ranges == [(0.0, 8.0)]

    def test_vad_regions_break_up_the_panns_blob(self):
        service = self._service()

        class _TwoUtteranceDetector:
            def detect(self, audio, sample_rate):
                return [SpeechRegion(0.5, 2.0), SpeechRegion(4.0, 6.0)]

        service._speech_detector = _TwoUtteranceDetector()
        # PANNs collapsed the whole clip into one protected range -- this is
        # exactly the blob VAD is meant to break up.
        result = AudioAnalysisResult(protected_ranges=[(0.0, 8.0)])
        audio = np.zeros(16000 * 8, dtype=np.float32)

        with patch(
            "immich_memories.analysis.speech_analysis.extract_audio_16k", return_value=audio
        ):
            updated = service.apply_vad_ranges(Path("/fake.mov"), result, video_duration=8.0)

        assert updated.protected_ranges == [(0.5, 2.0), (4.0, 6.0)]

    def test_laughter_keeps_its_protection_when_vad_finds_speech(self):
        # VAD's speech column never fires on laughter, so replacing every
        # protected range with VAD output used to leave a laugh unprotected
        # and a cut landing in the middle of it.
        service = self._service()

        class _OneUtteranceDetector:
            def detect(self, audio, sample_rate):
                return [SpeechRegion(4.2, 5.8)]

        service._speech_detector = _OneUtteranceDetector()
        result = AudioAnalysisResult(
            events=[
                AudioEvent("Laughter", 1.0, 2.5, 0.8),
                AudioEvent("Speech", 3.0, 7.0, 0.9),
                AudioEvent("Motor vehicle (road)", 0.0, 8.0, 0.7),
            ],
            protected_ranges=[(1.0, 2.5), (3.0, 7.0)],
        )
        audio = np.zeros(16000 * 8, dtype=np.float32)

        with patch(
            "immich_memories.analysis.speech_analysis.extract_audio_16k", return_value=audio
        ):
            updated = service.apply_vad_ranges(Path("/fake.mov"), result, video_duration=8.0)

        assert updated.protected_ranges == [(1.0, 2.5), (4.2, 5.8)]

    def test_laughter_only_clip_keeps_every_protected_range(self):
        service = self._service()

        class _LaughterOnlyDetector:
            def detect(self, audio, sample_rate):
                return [SpeechRegion(0.2, 0.4)]

        service._speech_detector = _LaughterOnlyDetector()
        result = AudioAnalysisResult(
            events=[
                AudioEvent("Baby laughter", 1.0, 2.5, 0.8),
                AudioEvent("Giggle", 5.0, 6.0, 0.7),
            ],
            protected_ranges=[(1.0, 2.5), (5.0, 6.0)],
        )
        audio = np.zeros(16000 * 8, dtype=np.float32)

        with patch(
            "immich_memories.analysis.speech_analysis.extract_audio_16k", return_value=audio
        ):
            updated = service.apply_vad_ranges(Path("/fake.mov"), result, video_duration=8.0)

        assert (1.0, 2.5) in updated.protected_ranges
        assert (5.0, 6.0) in updated.protected_ranges

    def test_extraction_runs_once_per_video_path(self):
        service = self._service()

        class _TwoUtteranceDetector:
            def detect(self, audio, sample_rate):
                return [SpeechRegion(0.5, 2.0), SpeechRegion(4.0, 6.0)]

        service._speech_detector = _TwoUtteranceDetector()
        audio = np.zeros(16000 * 8, dtype=np.float32)
        video_path = Path("/fake.mov")

        # WHY: replaces extract_audio_16k (the FFmpeg boundary) to prove the
        # service's own cache -- not FFmpeg's absence -- explains the count.
        with patch(
            "immich_memories.analysis.speech_analysis.extract_audio_16k", return_value=audio
        ) as extract:
            first = service.apply_vad_ranges(
                video_path, AudioAnalysisResult(protected_ranges=[(0.0, 8.0)]), 8.0
            )
            second = service.apply_vad_ranges(
                video_path, AudioAnalysisResult(protected_ranges=[(0.0, 8.0)]), 8.0
            )

        extract.assert_called_once()
        assert first.protected_ranges == [(0.5, 2.0), (4.0, 6.0)]
        assert second.protected_ranges == [(0.5, 2.0), (4.0, 6.0)]
        assert list(service._vad_audio_cache) == [str(video_path)]


class TestAnalyzeAudioContentAppliesVad:
    """`analyze` wires PANNs analysis through `apply_vad_ranges`."""

    def test_vad_override_is_cached_on_the_final_result(self, tmp_path: Path):
        video_path = tmp_path / "video.mp4"
        video_path.write_bytes(b"fake")

        # WHY: mock audio_analyzer -- PANNs classification needs the real
        # torch model; the panns_blob shape (one range spanning the clip)
        # is all this test needs.
        audio_analyzer = MagicMock()
        audio_analyzer.analyze.return_value = AudioAnalysisResult(protected_ranges=[(0.0, 8.0)])

        service = SpeechAnalysisService(
            audio_content_config=AudioContentConfig(),
            audio_analyzer=audio_analyzer,
        )

        class _TwoUtteranceDetector:
            def detect(self, audio, sample_rate):
                return [SpeechRegion(0.5, 2.0), SpeechRegion(4.0, 6.0)]

        service._speech_detector = _TwoUtteranceDetector()
        audio = np.zeros(16000 * 8, dtype=np.float32)

        with patch(
            "immich_memories.analysis.speech_analysis.extract_audio_16k", return_value=audio
        ):
            result = service.analyze(video_path, video_duration=8.0)

        assert result.protected_ranges == [(0.5, 2.0), (4.0, 6.0)]
        assert service._audio_analysis_cache[str(video_path)].protected_ranges == [
            (0.5, 2.0),
            (4.0, 6.0),
        ]


class TestAudioContentAnalysisBranches:
    """`run_audio_content_analysis` / `analyze`: disabled, failure, and cache-hit branches."""

    def test_returns_none_when_disabled(self):
        service = SpeechAnalysisService(
            audio_content_config=AudioContentConfig(), audio_content_enabled=False
        )
        result = service.run_audio_content_analysis(Path("/fake.mp4"), 10.0)
        assert result is None

    def test_returns_none_when_analysis_returns_none(self):
        service = SpeechAnalysisService(
            audio_content_config=AudioContentConfig(), audio_content_enabled=True
        )
        service.analyze = MagicMock(return_value=None)
        result = service.run_audio_content_analysis(Path("/fake.mp4"), 10.0)
        assert result is None

    def test_analyze_audio_content_catches_import_error(self):
        service = SpeechAnalysisService(
            audio_content_config=AudioContentConfig(), audio_content_enabled=True
        )
        # WHY: AudioContentAnalyzer import/init is an external ML dependency
        mock_audio_analyzer = MagicMock()
        mock_audio_analyzer.analyze.side_effect = RuntimeError("no panns")
        service._audio_analyzer = mock_audio_analyzer
        result = service.analyze(Path("/fake.mp4"), 10.0)
        assert result is None

    def test_analyze_audio_content_caches_result(self):
        service = SpeechAnalysisService(
            audio_content_config=AudioContentConfig(), audio_content_enabled=True
        )
        cached = AudioAnalysisResult(audio_score=0.9)
        service._audio_analysis_cache["/fake.mp4"] = cached
        result = service.analyze(Path("/fake.mp4"), 10.0)
        assert result is cached
