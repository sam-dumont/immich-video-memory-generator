"""What the review decided, what was done about it, and saying so.

The prompt has instructed a starred-vs-starred battle since #757: an occasion
the owner starred repeatedly earns one place, not one each. The judge ran it.
The plumbing then dropped every starred clip from the drop list — silently, no
log, no counter — so the instruction could never take effect, and starred junk
was immortal across four renders.

The log was worse than silent. It printed `entries[:len(drops)]`: the FIRST n
verdict entries rather than the applied ones. A vetoed entry was reported as
dropped, so four renders of "dropping clip X" lines were partly fiction, and
two diagnoses were built on them.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from immich_memories.analysis.selection_review import review_selection
from immich_memories.analysis.smart_pipeline import ClipWithSegment
from immich_memories.api.models import Asset, AssetType, VideoClipInfo
from immich_memories.config_models_llm import LLMConfig

VISIT = datetime(2023, 6, 14, 15, 7, tzinfo=UTC)


def _clip(asset_id: str, *, minutes: float = 0.0, starred: bool = False) -> ClipWithSegment:
    when = VISIT + timedelta(minutes=minutes)
    asset = Asset(
        id=asset_id,
        type=AssetType.VIDEO,
        fileCreatedAt=when,
        fileModifiedAt=when,
        updatedAt=when,
        isFavorite=starred,
    )
    clip = VideoClipInfo(asset=asset, duration_seconds=4.0)
    clip.llm_description = f"a shot called {asset_id}"
    return ClipWithSegment(clip=clip, start_time=0.0, end_time=4.0, score=0.5)


def _verdict(selection: list, *entries: dict) -> str:
    """The model's answer: which clips belong, and which do not.

    The keep list is derived from the entries a test names, because the parser
    refuses any answer whose two lists do not account for every clip.
    """
    named = {entry["index"] for entry in entries}
    keep = [n for n in range(1, len(selection) + 1) if n not in named]
    return json.dumps({"keep": keep, "cut": list(entries)})


def _review(selection, *entries: dict):
    raw = _verdict(selection, *entries)
    # WHY: the model. Everything under test is what we do with its answer.
    with patch("immich_memories.analysis.selection_review._ask", return_value=raw):
        return review_selection(selection, LLMConfig())


class TestTheStarredBattleIsActuallyFought:
    def test_a_starred_clip_loses_to_a_starred_sibling(self):
        """The rule the prompt promises: one place for the occasion, not one each."""
        selection = [
            _clip("star-a", starred=True),
            _clip("star-b", minutes=20, starred=True),
            _clip("elsewhere", minutes=6000),
        ]

        verdict = _review(selection, {"index": 2, "reason": "same occasion as clip 1"})

        assert verdict.drops == ["star-b"]

    def test_a_star_holds_while_an_unstarred_sibling_still_ships(self):
        """The battle is a last resort, not a first one.

        An occasion earns one place. When the judge wants that place spent on
        an unstarred clip of the same occasion, the owner's mark decides it —
        so a star may only lose to a star once nothing unstarred from its
        occasion is left in the cut.
        """
        selection = [
            _clip("star-a", starred=True),
            _clip("star-b", minutes=20, starred=True),
            _clip("plain-same-occasion", minutes=40),
            _clip("elsewhere", minutes=6000),
        ]

        verdict = _review(selection, {"index": 2, "reason": "same occasion as clip 1"})

        assert verdict.drops == []
        assert any("star-b" in fate and "kept" in fate for fate in verdict.fates)

    def test_neither_star_falls_while_the_occasion_has_an_unstarred_clip(self):
        """The measured breach, pinned.

        One afternoon in one place, photographed across three ten-minute
        boxes: two of them starred, one not. Every mechanism read the boxes as
        three moments, so nothing objected — and the review spent the
        occasion's place on the unstarred clip. Episode scope is stricter than
        the law needs (the law's unit is the moment), and that is deliberate:
        while the occasion still has an unstarred clip to give up, no star of
        it is the one in question.
        """
        selection = [
            _clip("star-first-box", starred=True),
            _clip("star-second-box", minutes=15, starred=True),
            _clip("plain-third-box", minutes=57),
            _clip("elsewhere", minutes=6000),
        ]

        verdict = _review(
            selection,
            {"index": 1, "reason": "one place for the occasion"},
            {"index": 2, "reason": "one place for the occasion"},
        )

        assert verdict.drops == []

    def test_a_lone_star_is_kept_and_the_veto_is_reported(self):
        """The owner's mark still wins its occasion — but not in silence.

        Vetoing invisibly is what made four renders undiagnosable.
        """
        selection = [
            _clip("only-star", starred=True),
            _clip("plain", minutes=20),
            _clip("elsewhere", minutes=6000),
        ]

        verdict = _review(selection, {"index": 1, "reason": "not worth showing"})

        assert verdict.drops == []
        assert any("only-star" in fate and "kept" in fate for fate in verdict.fates)


class TestTheLedgerNamesWhatActuallyHappened:
    def test_a_vetoed_entry_is_not_reported_as_dropped(self):
        """entries[:len(drops)] printed the first n, not the applied n."""
        selection = [
            _clip("only-star", starred=True),
            _clip("plain", minutes=20),
            _clip("elsewhere", minutes=6000),
        ]

        verdict = _review(
            selection,
            {"index": 1, "reason": "vetoed one"},
            {"index": 2, "reason": "the real drop"},
        )

        assert verdict.drops == ["plain"]
        applied = [f for f in verdict.fates if "applied" in f]
        assert len(applied) == 1
        assert "the real drop" in applied[0]

    def test_an_answer_naming_a_clip_that_is_not_there_is_refused_whole(self, caplog):
        """Not one bad line in a good cut — an answer about some other set.

        Under keep-semantics an index nothing can be placed against breaks the
        guarantee the pass rests on: that the two lists account for every clip.
        Carrying out the rest of it would act on part of an answer to a
        question nobody asked.
        """
        import logging

        selection = [_clip(f"c{n}", minutes=n * 6000) for n in range(3)]

        with caplog.at_level(logging.WARNING, logger="immich_memories.analysis.selection_review"):
            verdict = _review(selection, {"index": 99, "reason": "off the end"})

        assert verdict.drops == []
        assert any(record.levelno >= logging.WARNING for record in caplog.records)
