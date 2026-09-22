"""Polish a rules draft with the model instead of re-planning the film around it.

A library that has been catalogued already holds an account of the period and a neutral list of
its stories, and the no-model reader can build a whole film out of them. What that film has not
had is a reader looking at the finished cut and saying which of its shots add nothing. This
layer asks exactly that, once, and changes only what the answer and the gates leave open.

It is not a planner. There are no caps, no reserve, no depth, no re-allocation and no coverage
floor here: the gated draft is the film, and the only picture that leaves is one the gates
refused or the vote named.
"""

from __future__ import annotations

import calendar
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from immich_memories.analysis.editorial_thin_catalogue import (
    BankedCatalogue,
    ThinCatalogue,
    banked_catalogue,
)
from immich_memories.analysis.editorial_thin_gates import GateRefusal, ThinGates
from immich_memories.analysis.editorial_thin_vote import classify_fit, vote_thesis_fit
from immich_memories.security import write_secret_file

THIN_VERSION = "thin-polish-v1"


def catalogued_period(ranges: Sequence[Any]) -> str:
    """The library node a film of these dates could hold an account of, or nothing.

    Cataloguing writes its account per calendar month and per year. A film whose dates are one
    of those asks for it by name; a film over any other span has no catalogued period and plans
    the way it always has.
    """
    if len(ranges) != 1:
        return ""
    first, last = ranges[0].start, ranges[0].end
    if first.day != 1 or last.day != calendar.monthrange(last.year, last.month)[1]:
        return ""
    if (first.year, first.month) == (last.year, last.month):
        return f"{first.year:04d}-{first.month:02d}"
    if first.year == last.year and (first.month, last.month) == (1, 12):
        return f"{first.year:04d}"
    return ""


@dataclass(frozen=True)
class ThinPolish:
    """The model polish of a rules draft, for a period the library holds an account of."""

    account: str
    bank_dir: Path

    def catalogue_of(
        self, story, moment_assets: Mapping[str, Sequence[str]]
    ) -> BankedCatalogue | None:
        """The catalogued period behind this run's own story reading, or None for no account."""
        return banked_catalogue(
            account=self.account,
            story_rows=story.stories,
            hints=story.audit.get("hints") or {},
            asset_ids_of=_story_asset_ids(story.episodes, story.stories, moment_assets),
        )

    def polish(
        self,
        carriers: Sequence[dict[str, Any]],
        *,
        judge,
        gates: ThinGates,
        catalogue: ThinCatalogue | None,
        contract: str,
        line_of: Callable[[str], str],
        record: Callable[[str, Mapping[str, Any]], None],
        protected: Sequence[str] = (),
    ) -> list[dict[str, Any]]:
        """The cut this period's gates and one closed vote leave standing.

        A period the library has no account of is not polished at all: the film is the one the
        planner already built, which is the fallback this layer is switched on in front of.
        """
        if catalogue is None or not carriers:
            record("thin-polish", {"version": THIN_VERSION, "ran": False, "reason": "no catalogue"})
            return list(carriers)
        admitted, refused = gates.admit(
            carriers,
            tier_of={story.key: story.tier for story in catalogue.stories},
            protected=protected,
        )
        kept, verdicts, rounds = self._voted(admitted, judge, catalogue, contract, line_of)
        record(
            "thin-polish",
            {
                "version": THIN_VERSION,
                "ran": True,
                "stories": len(catalogue.stories),
                "draft_shots": len(carriers),
                "refused_by_the_gates": [_refusal_row(row) for row in refused],
                "voted": len(verdicts),
                "rounds": rounds,
                "verdicts": verdicts,
                "removed_by_the_vote": [
                    asset for asset, verdict in verdicts.items() if verdict["state"] == "bad"
                ],
                "held_by_the_owner": [
                    asset for asset, verdict in verdicts.items() if verdict["held_by"]
                ],
            },
        )
        return kept

    def _voted(
        self,
        carriers: list[dict[str, Any]],
        judge,
        catalogue: ThinCatalogue,
        contract: str,
        line_of: Callable[[str], str],
    ) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], list[dict]]:
        bank_path = self.bank_dir / "thesis-fit.private.json"
        bank = json.loads(bank_path.read_text()) if bank_path.exists() else {}
        story_of = {
            asset: story.key for story in catalogue.stories for asset in story.asset_ids
        }.get
        votes, rounds = vote_thesis_fit(
            judge,
            pictures=[c["asset_id"] for c in carriers],
            line_of=line_of,
            thesis=catalogue.thesis,
            contract=contract,
            story_of=lambda asset: story_of(asset, "") or "",
            bank=bank,
            save=lambda: write_secret_file(bank_path, json.dumps(bank, indent=1)),
        )
        verdicts = classify_fit(carriers, votes)
        kept = [c for c in carriers if verdicts[c["asset_id"]]["state"] != "bad"]
        return kept, verdicts, rounds


def _refusal_row(refusal: GateRefusal) -> dict[str, str]:
    return {
        "asset_id": refusal.asset_id,
        "story": refusal.story,
        "rule": refusal.rule,
        "detail": refusal.detail,
    }


def _story_asset_ids(
    episodes: Sequence[Any],
    stories: Sequence[Mapping[str, Any]],
    moment_assets: Mapping[str, Sequence[str]],
) -> dict[str, list[str]]:
    """Which pictures of the period each catalogued story is about."""
    moments_of = {episode.key: tuple(episode.moments) for episode in episodes}
    result = {}
    for story in stories:
        assets: list[str] = []
        for key in story.get("episodes") or ():
            for moment in moments_of.get(key, ()):
                assets.extend(a for a in moment_assets.get(moment, ()) if a not in assets)
        result[str(story["key"])] = assets
    return result
