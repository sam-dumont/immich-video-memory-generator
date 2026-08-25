"""The judge is told to drop "the same moment" — and cannot see moments.

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


def _moment_of(line: str) -> str | None:
    found = re.search(r"moment=(\S+)", line)
    return found.group(1) if found else None


def test_the_judge_is_told_the_time_of_day() -> None:
    """A date alone cannot separate ten minutes from ten hours."""
    block = _clips_block([_clip("a", DECK, "a performance on deck")])

    assert "15:32" in block


def test_two_clips_ten_minutes_apart_are_named_as_one_moment() -> None:
    """The judge's own rule says drop the same moment; give it the moment."""
    lines = _clips_block(
        [
            _clip("first", DECK, "a performance on deck"),
            _clip("second", DECK + timedelta(minutes=10), "the same performance on deck"),
            _clip("elsewhere", DECK + timedelta(days=3), "a harbour at dawn"),
        ]
    ).splitlines()

    assert _moment_of(lines[0]) == _moment_of(lines[1])
    assert _moment_of(lines[2]) != _moment_of(lines[0])
