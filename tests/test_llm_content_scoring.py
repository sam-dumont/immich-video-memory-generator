"""Contract of the LLM content score, the one LLM output that reaches ranking.

`content_score` feeds `_compute_total_score`'s additive bonus, which is worth up
to `content_weight * 2` -- several times the entire dynamic range of the audio
term. Anything unbounded here decides the whole memory.
"""

from __future__ import annotations

from immich_memories.analysis.llm_response_parser import ContentAnalysis


def test_content_score_is_bounded_however_the_fields_were_set():
    """An out-of-range verdict must not produce an out-of-range score.

    The JSON path clamps interestingness and quality; the regex fallback for
    malformed responses assigned them raw. A model answering on a 0-10 scale
    therefore produced content_score 5.19 against a legitimate maximum of 1.0,
    turning a +0.21 bonus into +3.28 and handing one segment the whole memory.
    Clamping lives on the score itself so no future writer can reintroduce it.
    """
    absurd = ContentAnalysis(interestingness=8.5, quality=9.0, confidence=0.6)

    assert 0.0 <= absurd.content_score <= 1.0
