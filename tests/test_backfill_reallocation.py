"""Freed seconds go to the next-ranked clip, never back to the one just dropped.

Measured in a real August month: dedup cut the set to 12, duration backfill
rebuilt it to 14, and the review then removed 2 again — the same clips going
round. clip_scaler.py records the same shape from a 967-asset trip: 39 in,
16 out, backfill rebuilt to 55.

The cause is the last rung of backfill's relaxation ladder. When nothing fits
the spacing rule it retries with enforce_temporal_spacing=False, and the
best-scoring thing available is precisely the near-duplicate dedup rejected.

Sam's rule: when the pool thins, the memory gets shorter. It does not get
padded with what was already refused. Fable's shaping: the freed seconds go
to the next-ranked candidate, and only genuine exhaustion shrinks the cut.
"""

from datetime import UTC, datetime
from types import SimpleNamespace

from immich_memories.analysis.clip_backfill import (
    _build_backfill_context,
    _is_backfill_candidate_admissible,
)
from immich_memories.analysis.smart_pipeline import PipelineConfig


def _clip(asset_id: str, minute: int, score: float, duration: float = 4.0):
    when = datetime(2011, 8, 4, 12, minute, tzinfo=UTC)
    return SimpleNamespace(
        clip=SimpleNamespace(
            asset=SimpleNamespace(
                id=asset_id, is_favorite=False, file_created_at=when, type="VIDEO"
            ),
            duration_seconds=duration,
        ),
        start_time=0.0,
        end_time=duration,
        score=score,
        analyzed=True,
    )


def _admissible(candidate, *, selected, refused, relax_spacing):
    context = _build_backfill_context(
        selected,
        config=PipelineConfig(),
        temporal_window=5.0,
        occupied_moments=[c.clip.asset.file_created_at for c in selected],
        refused_ids=frozenset(refused),
    )
    return _is_backfill_candidate_admissible(
        candidate,
        context=context,
        photo_limit=None,
        remaining_budget=60.0,
        enforce_favorite_ratio=False,
        enforce_temporal_spacing=not relax_spacing,
    )


def test_a_refused_clip_stays_refused_even_when_spacing_is_relaxed() -> None:
    """The last rung of the ladder is exactly where the near-dup came back."""
    kept = _clip("kept", minute=0, score=0.80)
    near_dup = _clip("near-dup", minute=1, score=0.79)

    assert not _admissible(near_dup, selected=[kept], refused={"near-dup"}, relax_spacing=True), (
        "backfill re-added the clip dedup had just dropped"
    )


def test_an_unrefused_clip_elsewhere_is_still_admissible() -> None:
    """Freed seconds go somewhere — just not backwards."""
    kept = _clip("kept", minute=0, score=0.80)
    elsewhere = _clip("elsewhere", minute=45, score=0.55)

    assert _admissible(elsewhere, selected=[kept], refused={"near-dup"}, relax_spacing=False)


def test_nothing_is_refused_by_default() -> None:
    """An empty refusal set must not change today's behaviour."""
    kept = _clip("kept", minute=0, score=0.80)
    other = _clip("other", minute=45, score=0.55)

    assert _admissible(other, selected=[kept], refused=set(), relax_spacing=False)
