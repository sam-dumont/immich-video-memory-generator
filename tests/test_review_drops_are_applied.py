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


def _verdict(*entries: dict) -> str:
    return json.dumps({"drop": list(entries)})


def _review(selection, raw):
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

        verdict = _review(selection, _verdict({"index": 2, "reason": "same occasion as clip 1"}))

        assert verdict.drops == ["star-b"]

    def test_a_lone_star_is_kept_and_the_veto_is_reported(self):
        """The owner's mark still wins its occasion — but not in silence.

        Vetoing invisibly is what made four renders undiagnosable.
        """
        selection = [
            _clip("only-star", starred=True),
            _clip("plain", minutes=20),
            _clip("elsewhere", minutes=6000),
        ]

        verdict = _review(selection, _verdict({"index": 1, "reason": "not worth showing"}))

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
            _verdict(
                {"index": 1, "reason": "vetoed one"},
                {"index": 2, "reason": "the real drop"},
            ),
        )

        assert verdict.drops == ["plain"]
        applied = [f for f in verdict.fates if "applied" in f]
        assert len(applied) == 1
        assert "the real drop" in applied[0]

    def test_an_out_of_range_index_is_reported(self):
        selection = [_clip(f"c{n}", minutes=n * 6000) for n in range(3)]

        verdict = _review(selection, _verdict({"index": 99, "reason": "off the end"}))

        assert verdict.drops == []
        assert any("99" in fate for fate in verdict.fates)

    def test_the_cap_says_when_it_bites(self):
        """At most a fifth of the cut per round — silently, until now."""
        selection = [_clip(f"c{n}", minutes=n * 6000) for n in range(5)]

        verdict = _review(
            selection,
            _verdict(*[{"index": n, "reason": f"drop {n}"} for n in range(1, 5)]),
        )

        assert len(verdict.drops) == 1
        assert any("cap" in fate.lower() for fate in verdict.fates)
