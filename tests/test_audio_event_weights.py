"""Consistency of the curated audio event weight table.

PANNs is multi-label and fires the generic and the specific class for the same
sound, but the table names only some members of each family. Everything it misses
silently falls through to the 0.2 default, which is how the loudest kind of
laughter ended up weighted below plain speech.
"""

from __future__ import annotations

import pytest

from immich_memories.analysis.segment_generation import score_segment_audio
from immich_memories.audio.audio_models import AUDIO_EVENT_WEIGHTS, AudioAnalysisResult, AudioEvent


def test_belly_laugh_is_not_worth_less_than_speech():
    """`Belly laugh` fell through to the 0.2 default, below `Speech` at 0.4.

    It is a sibling of `Laughter` (1.0) describing a louder version of the same
    event, so the table was expressing the opposite of its stated intent.
    """
    belly = AudioAnalysisResult(events=[AudioEvent("Belly laugh", 0.0, 5.0, confidence=0.8)])
    speech = AudioAnalysisResult(events=[AudioEvent("Speech", 0.0, 5.0, confidence=0.8)])

    assert (
        score_segment_audio(0.0, 5.0, belly)["score"]
        > score_segment_audio(0.0, 5.0, speech)["score"]
    )


def test_every_weighted_class_is_a_real_audioset_class():
    """A key that matches no class is dead weight nobody notices.

    `"Race car, racing car"` was never an AudioSet label -- the real one is
    `"Race car, auto racing"` -- so a class the table rated 0.7 was scored at the
    0.2 default instead. Checking the whole table against the model's own label
    list is the only way this class of typo surfaces.
    """
    panns = pytest.importorskip("panns_inference", reason="requires the audio-ml extra")

    unknown = sorted(set(AUDIO_EVENT_WEIGHTS) - set(panns.labels))
    assert not unknown, f"weight table names classes AudioSet does not have: {unknown}"
