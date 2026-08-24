"""Content decides the score; technical quality adjusts it. Not the reverse.

Measured on 5,505 real analysed segments from the cache. Under the weighted
sum the live scorer uses — face 0.35, motion 0.20, stability 0.15, audio 0.15,
duration 0.15, content 0.00 — content contributes nothing and face is the
largest single term. A face filling 15% of frame scores the maximum, so a
selfie banks the biggest weight in the formula while a landscape banks none.

Ranking those segments both ways: people 39.4% -> 43.8% (worse, correctly),
landscape 71.3% -> 59.7%, animal 72.4% -> 58.3%. 232 of the 322 segments
demoted from the top decile are `people`.

A clip nothing has looked at keeps the technical-only score. It must never be
handed a mid-range content value: at these weights "unanalysed" would outrank
"analysed and judged poor", which is exactly backwards.
"""

import pytest

from immich_memories.analysis.scoring import blend_content_and_technical


def test_content_sets_the_level() -> None:
    """Two clips shot identically; the one that shows something wins."""
    good = blend_content_and_technical(content=0.9, technical=0.5)
    dull = blend_content_and_technical(content=0.2, technical=0.5)

    assert good > dull * 2, f"content barely mattered: {good:.3f} vs {dull:.3f}"


def test_technical_quality_cannot_rescue_empty_content() -> None:
    """A beautifully shot photo of a screen must not beat a shaky real moment."""
    polished_screen = blend_content_and_technical(content=0.2, technical=1.0)
    shaky_moment = blend_content_and_technical(content=0.8, technical=0.1)

    assert shaky_moment > polished_screen


def test_technical_quality_still_separates_equal_content() -> None:
    """It adjusts on top — two clips of the same thing, the steadier one wins."""
    steady = blend_content_and_technical(content=0.7, technical=0.9)
    shaky = blend_content_and_technical(content=0.7, technical=0.2)

    assert steady > shaky


def test_an_unanalysed_clip_keeps_its_technical_score() -> None:
    """No phantom mid-range content. Unknown must not outrank known-poor."""
    unknown = blend_content_and_technical(content=None, technical=0.6)
    known_poor = blend_content_and_technical(content=0.15, technical=0.6)

    assert unknown == pytest.approx(0.6), "an unseen clip was given invented content"
    assert unknown > known_poor, "known-poor should rank below unseen, not above"


def test_the_scale_stays_bounded() -> None:
    """Scores stay comparable to what the rest of selection expects."""
    for content in (0.0, 0.5, 1.0, None):
        for technical in (0.0, 0.5, 1.0):
            score = blend_content_and_technical(content=content, technical=technical)
            assert 0.0 <= score <= 1.0, (
                f"out of range: content={content} tech={technical} -> {score}"
            )
