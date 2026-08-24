"""How much of a candidate pool a run actually looked at.

Measured on a real April 2021 recap (#489): 149 candidates, 25 of them scored
by a real look and 124 by metadata alone — and 55% of the pool carried one
identical fallback score, so most of the ranking was list order wearing a
number. That run looked exactly like a confidently-ranked one. This is the
count that tells them apart.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from immich_memories.analysis.smart_pipeline import ClipWithSegment

# Below this share of the pool, the ranking is mostly metadata guesswork and a
# human eye is the last thing between a guess and the cut. A module constant
# rather than config: it describes when a run deserves a second look, which is
# not a preference a user should have to hold an opinion about.
THIN_COVERAGE = 0.60


@dataclass(frozen=True)
class AnalysisCoverage:
    """Of the final candidate pool, how many clips a real look scored.

    ``analyzed`` counts clips whose score came from visual analysis, fresh or
    replayed from cache. The remainder were ranked on metadata alone.
    """

    analyzed: int
    total: int

    @property
    def percent(self) -> int:
        return round(self.analyzed * 100 / self.total) if self.total else 0


def coverage_of(pool: Iterable[ClipWithSegment]) -> AnalysisCoverage:
    """Count how much of a candidate pool carries a real look.

    Not inlined at its call site: `phase_refine` sits on the Xenon C boundary,
    and one comprehension added to it ranks the whole function D.
    """
    clips = list(pool)
    return AnalysisCoverage(
        analyzed=sum(1 for clip in clips if clip.analyzed),
        total=len(clips),
    )


def thin_coverage_notice(coverage: AnalysisCoverage) -> str | None:
    """The one line worth saying when most of the pool was never looked at.

    ``None`` when coverage is healthy: a well-analyzed run should say nothing,
    or the warning stops meaning anything on the runs that need it.
    """
    if not coverage.total or coverage.analyzed >= coverage.total * THIN_COVERAGE:
        return None
    return (
        f"{coverage.analyzed} of {coverage.total} candidates ({coverage.percent}%) "
        f"were visually analyzed; the rest were picked on metadata. "
        f"Review recommended."
    )
