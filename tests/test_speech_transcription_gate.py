"""The confidence gate around transcription, and the region cache that feeds it."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import numpy as np

from immich_memories.analysis.speech_analysis import SpeechAnalysisService, voiced_seconds
from immich_memories.config_models import AudioContentConfig, SpeechConfig
from immich_memories.speech.models import SpeechRegion


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
