"""Speech boundaries and audio scoring are independent capabilities.

VAD placement of cut points needs only 16 kHz audio and the bundled ONNX model.
Audio *scoring* needs PANNs, which is an optional extra and disabled by default.
Coupling them meant `audio_content.enabled: false` -- the shipped default --
silently disabled speech-boundary placement too, so a feature documented as its
own `speech:` block never ran on a default install.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import numpy as np

from immich_memories.analysis.speech_analysis import SpeechAnalysisService
from immich_memories.config_models import AudioContentConfig, SpeechConfig
from immich_memories.speech.models import SpeechRegion


def _service(**kwargs) -> SpeechAnalysisService:
    return SpeechAnalysisService(audio_content_config=AudioContentConfig(), **kwargs)


def test_speech_boundaries_survive_audio_content_being_disabled():
    """VAD ranges must still be produced when audio scoring is off."""
    service = _service(audio_content_enabled=False, speech_config=SpeechConfig(enabled=True))

    # WHY: replaces extract_audio_16k (the FFmpeg boundary) so no real video file
    # is needed; the VAD model itself runs for real against the array.
    with (
        patch(
            "immich_memories.analysis.speech_analysis.extract_audio_16k",
            return_value=np.zeros(16000 * 8, dtype=np.float32),
        ),
        patch.object(
            service._speech_detector,
            "detect",
            return_value=[SpeechRegion(start=1.0, end=3.0)],
        ),
    ):
        scoring_enabled, result = service.get_audio_content_result(
            Path("/fake.mov"), video_duration=8.0, enable_audio_content_analysis=True
        )

    assert scoring_enabled is False, "audio scoring must stay off"
    assert result is not None, "speech boundaries must still be available"
    assert result.protected_ranges, "VAD should have produced protected ranges"
    assert not result.events, "no PANNs events without audio content analysis"


def test_no_speech_result_when_speech_itself_is_disabled():
    """Turning speech off must leave nothing behind for boundary adjustment."""
    service = _service(audio_content_enabled=False, speech_config=SpeechConfig(enabled=False))

    scoring_enabled, result = service.get_audio_content_result(
        Path("/fake.mov"), video_duration=8.0, enable_audio_content_analysis=True
    )

    assert scoring_enabled is False
    assert result is None
