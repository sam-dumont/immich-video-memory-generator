"""The media pool page after S10: one set of counters, one primary action, ticks explained."""

from __future__ import annotations

from immich_memories.api.models import AssetType
from immich_memories.ui.pages.step2_helpers import (
    pool_counters_line,
    review_candidates,
    tick_explanation,
)
from tests.conftest import make_clip


def test_the_counters_read_as_one_sentence_that_agrees_with_the_storyboard() -> None:
    line = pool_counters_line(videos=3, photos=3, ticked=5, in_cut=6, cut_seconds=24.0)
    assert line == "6 in the pool (3 videos, 3 photos) · 5 ticked · 6 in the cut, 0:24"


def test_the_counters_say_nothing_about_a_cut_that_has_not_happened() -> None:
    line = pool_counters_line(videos=3, photos=0, ticked=3, in_cut=None, cut_seconds=None)
    assert line == "3 in the pool (3 videos) · 3 ticked"


def test_a_tick_means_exclusion_before_the_first_cut_and_an_instruction_after() -> None:
    assert tick_explanation(has_result=False) == "Untick a picture to leave it out of the cut."
    assert tick_explanation(has_result=True) == (
        "Ticked pictures are in the next cut, unticked ones are out. Cut again applies it."
    )


def test_only_video_clips_reach_the_excerpt_editor() -> None:
    video = make_clip("v1")
    still = make_clip("p1")
    still.asset.type = AssetType.IMAGE
    assert review_candidates([video, still]) == [video]
