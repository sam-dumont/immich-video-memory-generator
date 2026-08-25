"""A clip nobody looked at, and a clip nobody could read, are not the same.

The review is told — correctly — never to drop a clip for missing information:
a third of a real pool has no analysis yet, and treating silence as a verdict
would gut the memory. The cost is that a bare line is immune to the only
quality judgment in the pipeline.

Verify exists to make sure no bare line ever reaches the judge. But a clip it
has already attempted is never queued again — deliberately, so a clip whose
analysis fails cannot loop forever. Those two rules meet badly: a clip that was
looked at and could not be described is skipped by verify AND protected by the
review, permanently. June shipped a 2.5s window frame and a dark screen that
way, three renders running.
"""

from __future__ import annotations

from datetime import UTC, datetime

from immich_memories.analysis.selection_review import _clips_block
from immich_memories.analysis.smart_pipeline import ClipWithSegment
from immich_memories.api.models import Asset, AssetType, VideoClipInfo

WHEN = datetime(2023, 6, 14, 15, 7, tzinfo=UTC)


def _clip(asset_id: str, *, description: str | None = None) -> ClipWithSegment:
    asset = Asset(
        id=asset_id,
        type=AssetType.VIDEO,
        fileCreatedAt=WHEN,
        fileModifiedAt=WHEN,
        updatedAt=WHEN,
    )
    clip = VideoClipInfo(asset=asset, duration_seconds=4.0)
    clip.llm_description = description
    return ClipWithSegment(clip=clip, start_time=0.0, end_time=4.0, score=0.5)


class TestTheJudgeIsToldWhichSilenceIsWhich:
    def test_a_clip_that_could_not_be_read_says_so(self):
        """It was looked at. It has had its chance."""
        block = _clips_block([_clip("murky")], unreadable_ids={"murky"})

        assert "unreadable=yes" in block

    def test_a_clip_nobody_has_looked_at_yet_does_not(self):
        """Silence from a clip nobody queued is not a verdict, and must not
        read like one — this is the protection that exists for good reason."""
        block = _clips_block([_clip("unseen")], unreadable_ids=set())

        assert "unreadable" not in block

    def test_a_described_clip_is_never_marked(self):
        block = _clips_block([_clip("seen", description="a harbour")], unreadable_ids={"seen"})

        assert "unreadable" not in block


class TestThePromptSeparatesTheTwo:
    def test_the_protection_is_scoped_to_the_unlooked_at(self):
        """Both rules must be present, or one swallows the other."""
        from immich_memories.analysis.selection_review import _PROMPT

        assert "never drop a clip for missing information" in _PROMPT
        assert "unreadable=yes" in _PROMPT
