"""Competent prose can hide that nothing happened.

A 2.5s video of a window survived five renders. It is not undescribed — that
was the previous hole. It is described WELL: "view from a window onto a
residential backyard, evergreen tree, small shed", category=landscape,
quality=0.85 for a nicely exposed roof. Every signal saying nothing happens —
no activities, no people, interest 0.4, two and a half seconds — stayed out of
the judge's view, so its line read like a pleasant garden shot and the judge
never named it in any round.

The facts go on the line. No threshold: a 2.5s clip of a sleeping newborn with
no activities is a keeper, and only context can tell the two apart.
"""

from __future__ import annotations

from datetime import UTC, datetime

from immich_memories.analysis.selection_review import _clips_block
from immich_memories.analysis.smart_pipeline import ClipWithSegment
from immich_memories.api.models import Asset, AssetType, Person, VideoClipInfo

WHEN = datetime(2023, 6, 14, 15, 7, tzinfo=UTC)


def _clip(
    asset_id: str,
    *,
    seconds: float = 2.5,
    activities: list[str] | None = None,
    people: int = 0,
    interest: float | None = None,
) -> ClipWithSegment:
    asset = Asset(
        id=asset_id,
        type=AssetType.VIDEO,
        fileCreatedAt=WHEN,
        fileModifiedAt=WHEN,
        updatedAt=WHEN,
        people=[Person(id=f"p{n}", name=f"N{n}") for n in range(people)],
    )
    clip = VideoClipInfo(asset=asset, duration_seconds=seconds)
    clip.llm_description = "view from a window onto a residential backyard"
    clip.llm_activities = activities
    clip.llm_interestingness = interest
    return ClipWithSegment(clip=clip, start_time=0.0, end_time=seconds, score=0.5)


class TestTheLineSaysWhetherAnythingHappened:
    def test_an_empty_clip_shows_its_emptiness(self):
        line = _clips_block([_clip("window", interest=0.4)])

        assert "activities=none" in line
        assert "people=0" in line
        assert "interest=0.4" in line
        assert "2.5s" in line

    def test_a_clip_with_activity_names_it(self):
        line = _clips_block([_clip("ride", activities=["cycling"], people=2, interest=0.8)])

        assert "activities=cycling" in line
        assert "people=2" in line
        assert "activities=none" not in line

    def test_an_unmeasured_interest_says_nothing(self):
        """Silence is not a verdict here either."""
        line = _clips_block([_clip("unseen", interest=None)])

        assert "interest=" not in line


class TestTheObjectThatIsTheNews:
    def test_the_prompt_carves_out_life_event_markers(self):
        """A pregnancy test is an information photo AND the whole story.

        The object class is junk in any other month, so the carve-out is by
        what the object announces, not by loosening the class.
        """
        from immich_memories.analysis.selection_review import _PROMPT

        assert "pregnancy test" in _PROMPT.lower()
        assert "announces" in _PROMPT.lower() or "the news" in _PROMPT.lower()
