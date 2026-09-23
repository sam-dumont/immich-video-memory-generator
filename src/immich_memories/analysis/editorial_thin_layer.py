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
import logging
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from operator import itemgetter
from pathlib import Path
from typing import Any

from immich_memories.analysis.editorial_block_votes import BLOCK_SIZE, balanced_groups
from immich_memories.analysis.editorial_thin_catalogue import (
    BankedCatalogue,
    ThinCatalogue,
    banked_catalogue,
)
from immich_memories.analysis.editorial_thin_gates import GateRefusal, ThinGates
from immich_memories.analysis.editorial_thin_refill import ThinRefill, ThinSlot, plan_slots
from immich_memories.analysis.editorial_thin_vote import (
    classify_fit,
    is_protected,
    vote_thesis_fit,
)
from immich_memories.security import write_secret_file

logger = logging.getLogger(__name__)

THIN_VERSION = "thin-polish-v1"


def catalogued_period(ranges: Sequence[Any]) -> str:
    """The library node a film of these dates could hold an account of, or nothing.

    Cataloguing writes its account per calendar month and per year. A film whose dates are one
    of those asks for it by name. A window over more than one calendar year, such as a person
    film from a birth date to today, is its own node ("2005-12-03..2026-09-23"), told from one
    account per year it touches. A film over any other span has no catalogued period and plans
    the way it always has.
    """
    if len(ranges) != 1:
        return ""
    first, last = ranges[0].start, ranges[0].end
    if first.year != last.year:
        return f"{_day(first)}..{_day(last)}"
    if first.day != 1 or last.day != calendar.monthrange(last.year, last.month)[1]:
        return ""
    if first.month == last.month:
        return f"{first.year:04d}-{first.month:02d}"
    if (first.month, last.month) == (1, 12):
        return f"{first.year:04d}"
    return ""


def _day(when: Any) -> str:
    return f"{when.year:04d}-{when.month:02d}-{when.day:02d}"


