"""One question the judge answers, not a list of classes it applies.

June was rejected twice with the judge working: eleven drops, and it caught a
sibling of every piece of residue. It applied the taxonomies inconsistently
round to round — a screen it had dropped variants of all day survived the last
one. Many classes applied unevenly is weaker than one question asked every
time, and the owner supplied the question: "would you show this to someone
else?"
"""

from __future__ import annotations

from datetime import UTC, datetime

from immich_memories.analysis.selection_review import _PROMPT, _clips_block
from immich_memories.analysis.smart_pipeline import ClipWithSegment
from immich_memories.api.models import Asset, AssetType, ExifInfo, VideoClipInfo

WHEN = datetime(2023, 6, 14, 15, 7, tzinfo=UTC)


def _clip(asset_id: str, *, lens: str | None = None) -> ClipWithSegment:
    asset = Asset(
        id=asset_id,
        type=AssetType.IMAGE,
        fileCreatedAt=WHEN,
        fileModifiedAt=WHEN,
        updatedAt=WHEN,
        exifInfo=ExifInfo(lensModel=lens) if lens else None,
    )
    clip = VideoClipInfo(asset=asset, duration_seconds=4.0)
    clip.llm_description = "a person"
    return ClipWithSegment(clip=clip, start_time=0.0, end_time=4.0, score=0.5)


class TestTheJudgeIsToldWhichCameraTookIt:
    """A front camera is how a phone says "this is a picture of me"."""

    def test_a_front_camera_clip_says_so(self):
        line = _clips_block([_clip("selfie", lens="iPhone 15 Pro front camera 2.22mm f/1.9")])

        assert "camera=front" in line

    def test_a_rear_camera_clip_does_not(self):
        line = _clips_block([_clip("shot", lens="iPhone 15 Pro back camera 6.86mm f/1.78")])

        assert "camera=front" not in line

    def test_an_unknown_camera_says_nothing(self):
        """Silence is not a verdict here either."""
        line = _clips_block([_clip("unknown")])

        assert "camera=" not in line


class TestTheOrganizingQuestionComesFirst:
    def test_the_prompt_opens_on_shareability(self):
        """The question is the frame; the classes are examples of failing it.

        Led by a list, the model applies whichever class it happens to match
        and misses the same thing next round. Led by a question, it has one
        thing to answer about every clip.
        """
        assert "show" in _PROMPT.lower() and "someone else" in _PROMPT.lower()

    def test_the_question_precedes_the_examples(self):
        question = _PROMPT.lower().index("someone else")
        redundant = _PROMPT.index("REDUNDANT")

        assert question < redundant
