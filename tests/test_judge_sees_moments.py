"""What the judge can see about when a clip happened, and who starred it.

Its clip lines carried `date=2011-08-04` and nothing finer, so two clips ten
minutes apart looked like two clips on a Thursday. A real ship-deck
performance shipped three times: 03 Aug 16:23 from one camera, 04 Aug 15:32
and 15:42 from another. Nothing in the judge's view could have told it.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from immich_memories.analysis.selection_review import _clips_block
from immich_memories.analysis.smart_pipeline import ClipWithSegment
from immich_memories.api.models import Asset, AssetType, VideoClipInfo

DECK = datetime(2011, 8, 4, 15, 32, tzinfo=UTC)


def _clip(asset_id: str, when: datetime, description: str) -> ClipWithSegment:
    asset = Asset(
        id=asset_id,
        type=AssetType.VIDEO,
        fileCreatedAt=when,
        fileModifiedAt=when,
        updatedAt=when,
    )
    clip = VideoClipInfo(asset=asset, duration_seconds=5.0)
    clip.llm_description = description
    return ClipWithSegment(clip=clip, start_time=0.0, end_time=5.0, score=0.5)


def _episode_of(line: str) -> str | None:
    found = re.search(r"episode=(\S+)", line)
    return found.group(1) if found else None


def test_the_judge_is_told_the_time_of_day() -> None:
    """A date alone cannot separate ten minutes from ten hours."""
    block = _clips_block([_clip("a", DECK, "a performance on deck")])

    assert "15:32" in block


def test_two_clips_ten_minutes_apart_are_named_as_one_occasion() -> None:
    """The judge's own rule says drop the same moment; give it the moment."""
    lines = _clips_block(
        [
            _clip("first", DECK, "a performance on deck"),
            _clip("second", DECK + timedelta(minutes=10), "the same performance on deck"),
            _clip("elsewhere", DECK + timedelta(days=3), "a harbour at dawn"),
        ]
    ).splitlines()

    assert _episode_of(lines[0]) == _episode_of(lines[1])
    assert _episode_of(lines[2]) != _episode_of(lines[0])


def _starred(clip: ClipWithSegment) -> ClipWithSegment:
    clip.clip.asset.is_favorite = True
    return clip


def test_one_site_visit_is_one_occasion_however_it_is_spread() -> None:
    """Three favourites from one afternoon at one place: 15:07, 15:22, 16:04.

    Fifteen and forty-two minutes apart — outside every same-moment window, so
    the judge was shown three different labels on one day and had no basis to
    object. "Three brick pavilions" reads as architectural variety in text.
    An occasion is the block a moment sits in, not the moment.
    """
    visit = datetime(2023, 6, 14, 15, 7, tzinfo=UTC)
    lines = _clips_block(
        [
            _starred(_clip("pavilion-a", visit, "a brick pavilion")),
            _starred(_clip("pavilion-b", visit + timedelta(minutes=15), "another brick pavilion")),
            _starred(_clip("pavilion-c", visit + timedelta(minutes=57), "a third brick pavilion")),
        ]
    ).splitlines()

    labels = {_episode_of(line) for line in lines}
    assert labels != {None}, "the judge was told nothing about the occasion"
    assert len(labels) == 1


def test_the_judge_is_told_which_clips_the_owner_starred() -> None:
    """Several starred clips in one occasion are a battle to judge between.

    Without knowing which are starred the judge can drop a favourite and keep
    the unstarred clip beside it — and because the review shrinks the pool,
    nothing downstream can put it back.
    """
    visit = datetime(2023, 6, 14, 15, 7, tzinfo=UTC)
    lines = _clips_block(
        [
            _starred(_clip("kept", visit, "a brick pavilion")),
            _clip("plain", visit + timedelta(minutes=15), "a corridor"),
        ]
    ).splitlines()

    assert "starred" in lines[0]
    assert "starred" not in lines[1]
