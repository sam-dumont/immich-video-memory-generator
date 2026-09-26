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
from datetime import datetime
from operator import itemgetter
from typing import Any

from immich_memories.analysis.editorial_shot_kinds import KindOf, lacking
from immich_memories.analysis.editorial_story_lookalike import MOTION_KINDS
from immich_memories.analysis.editorial_story_shortlist import DepictedChoice, pick_story_moments
from immich_memories.analysis.editorial_structure_budget import (
    MIN_CARRIER_SECONDS,
    MIN_MOTION_SECONDS,
)
from immich_memories.analysis.editorial_thin_catalogue import ThinCatalogue
from immich_memories.analysis.editorial_thin_gates import GateRefusal, ThinGates
from immich_memories.analysis.editorial_thin_pages import (
    favourite_of_its_moment,
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
# The seats a removal opens: every one of them is refilled or says why it could not be.
REMOVALS = frozenset({VOTE_BAD, GATE_REFUSED})
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
    # The seconds a removed shot gave back: its refill may take them past the length target,
    # because the draft already held them.
    frees: float = 0.0
    # A removal's seat reads on into the film's other stories, nearest in time first, once its
    # own story's page runs out.
    fallback: tuple[dict[str, Any], ...] = ()

    @property
    def offered(self) -> tuple[dict[str, Any], ...]:
        """Every row the seat may pick from, in order: its page, then its fallback."""
        return (*self.page, *self.fallback)

    def row(self) -> dict[str, str]:
        return {
            "slot": self.key,
            "story": self.story,
            "rule": self.kind,
            "replacing": self.replacing,
            "chosen": self.filled_by,
            "outcome": self.outcome or ("seated" if self.filled_by else ""),
            "offered": str(len(self.offered)),
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
    frees: float = 0.0,
) -> tuple[list[dict[str, Any]], bool]:
    """Put one candidate in the cut, or leave the cut exactly as it was.

    The newcomer is trimmed by whatever it overruns the film by, and refused below the shortest
    a moving picture may run rather than seated as a flash. The film may run to the length
    target, or to the length it already had plus the seconds a removed shot freed (`frees`),
    whichever is longer: a swap or a refill never makes the film longer than the draft was.
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
    # A removed shot shorter than a moving picture's floor still frees a whole seat; finishing
    # shaves the fraction of a second the floor overruns.
    freed = max(frees, MIN_MOTION_SECONDS) if frees > 0 else 0.0
    limit = max(content_cap, sum(row["seconds"] for row in cut) + freed)
    excess = sum(row["seconds"] for row in result) - limit
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
    removed: Mapping[str, Mapping[str, Any]] | None = None,
    kind_of: KindOf | None = None,
    vouched: Callable[[Mapping[str, Any]], bool] = lambda _row: True,
) -> list[ThinSlot]:
    """Every seat this polish may fill, in the order the budget is spent on them.

    A notable record the cut does not hold comes first, in the room the draft left unused.
    Then a replacement for every shot the vote named or the gates refused (`removed`, the draft
    rows by asset): it takes the seconds that shot held, so it needs no room. Its page is its
    own story's, or when that story has nothing left, the pictures of the stories the film
    already holds, nearest in time first. A removal nothing is left for is still a seat, which
    records that nothing was eligible. A shot only one
    order doubted keeps its place under a swap, which needs no room at all either.

    Every refill's page leads with the kind of shot (`kind_of`: portrait or texture) its story
    holds fewer of in the cut, or the film does when the story holds none: the picker reads a
    page in order, and a film refilled from the top of plain pages drifts to posed portraits.
    Only a row the library `vouched` for (a star, a video, a known person) is lifted: a head's
    guess at a kind never puts an object nobody vouches for on top.
    """
    story_of = {asset: story.key for story in catalogue.stories for asset in story.asset_ids}
    offers = _offers(candidates_of, seen)
    removed = removed or {}
    by_asset = {row["asset_id"]: row for row in cut}
    freed = sum(row["seconds"] for row in removed.values())
    room = openable_slots(content_cap, sum(row["seconds"] for row in cut) + freed)
    slots = newcomer_slots(cut, catalogue, offers, room, refused=refused)
    appends = [
        (asset, VOTE_BAD, story_of.get(asset, ""), "")
        for asset, verdict in verdicts.items()
        if verdict["state"] == "bad"
    ]
    appends.extend((row.asset_id, GATE_REFUSED, row.story, row.moment) for row in refused)
    slots.extend(_append_slots(cut, appends, offers, catalogue.notable_record_of, removed))
    slots.extend(
        ThinSlot(
            key=f"D9{number:02d}",
            story=story_of.get(asset, ""),
            kind=VOTE_WEAK,
            page=tuple(
                records_first(
                    _other_moments(offers(story_of.get(asset, "")), by_asset.get(asset)),
                    catalogue.notable_record_of,
                )
            ),
            replacing=asset,
        )
        for number, (asset, verdict) in enumerate(sorted(verdicts.items()), 1)
        if verdict["state"] == "weak"
    )
    if kind_of is not None:
        slots = [
            s
            if s.kind == NOTABLE
            else replace(
                s,
                page=_variety_first(s, s.page, cut, kind_of, vouched),
                fallback=_variety_first(s, s.fallback, cut, kind_of, vouched),
            )
            for s in slots
        ]
    return [slot for slot in slots if slot.offered or slot.kind in REMOVALS]


def _variety_first(
    slot: ThinSlot, rows, cut, kind_of: KindOf, vouched
) -> tuple[dict[str, Any], ...]:
    """These rows of the slot with the kind its story (else the film) holds fewer of leading."""
    company = [row for row in cut if row.get("story_episode") == slot.story] or list(cut)
    want = lacking(kind_of(row["asset_id"]) for row in company if row["asset_id"] != slot.replacing)
    if want is None:
        return tuple(rows)
    return tuple(
        sorted(rows, key=lambda unit: not (kind_of(unit["asset_id"]) == want and vouched(unit)))
    )


def _offers(candidates_of, seen: set[str]):
    def offers(key: str) -> list[dict[str, Any]]:
        return [dict(unit) for unit in candidates_of(key) if unit["asset_id"] not in seen]

    return offers


def newcomer_slots(
    cut: Sequence[Mapping[str, Any]],
    catalogue: ThinCatalogue,
    offers: Callable[[str], list[dict[str, Any]]],
    room: int,
    *,
    refused: Sequence[GateRefusal] = (),
) -> list[ThinSlot]:
    """A seat for each story the catalogue records something about and the cut has no shot of.

    A story whose shot the gates took already has its seat back; a newcomer slot as well would
    be a second picture the draft never gave it, which is depth.
    """
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


def _append_slots(cut, appends, offers, record_of, removed) -> list[ThinSlot]:
    moments_in_cut = {row.get("moment") for row in cut}
    refused_moments: dict[str, list[str]] = {}
    for _asset, kind, story, moment in appends:
        if kind == GATE_REFUSED and moment:
            refused_moments.setdefault(story, []).append(moment)
    slots = []
    for number, (asset, kind, story, _moment) in enumerate(appends, 1):
        page = offers(story)
        pool = _nearest_in_the_film(cut, offers, removed.get(asset), story)
        if kind == VOTE_BAD:
            page = _other_moments(page, removed.get(asset))
            pool = _other_moments(pool, removed.get(asset))
        if story in refused_moments:
            page = gate_refill_page(page, refused_moments[story], moments_in_cut)
        else:
            page = motion_first(page)
        page = records_lead(page, record_of)
        prefix = "R" if kind == VOTE_BAD else "T"
        slots.append(
            ThinSlot(
                key=f"{prefix}{number:03d}",
                story=story,
                kind=kind,
                page=tuple(page),
                frees=float(removed[asset]["seconds"]) if asset in removed else 0.0,
                fallback=tuple(pool),
            )
        )
    return slots


def _other_moments(page, shot: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """The page without the moment of a shot the vote named: the vote judged the moment, and
    another frame of it adds nothing either."""
    moment = shot.get("moment") if shot else None
    return [dict(row) for row in page if not moment or row.get("moment") != moment]


def _why_empty(refusal: GateRefusal | None) -> str:
    return "none available" if refusal is None else f"refused by {refusal.rule}"


def _nearest_in_the_film(
    cut, offers, shot: Mapping[str, Any] | None, own: str
) -> list[dict[str, Any]]:
    """The pictures of the other stories the cut holds, the nearest in time to `shot` first."""
    if shot is None:
        return []
    when = _moment_in_time(shot["taken"])
    stories = dict.fromkeys(str(row.get("story_episode") or "") for row in cut)
    pool = [unit for key in stories if key and key != own for unit in offers(key)]
    return sorted(pool, key=lambda unit: abs(_moment_in_time(unit["taken"]) - when))


def _moment_in_time(taken: Any) -> float:
    when = datetime.fromisoformat(str(taken))
    return (when.replace(tzinfo=None) - datetime(1970, 1, 1)).total_seconds()


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

        Every seat picks first, from the first rows of its page the standing facts keep (they
        ask nothing), and the model-backed gates are asked about the chosen rows only: a page
        is a whole story. A seat whose choice the standing gate refuses picks once more from
        the same page. The chosen rows that stand are then put to the audience gate together.
        """
        current = [dict(row) for row in cut]
        taken = {row["asset_id"] for row in current}
        chosen, failed = self._picks(slots, taken=taken)
        self.gates.prefetch_audience(list(chosen.values()))
        outcomes = []
        for index, slot in enumerate(slots):
            candidate = chosen.get(index)
            if candidate is None:
                outcomes.append(replace(slot, outcome=failed.get(index, "none available")))
                continue
            refusal = self.gates.admits(candidate, cut=current, tier_of=self.tier_of)
            if refusal is not None and slot.kind in REMOVALS:
                candidate, refusal = self._chosen_again(slot, current, taken)
            if candidate is None or refusal is not None:
                outcomes.append(replace(slot, outcome=_why_empty(refusal)))
                continue
            current, changed = seat(
                current,
                candidate,
                replacing=slot.replacing,
                content_cap=self.content_cap,
                frees=slot.frees,
            )
            outcomes.append(
                replace(
                    slot,
                    filled_by=candidate["asset_id"] if changed else "",
                    outcome="" if changed else "no room for a whole carrier",
                )
            )
        return current, outcomes

    def _standing_rows(self, slot: ThinSlot, taken: set[str]) -> list[dict[str, Any]]:
        """The first rows of the seat's page nobody has taken that the standing facts keep.

        Standing is read from the facts the draft used and asks nothing, so a seat is never
        offered a picture the gate would refuse the moment it is picked.
        """
        rows: list[dict[str, Any]] = []
        free = [unit for unit in slot.offered if unit["asset_id"] not in taken]
        for start in range(0, len(free), PAGE_ROWS):
            chunk = free[start : start + PAGE_ROWS]
            self.gates.settle(chunk, self.tier_of)
            rows.extend(unit for unit in chunk if self.gates.stands_alone(unit, self.tier_of))
            if len(rows) >= PAGE_ROWS:
                break
        return rows[:PAGE_ROWS]

    def _chosen_again(
        self, slot: ThinSlot, current: Sequence[Mapping[str, Any]], taken: set[str]
    ) -> tuple[Mapping[str, Any] | None, GateRefusal | None]:
        """A removal's seat, after the gates refused its choice: one more pick from its page.

        A seat a removal opened is how the film keeps its length, so it is not given up while
        its page still holds a picture nobody has taken.
        """
        page = self._standing_rows(slot, taken)
        pick = self._choose(slot, page)
        if pick is None:
            return None, None
        pick = favourite_of_its_moment(pick, page, taken)
        taken.add(pick["asset_id"])
        self.gates.settle([pick], self.tier_of)
        if not self.gates.stands_alone(pick, self.tier_of):
            return pick, GateRefusal(pick["asset_id"], slot.story, "standing", "second choice")
        return pick, self.gates.admits(pick, cut=current, tier_of=self.tier_of)

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
            page = self._standing_rows(slots[index], taken)
            pick = self._choose(slots[index], page)
            if pick is not None:
                pick = favourite_of_its_moment(pick, page, taken)
                taken.add(pick["asset_id"])
                picks[index] = pick
        return picks

    def _choose(self, slot: ThinSlot, page: Sequence[Mapping[str, Any]]):
        if not page:
            return None
        if self.gates.prepare_candidates is not None:
            self.gates.prepare_candidates(page)
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