@dataclass(frozen=True)
class ThinPolish:
    """The model polish of a rules draft, over an account of the period it is a cut of."""

    bank_dir: Path
    # The account of the period, and what its readings named as worth a record, given the
    # shots this draft chose, by story. Both arrive together, because both come from the same
    # readings and neither is worth paying for before the draft exists.
    read_period: Callable[[Mapping[str, Sequence[str]]], tuple[str, Mapping[str, str]]] = (
        lambda _stories: ("", {})
    )

    def catalogue_of(
        self,
        story,
        moment_assets: Mapping[str, Sequence[str]],
        records: Mapping[str, str] | None = None,
        *,
        drafted: Sequence[Mapping[str, Any]],
    ) -> BankedCatalogue | None:
        """The catalogued period behind this run's own story reading, or None for no account.

        Every story of the period is listed, because the layer needs to know what the period
        holds. Only the draft's own shots are READ, by the episode each one sits in: the
        account is the thesis of THIS cut. A story is not the unit here, because one story can
        hold most of a month: on one measured month the longest story spanned 10 episodes to
        give the film 5 shots, and reading every story the draft touched read 44 of the
        month's 50 episodes. The rest of the period is filled in later, by `prepare
        --overviews` or by another cut.
        """
        asset_ids_of = _story_asset_ids(story.episodes, story.stories, moment_assets)
        account, banked_records = self.read_period(_drafted_shots(asset_ids_of, drafted))
        return banked_catalogue(
            account=account,
            story_rows=story.stories,
            hints=story.audit.get("hints") or {},
            asset_ids_of=asset_ids_of,
            records=banked_records if records is None else records,
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
        candidates_of: Callable[[str], Sequence[Mapping[str, Any]]] = lambda _key: (),
        content_cap: float = 0.0,
        protected: Sequence[str] = (),
    ) -> list[dict[str, Any]]:
        """The cut this period's gates, one closed vote and one refill leave standing.

        A period the library has no account of is not polished at all: the film is the one the
        planner already built, which is the fallback this layer is switched on in front of.
        """
        if catalogue is None or not carriers:
            reason = "no catalogued account of this period" if catalogue is None else "no draft"
            record("thin-polish", {"version": THIN_VERSION, "ran": False, "reason": reason})
            return list(carriers)
        carriers = _with_records(carriers, catalogue)
        first_call = len(judge.calls)
        tier_of = {story.key: story.tier for story in catalogue.stories}
        admitted, refused = gates.admit(carriers, tier_of=tier_of, protected=protected)
        kept, verdicts, rounds = self._voted(admitted, judge, catalogue, contract, line_of)
        slots = plan_slots(
            kept,
            catalogue=catalogue,
            verdicts=verdicts,
            refused=refused,
            candidates_of=lambda key: _with_records(candidates_of(key), catalogue),
            seen={c["asset_id"] for c in carriers},
            content_cap=content_cap,
        )
        refill = ThinRefill(
            judge=judge,
            gates=gates,
            contract=contract,
            line_of=line_of,
            record=record,
            tier_of=tier_of,
            title_of={story.key: story.title for story in catalogue.stories},
            content_cap=content_cap,
        )
        filled, outcomes = refill.fill(kept, slots)
        partition = balanced_groups([c["asset_id"] for c in admitted])
        final, revoked = self._checked(
            filled, kept, outcomes, partition, judge, catalogue, contract, line_of
        )
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
                "slots": [slot.row() for slot in outcomes],
                "revoked_by_the_fit_check": sorted(revoked),
                "shots": len(final),
                "planned_seconds": round(sum(c["seconds"] for c in final), 3),
                "content_cap": content_cap,
                "calls": _spent(len(judge.calls) - first_call, len(carriers), len(outcomes)),
            },
        )
        return final

    def _checked(
        self,
        filled: list[dict[str, Any]],
        before: Sequence[Mapping[str, Any]],
        outcomes: Sequence[ThinSlot],
        partition: Sequence[Sequence[str]],
        judge,
        catalogue: ThinCatalogue,
        contract: str,
        line_of: Callable[[str], str],
    ) -> tuple[list[dict[str, Any]], set[str]]:
        """Every newcomer, judged again in the company of the block it joined.

        The first vote's blocks are kept: a swap takes its shot's place and an appended newcomer
        joins the block its capture time falls in, so a few newcomers re-ask a few blocks and
        not the whole cut. Only the newcomers' verdicts are read. Banked by the block, never by
        the row, so a second run replays it, and a newcomer is never the only row left to ask.
        A revoked newcomer does not take the shot it replaced with it: that shot comes back and
        the film is where it started.
        """
        held = {row["asset_id"] for row in before}
        fresh = [row for row in filled if row["asset_id"] not in held]
        if not fresh:
            return filled, set()
        newcomers = {row["asset_id"] for row in fresh}
        by_asset = {row["asset_id"]: row for row in filled}
        votes: dict[str, tuple[int, str]] = {}
        for group in rejoined_blocks(partition, filled, newcomers, outcomes):
            block = [by_asset[asset] for asset in group]
            block_votes, _rounds = self._ask(
                block, judge, catalogue, contract, line_of, moving=newcomers
            )
            votes.update(block_votes)
        verdicts = classify_fit(fresh, votes)
        revoked = {row["asset_id"] for row in fresh if verdicts[row["asset_id"]]["state"] == "bad"}
        if not revoked:
            return filled, set()
        snapshot = {row["asset_id"]: dict(row) for row in before}
        restored = [
            snapshot[slot.replacing]
            for slot in outcomes
            if slot.filled_by in revoked
            and slot.replacing
            and slot.replacing not in {row["asset_id"] for row in filled}
        ]
        kept = [row for row in filled if row["asset_id"] not in revoked]
        return sorted([*kept, *restored], key=itemgetter("taken", "asset_id")), revoked

    def _voted(
        self,
        carriers: list[dict[str, Any]],
        judge,
        catalogue: ThinCatalogue,
        contract: str,
        line_of: Callable[[str], str],
    ) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], list[dict]]:
        votes, rounds = self._ask(carriers, judge, catalogue, contract, line_of)
        verdicts = classify_fit(carriers, votes)
        kept = [c for c in carriers if verdicts[c["asset_id"]]["state"] != "bad"]
        return kept, verdicts, rounds

    def _bank(self) -> dict:
        """Both votes read and write one file, so neither is paid for twice."""
        path = self._bank_path()
        return json.loads(path.read_text()) if path.exists() else {}

    def _bank_path(self) -> Path:
        return self.bank_dir / "thesis-fit.private.json"

    def _ask(self, carriers, judge, catalogue, contract, line_of, moving=None):
        """The vote over these shots; with `moving`, only those shots' answers are read."""
        bank = self._bank()
        story_of = {
            asset: story.key for story in catalogue.stories for asset in story.asset_ids
        }.get
        return vote_thesis_fit(
            judge,
            pictures=[c["asset_id"] for c in carriers],
            protected=[
                c["asset_id"]
                for c in carriers
                if is_protected(c) or (moving is not None and c["asset_id"] not in moving)
            ],
            line_of=line_of,
            thesis=catalogue.thesis,
            contract=contract,
            story_of=lambda asset: story_of(asset, "") or "",
            bank=bank,
            save=lambda: write_secret_file(self._bank_path(), json.dumps(bank, indent=1)),
        )


