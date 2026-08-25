"""Backfill may not restock what the judge keeps throwing out.

June 2023 was rejected twice. The judge worked — eleven drops, and it caught a
sibling of every piece of residue — but the supply outlasted it: drop a
pavilion, the refill offers another pavilion; drop a screen, the refill offers
another screen. Seventeen rounds, ending "budget spent, dropping 1 unreviewed
clip(s) rather than shipping them".

Backfill decides what may be offered. Two things it never asked: whether the
occasion is already in the cut, and what the clip is OF.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from immich_memories.analysis.clip_backfill import (
    _build_backfill_context,
    _is_backfill_candidate_admissible,
    _resolve_backfill_candidates,
)
from immich_memories.analysis.smart_pipeline import ClipWithSegment, PipelineConfig
from immich_memories.api.models import Asset, AssetType, VideoClipInfo

VISIT = datetime(2023, 6, 14, 15, 7, tzinfo=UTC)


def _clip(
    asset_id: str,
    when: datetime,
    *,
    category: str | None = None,
    score: float = 0.5,
    seconds: float = 4.0,
) -> ClipWithSegment:
    asset = Asset(
        id=asset_id,
        type=AssetType.VIDEO,
        fileCreatedAt=when,
        fileModifiedAt=when,
        updatedAt=when,
    )
    clip = VideoClipInfo(asset=asset, duration_seconds=seconds)
    clip.llm_category = category
    return ClipWithSegment(clip=clip, start_time=0.0, end_time=seconds, score=score)


def _context(selected: list[ClipWithSegment]):
    return _build_backfill_context(
        selected,
        config=PipelineConfig(target_clips=8),
        temporal_window=5.0,
        occupied_moments=[c.clip.asset.file_created_at for c in selected],
    )


def _admissible(candidate: ClipWithSegment, selected: list[ClipWithSegment]) -> bool:
    return _is_backfill_candidate_admissible(
        candidate,
        context=_context(selected),
        photo_limit=None,
        remaining_budget=60.0,
    )


class TestBackfillWillNotPadWithJunk:
    """A memory that cannot fill runs short. It does not fill with a screen."""

    def test_a_screen_is_never_padded_in(self):
        assert not _admissible(_clip("screen", VISIT + timedelta(days=2), category="screen"), [])

    def test_an_object_is_never_padded_in(self):
        assert not _admissible(_clip("thing", VISIT + timedelta(days=2), category="object"), [])

    def test_an_unlabelled_clip_is_still_offered(self):
        """A third of a real pool has no analysis. Silence is not a verdict.

        Reading it as junk would empty exactly the quiet months that need
        backfill most.
        """
        assert _admissible(_clip("unseen", VISIT + timedelta(days=2), category=None), [])

    def test_scenery_and_animals_are_still_offered(self):
        """Scenery earns its place by being good; animals are a garnish."""
        assert _admissible(_clip("view", VISIT + timedelta(days=2), category="landscape"), [])
        assert _admissible(_clip("cat", VISIT + timedelta(days=3), category="animal"), [])


class TestBackfillPrefersAnUnrepresentedOccasion:
    def test_a_clip_from_an_occasion_already_in_the_cut_is_not_the_strict_choice(self):
        """The pavilion case: 15:07 is in the cut, 15:49 is the same visit.

        Forty-two minutes apart is outside every same-moment window, so the
        old strict pass offered it and the judge had to throw it out again.
        """
        in_cut = [_clip("kept", VISIT)]
        same_visit = _clip("another-pavilion", VISIT + timedelta(minutes=42))
        other_day = _clip("elsewhere", VISIT + timedelta(days=2))

        resolved = _resolve_backfill_candidates(
            [same_visit, other_day],
            context=_context(in_cut),
            active_photo_limit=None,
            remaining_budget=60.0,
        )

        assert resolved.tier == "strict"
        assert [c.clip.asset.id for c in resolved.items] == ["elsewhere"]

    def test_when_only_the_same_occasion_is_left_it_still_fills(self):
        """Prefer, never refuse: a wedding is one or two occasions all day.

        A hard rule would starve it. The ladder concedes instead, and says so.
        """
        in_cut = [_clip("kept", VISIT)]
        same_visit = _clip("another-pavilion", VISIT + timedelta(minutes=42))

        resolved = _resolve_backfill_candidates(
            [same_visit],
            context=_context(in_cut),
            active_photo_limit=None,
            remaining_budget=60.0,
        )

        assert [c.clip.asset.id for c in resolved.items] == ["another-pavilion"]
        assert resolved.tier != "strict"
