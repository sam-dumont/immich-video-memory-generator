"""Behaviour of `score_segment_audio`, the audio term that ranks segments.

`_calculate_audio_score` carries a laughter bonus but is never used for ranking.
`score_segment_audio` is, and had no laughter term at all, so laughter's 1.0
weight was averaged against speech and music until it disappeared. Measured over
3708 real candidate segments it separated laughter from non-laughter at AUC 0.50.

These tests fix the contract of the replacement term. The evidence that it
improves ranking is the benchmark, not these tests -- a synthetic segment cannot
demonstrate a distributional property.
"""

from __future__ import annotations

from immich_memories.analysis.segment_generation import score_segment_audio
from immich_memories.audio.audio_models import AudioAnalysisResult, AudioEvent


def test_laughter_outranks_equally_weighted_speech():
    """Laughter must beat non-laughter of identical weighted confidence.

    Laughter (weight 1.0) at confidence 0.4 and Speech (weight 0.4) at confidence
    1.0 both contribute 0.4 per second, so the duration-weighted mean cannot tell
    them apart. Before the laughter term these scored identically -- which is the
    ranking defect reduced to two events.
    """
    laughing = AudioAnalysisResult(events=[AudioEvent("Laughter", 0.0, 5.0, confidence=0.4)])
    talking = AudioAnalysisResult(events=[AudioEvent("Speech", 0.0, 5.0, confidence=1.0)])

    assert (
        score_segment_audio(0.0, 10.0, laughing)["score"]
        > score_segment_audio(0.0, 10.0, talking)["score"]
    )


def test_chuckle_counts_as_laughter():
    """AudioSet's "Chuckle, chortle" is laughter and must be treated as such.

    The old flag tested for "laugh" or "giggle" only, so this class -- which PANNs
    emits often -- set has_laughter False while the separate bonus in
    _calculate_audio_score, testing only "laugh", also missed it.
    """
    result = AudioAnalysisResult(events=[AudioEvent("Chuckle, chortle", 0.0, 4.0, confidence=0.5)])

    assert score_segment_audio(0.0, 10.0, result)["has_laughter"]


def test_silence_does_not_outrank_real_audio():
    """A segment where nothing was detected must not beat one with real content.

    The no-events return used to be 0.5 while the live formula's output over 3708
    real candidate segments had median 0.182 and maximum 0.627 -- so the "no
    information" constant beat 98.2% of segments that actually contained audio,
    and every ranking the audio term participated in was decided by the absence
    of evidence rather than by evidence.
    """
    nothing = AudioAnalysisResult(events=[])
    speech = AudioAnalysisResult(events=[AudioEvent("Speech", 0.0, 6.0, confidence=0.8)])
    laughter = AudioAnalysisResult(events=[AudioEvent("Laughter", 0.0, 1.0, confidence=0.5)])

    quiet_score = score_segment_audio(0.0, 6.0, nothing)["score"]
    assert quiet_score < score_segment_audio(0.0, 6.0, speech)["score"]
    assert quiet_score < score_segment_audio(0.0, 6.0, laughter)["score"]


def test_more_laughter_beats_more_laughter_labels():
    """Actual laughter duration must win over a shorter laugh that fires more labels.

    Since #291 each tracked class keeps its own event, so a single laugh emits
    Laughter, Giggle and Belly laugh over the same two seconds. Summing event
    durations counts that laugh three times, letting a two-second laugh out-earn
    a genuine four-second one. The term has to measure the union of laughter
    spans, not their sum.
    """
    brief_but_noisy = AudioAnalysisResult(
        events=[
            AudioEvent("Laughter", 0.0, 2.0, confidence=0.5),
            AudioEvent("Laughter", 0.0, 2.0, confidence=0.5),
            AudioEvent("Laughter", 0.0, 2.0, confidence=0.5),
        ]
    )
    genuinely_longer = AudioAnalysisResult(
        events=[AudioEvent("Laughter", 0.0, 4.0, confidence=0.5)]
    )

    assert (
        score_segment_audio(0.0, 30.0, genuinely_longer)["score"]
        > score_segment_audio(0.0, 30.0, brief_but_noisy)["score"]
    )