def thin_budget(draft: int, seats: int) -> int:
    """The calls a polish may spend: four questions per twelve draft shots (standing and fit
    once, audience in its two orders: one look at the draft) and four per seat it opens.
    Owner's budget, 09-23, with the audience's second order counted in."""
    return 4 * math.ceil(draft / BLOCK_SIZE) + 4 * seats


def _spent(asked: int, draft: int, seats: int) -> dict[str, int]:
    budget = thin_budget(draft, seats)
    if asked > budget:
        logger.warning(
            "The thin layer asked %d questions for %d shots and %d seats (budget %d)",
            asked,
            draft,
            seats,
            budget,
        )
    return {"asked": asked, "budget": budget}


def rejoined_blocks(
    partition: Sequence[Sequence[str]],
    cut: Sequence[Mapping[str, Any]],
    newcomers: set[str],
    outcomes: Sequence[ThinSlot],
) -> list[list[str]]:
    """The first vote's blocks as the filled cut holds them, only the ones a newcomer joined.

    A swap takes the place of the shot it replaced; an appended newcomer joins the first block
    whose last shot was taken at or after it, or the last block. A block grown past twelve is
    split evenly and only its parts holding a newcomer are returned.
    """
    taken = {row["asset_id"]: (str(row["taken"]), row["asset_id"]) for row in cut}
    members = [[asset for asset in block if asset in taken] for block in partition] or [[]]
    home = {asset: index for index, block in enumerate(partition) for asset in block}
    home.update(
        {slot.filled_by: home[slot.replacing] for slot in outcomes if slot.replacing in home}
    )
    ends = [taken[block[-1]] if block else None for block in members]
    for asset in sorted(newcomers & set(taken), key=taken.__getitem__):
        index = home.get(asset)
        members[_time_block(ends, taken[asset]) if index is None else index].append(asset)
    groups = (
        group
        for block in members
        for group in balanced_groups(sorted(block, key=taken.__getitem__))
    )
    return [group for group in groups if newcomers & set(group)]


def _time_block(ends: Sequence[tuple[str, str] | None], when: tuple[str, str]) -> int:
    """The first block whose last shot was taken at or after `when`, or the last block."""
    return next(
        (index for index, end in enumerate(ends) if end is not None and end >= when),
        len(ends) - 1,
    )


def _with_records(
    rows: Sequence[Mapping[str, Any]], catalogue: ThinCatalogue
) -> list[dict[str, Any]]:
    """Each shot with what the catalogue records it as, which is what protects it from a vote.

    The vote and the standing gate read `notable_record` off the shot itself, so a record the
    catalogue holds but the shot does not carry protects nothing.
    """
    marked = []
    for row in rows:
        record = catalogue.notable_record_of(row["asset_id"])
        marked.append(dict(row) | {"notable_record": record} if record else dict(row))
    return marked


def _drafted_shots(
    asset_ids_of: Mapping[str, Sequence[str]], drafted: Sequence[Mapping[str, Any]]
) -> dict[str, list[str]]:
    """The draft's shots, by the story each one belongs to. No draft reads nothing."""
    shots = {row["asset_id"] for row in drafted}
    chosen = {key: [a for a in assets if a in shots] for key, assets in asset_ids_of.items()}
    return {key: assets for key, assets in chosen.items() if assets}


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
