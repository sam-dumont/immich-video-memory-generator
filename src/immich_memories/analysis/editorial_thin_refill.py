"""The seats a polish may fill, and the transaction that fills one.

A slot is a second the film is missing: a shot the gates refused, a shot the vote named, or a
story of the scope the catalogue records something about that the cut never gave a voice to.
Nothing else opens one. There is no depth pass, no release ladder and no cap that could hand a
seat to a story the draft did not choose, and a swap only takes its shot out once a replacement
has passed everything.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, replace
from operator import itemgetter
from typing import Any

from immich_memories.analysis.editorial_story_lookalike import MOTION_KINDS
from immich_memories.analysis.editorial_story_shortlist import DepictedChoice, pick_story_moments
from immich_memories.analysis.editorial_structure_budget import (
    MIN_CARRIER_SECONDS,
    MIN_MOTION_SECONDS,
)
from immich_memories.analysis.editorial_thin_catalogue import ThinCatalogue
from immich_memories.analysis.editorial_thin_gates import GateRefusal, ThinGates
from immich_memories.analysis.editorial_thin_pages import (
    gate_refill_page,
    motion_first,
    newcomer_stories,
    records_first,
    records_lead,
)

NOTABLE = "notable"
VOTE_BAD = "vote-bad"
VOTE_WEAK = "vote-weak"
GATE_REFUSED = "gate-refused"
# The picker reads the first twelve rows of a page, in the page's own order: the refused shot's
# moment, the moments the cut lacks, motion first. A thousand-picture story is not a longer ask.
PAGE_ROWS = 12
# A seat's first choice, and one more when the standing gate refuses it.
PICK_ROUNDS = 2


@dataclass(frozen=True)
class ThinSlot:
    """One seat, the story it belongs to, and the page the picker will be shown."""

    key: str
    story: str
    kind: str
    page: tuple[dict[str, Any], ...]
    replacing: str = ""
    filled_by: str = ""
    outcome: str = ""

    def row(self) -> dict[str, str]:
        return {
            "slot": self.key,
            "story": self.story,
            "rule": self.kind,
            "replacing": self.replacing,
            "chosen": self.filled_by,
            "outcome": self.outcome or ("seated" if self.filled_by else ""),
            "offered": str(len(self.page)),
        }


def openable_slots(content_cap: float, planned: float) -> int:
    """How many more shots the film has room for, at production's minimum carrier length.

    A seat opened with less room than a carrier needs asks the model to choose a shot that can
    never be seated. A swap is exempt: it frees its own place.
    """
    return max(0, int((content_cap - planned) // MIN_CARRIER_SECONDS))


def seat(
    cut: Sequence[Mapping[str, Any]],
    candidate: Mapping[str, Any],
    *,
    replacing: str,
    content_cap: float,
) -> tuple[list[dict[str, Any]], bool]:
    """Put one candidate in the cut, or leave the cut exactly as it was.

    The newcomer is trimmed by whatever it overruns the film by, and refused below the shortest
    a moving picture may run rather than seated as a flash.
    """
    if candidate["asset_id"] in {c["asset_id"] for c in cut}:
        return [dict(c) for c in cut], False
    result = [deepcopy(dict(c)) for c in cut]
    seated = deepcopy(dict(candidate))
    if replacing:
        places = [index for index, row in enumerate(result) if row["asset_id"] == replacing]
        if len(places) != 1:
            return [dict(c) for c in cut], False
        seated["seconds"] = min(seated["seconds"], result[places[0]]["seconds"])
        result[places[0]] = seated
    else:
        result.append(seated)
    result.sort(key=itemgetter("taken", "asset_id"))
    excess = sum(row["seconds"] for row in result) - content_cap
    if excess > 0:
        if seated["seconds"] - excess < MIN_MOTION_SECONDS:
            return [dict(c) for c in cut], False
        seated["seconds"] -= excess
    return result, True


def plan_slots(
    cut: Sequence[Mapping[str, Any]],
    *,
    catalogue: ThinCatalogue,
    verdicts: Mapping[str, Mapping[str, Any]],
    refused: Sequence[GateRefusal],
    candidates_of: Callable[[str], Sequence[Mapping[str, Any]]],
    seen: set[str],
    content_cap: float,
) -> list[ThinSlot]:
    """Every seat this polish may fill, in the order the budget is spent on them.

    A notable record the cut does not hold comes first, then a replacement for a shot the vote
    named and for one the gates refused, both bounded by the room the film actually has. A shot
    only one order doubted keeps its place under a swap, which needs no room at all.
    """
    story_of = {asset: story.key for story in catalogue.stories for asset in story.asset_ids}
    offers = _offers(candidates_of, seen)
    room = openable_slots(content_cap, sum(row["seconds"] for row in cut))
    slots = _newcomer_slots(cut, catalogue, refused, offers, room)
    appends = [
        (asset, VOTE_BAD, story_of.get(asset, ""), "")
        for asset, verdict in verdicts.items()
        if verdict["state"] == "bad"
    ]
    appends.extend((row.asset_id, GATE_REFUSED, row.story, row.moment) for row in refused)
    slots.extend(
        _append_slots(cut, appends, offers, room - len(slots), catalogue.notable_record_of)
    )
    slots.extend(
        ThinSlot(
            key=f"D9{number:02d}",
            story=story_of.get(asset, ""),
            kind=VOTE_WEAK,
            page=tuple(records_first(offers(story_of.get(asset, "")), catalogue.notable_record_of)),
            replacing=asset,
        )
        for number, (asset, verdict) in enumerate(sorted(verdicts.items()), 1)
        if verdict["state"] == "weak"
    )
    return [slot for slot in slots if slot.page]


def _offers(candidates_of, seen: set[str]):
    def offers(key: str) -> list[dict[str, Any]]:
        return [dict(unit) for unit in candidates_of(key) if unit["asset_id"] not in seen]

    return offers


def _newcomer_slots(cut, catalogue, refused, offers, room: int) -> list[ThinSlot]:
    # A story whose shot the gates took already has its seat back; a newcomer slot as well would
    # be a second picture the draft never gave it, which is depth.
    held = {str(row.get("story_episode") or "") for row in cut} | {row.story for row in refused}
    stories = newcomer_stories(
        catalogue,
        held=held,
        covered_days={str(row["taken"])[:10] for row in cut},
        offers=lambda key: bool(offers(key)),
    )
    return [
        ThinSlot(
            key=f"N{number:03d}",
            story=story.key,
            kind=NOTABLE,
            page=tuple(records_first(offers(story.key), catalogue.notable_record_of)),
        )
        for number, story in enumerate(stories[: max(0, room)], 1)
    ]


def _append_slots(cut, appends, offers, room: int, record_of) -> list[ThinSlot]:
    moments_in_cut = {row.get("moment") for row in cut}
    refused_moments: dict[str, list[str]] = {}
    for _asset, kind, story, moment in appends:
        if kind == GATE_REFUSED and moment:
            refused_moments.setdefault(story, []).append(moment)
    slots = []
    for number, (_asset, kind, story, _moment) in enumerate(appends[: max(0, room)], 1):
        page = offers(story)
        if story in refused_moments:
            page = gate_refill_page(page, refused_moments[story], moments_in_cut)
        else:
            page = motion_first(page)
        page = records_lead(page, record_of)
        prefix = "R" if kind == VOTE_BAD else "T"
        slots.append(
            ThinSlot(key=f"{prefix}{number:03d}", story=story, kind=kind, page=tuple(page))
        )
    return slots


@dataclass(frozen=True)
class ThinRefill:
    """Fill the seats, one candidate at a time, through the production picker and the gates."""

    judge: Any
    gates: ThinGates
    contract: str
    line_of: Callable[[str], str]
    record: Callable[[str, Mapping[str, Any]], None]
    tier_of: Mapping[str, str]
    title_of: Mapping[str, str]
    content_cap: float

    def fill(
        self, cut: Sequence[Mapping[str, Any]], slots: Sequence[ThinSlot]
    ) -> tuple[list[dict[str, Any]], list[ThinSlot]]:
        """The cut these seats leave, and what happened in each one.

        Every seat picks first, and the gates are asked about the chosen rows only: a page is a
        whole story, and on the measured year putting every page to the standing gate cost 174
        requests for 24 picks. A seat whose choice the standing gate refuses picks once more
        from the same page.
        """
        current = [dict(row) for row in cut]
        chosen, failed = self._picks(slots, taken={row["asset_id"] for row in current})
        outcomes = []
        for index, slot in enumerate(slots):
            candidate = chosen.get(index)
            if candidate is None:
                outcomes.append(replace(slot, outcome=failed.get(index, "none available")))
                continue
            refusal = self.gates.admits(candidate, cut=current, tier_of=self.tier_of)
            if refusal is not None:
                outcomes.append(replace(slot, outcome=f"refused by {refusal.rule}"))
                continue
            current, changed = seat(
                current, candidate, replacing=slot.replacing, content_cap=self.content_cap
            )
            outcomes.append(
                replace(
                    slot,
                    filled_by=candidate["asset_id"] if changed else "",
                    outcome="" if changed else "no room for a whole carrier",
                )
            )
        return current, outcomes

    def _picks(
        self, slots: Sequence[ThinSlot], *, taken: set[str]
    ) -> tuple[dict[int, Mapping[str, Any]], dict[int, str]]:
        """Each seat's choice that stands, by seat, and why a seat that has none has none."""
        chosen: dict[int, Mapping[str, Any]] = {}
        failed: dict[int, str] = {}
        pending = list(range(len(slots)))
        for _round in range(PICK_ROUNDS):
            picks = self._pick_round(slots, pending, taken)
            self.gates.settle(list(picks.values()), self.tier_of)
            pending = []
            for index, pick in picks.items():
                if self.gates.stands_alone(pick, self.tier_of):
                    chosen[index] = pick
                    failed.pop(index, None)
                else:
                    failed[index] = "refused by standing"
                    pending.append(index)
        return chosen, failed

    def _pick_round(
        self, slots: Sequence[ThinSlot], pending: Sequence[int], taken: set[str]
    ) -> dict[int, Mapping[str, Any]]:
        """One pick per pending seat, from the first rows of its page nobody has taken."""
        picks = {}
        for index in pending:
            page = [unit for unit in slots[index].page if unit["asset_id"] not in taken]
            pick = self._choose(slots[index], page[:PAGE_ROWS])
            if pick is not None:
                taken.add(pick["asset_id"])
                picks[index] = pick
        return picks

    def _choose(self, slot: ThinSlot, page: Sequence[Mapping[str, Any]]):
        if not page:
            return None
        by_asset = {unit["asset_id"]: unit for unit in page}
        choices = [
            DepictedChoice(
                key=f"source:{unit['asset_id']}",
                episode=slot.story,
                taken=str(unit["taken"]),
                content=self.line_of(unit["asset_id"]),
                primary=unit["asset_id"],
            )
            for unit in page
        ]
        picked = pick_story_moments(
            self.judge,
            story={
                "key": f"{slot.story}-{slot.key}",
                "title": self.title_of.get(slot.story, slot.story),
                "purpose": "",
            },
            choices=choices,
            count=1,
            starred=lambda choice: bool(by_asset[choice.primary].get("favourite")),
            contract=self.contract,
            record=self.record,
            plays=lambda choice: by_asset[choice.primary].get("kind") in MOTION_KINDS,
            # The pick's order bias is cheap to live with here: the standing and audience gates
            # judge the chosen row, and a refused one is picked again.
            orders=1,
        )
        return by_asset[picked[0].primary] if picked else None
