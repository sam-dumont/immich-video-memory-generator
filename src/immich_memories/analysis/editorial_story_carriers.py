"""From funded stories to admitted carriers.

A funded story offers its open moments; the standing gate asks whether each candidate picture
stands by itself; the pick chooses which moments tell the story; and one picture per chosen
moment is admitted as a carrier if it is free, in context and spaced from what is already
committed. The audience gate judges the finished cut, not every candidate. Freed slots are
re-granted across the stories in further passes, never to variants. Occasion recovery may keep
a weak still, but cannot override rejected motion.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import datetime
from operator import itemgetter
from typing import Any

from immich_memories.analysis.editorial_story_depth import depth_ladder, neighbours
from immich_memories.analysis.editorial_story_lookalike import LookAlikeCheck
from immich_memories.analysis.editorial_story_pick_contract import (
    carries_motion,
)
from immich_memories.analysis.editorial_story_places import PlaceShares
from immich_memories.analysis.editorial_story_replies import WEIGHT_ROLE
from immich_memories.analysis.editorial_story_shortlist import (
    DepictedChoice,
    _spaced,
    _spread,
    nearby_picture_alternatives,
    pick_story_moments,
    shortlist_story_moments,
)
from immich_memories.analysis.editorial_story_slots import PartitionedSlots
from immich_memories.analysis.editorial_story_standing import (
    WEIGHED_STORY_WEIGHTS,
    StandingGate,
)

MAX_PASSES = 3


def choice_is_starred(c: DepictedChoice, unit_by_asset: Mapping[str, Any]) -> bool:
    return any(unit_by_asset[a][1].get("favourite") for a in c.members if a in unit_by_asset)


def _seconds_apart(taken: str, others: Sequence[str]) -> float:
    when = datetime.fromisoformat(taken)
    return min(
        (abs((when - datetime.fromisoformat(o)).total_seconds()) for o in others), default=0.0
    )


def shortlist_by_partition(
    choices: Sequence[DepictedChoice],
    grants_by_part: Mapping[str | None, int],
    parts: PartitionedSlots,
    unit_by_asset: Mapping[str, Any],
    *,
    starred: Callable[[DepictedChoice], bool],
    life: Callable[[str], bool],
    kind_of: Callable[[DepictedChoice], str],
) -> list[DepictedChoice]:
    """The moments a story's grant can still reach, sampled inside each funded partition.

    A moment holds motion when any picture that can carry it plays, so a video behind a still
    keeps its group in the sample the inventory reads.
    """

    def holds_motion(choice: DepictedChoice) -> bool:
        return any(
            carries_motion(unit_by_asset[a][1]) for a in choice.members if a in unit_by_asset
        )

    return [
        c
        for part, part_choices in parts.split(choices).items()
        if grants_by_part.get(part, 0)
        for c in shortlist_story_moments(
            _spaced(part_choices, unit_by_asset),
            grants_by_part[part],
            starred=starred,
            life=life,
            kind_of=kind_of,
            plays=holds_motion,
        )
    ]


def _unit_reader(
    read: Callable[[Mapping[str, Any]], str] | None, unit_by_asset: Mapping[str, Any]
) -> Callable[[DepictedChoice], str] | None:
    """Read a moment's primary unit through an optional observation port."""
    if read is None:
        return None
    return lambda c: read(unit_by_asset[c.primary][1])


class CarrierAdmission:
    """Fund, shortlist, weigh and admit the pictures that carry each story's moments."""

    def __init__(
        self,
        judge,
        *,
        stories: Sequence[Mapping[str, Any]],
        choices_of: dict[str, list[DepictedChoice]],
        unit_by_asset: Mapping[str, Any],
        anchor_label: Mapping[str, str],
        parts: PartitionedSlots,
        gate: StandingGate,
        line_of: Callable[[str], str],
        life: Callable[[str], bool],
        excluded: Mapping[str, str],
        kind_marker: Callable[[DepictedChoice], str],
        motion_line: Callable[[Mapping[str, Any]], str] | None,
        contract: str,
        record: Callable[[str, Mapping[str, Any]], None],
        slots: int,
        calls: dict[str, int],
        mechanical_picks: bool = False,
        lookalike: LookAlikeCheck | None = None,
        places: PlaceShares | None = None,
        place_of: Callable[[str], str] = lambda _asset: "",
        vouched: Callable[[Mapping[str, Any]], bool] = lambda _carrier: True,
    ) -> None:
        self._judge = judge
        self._vouched = vouched
        self._mechanical_picks = mechanical_picks
        self.lookalike = lookalike or LookAlikeCheck(None, slots=slots)
        self.places = places or PlaceShares({}, {})
        self._place_of = place_of
        self.stories = stories
        self.choices_of = choices_of
        self._unit_by_asset = unit_by_asset
        self._anchor_label = anchor_label
        self.parts = parts
        self.gate = gate
        self._line_of = line_of
        self._life = life
        self._excluded = excluded
        self._kind_marker = kind_marker
        self._motion_line = motion_line
        self._contract = contract
        self._record = record
        self.slots = slots
        self.calls = calls
        self.carriers: list[dict] = []
        self.alternatives_of: dict[str, list[str]] = {}
        self.chosen_by_story: dict[str, list[str]] = {s["key"]: [] for s in stories}
        self.pass_records: list[dict] = []
        self.kept_without_standing: list[str] = []
        self.displaced: list[dict] = []
        self.failed_standing: list[str] = []
        self.editorially_closed: set[tuple[str, str | None]] = set()
        self._taken: set[str] = set()
        self._used_choice_keys: set[str] = set()
        self._picked_before: dict[str | tuple[str, str | None], bool] = {}

    # -- eligibility ------------------------------------------------------------------

    def starred_choice(self, c: DepictedChoice) -> bool:
        return choice_is_starred(c, self._unit_by_asset)

    def free(self, asset: str) -> bool:
        return (
            asset not in self._taken
            and asset in self._unit_by_asset
            and asset not in self._excluded
            and (
                self.parts.limit is None
                or self.parts.of_asset(asset) is None
                or sum(
                    self.parts.of_asset(c["asset_id"]) == self.parts.of_asset(asset)
                    for c in self.carriers
                )
                < self.parts.limit
            )
        )

    def compatible(self, choice, chosen) -> bool:
        occupied = [*self.carriers, *(self._unit_by_asset[c.primary][1] for c in chosen)]
        return bool(_spaced([choice], self._unit_by_asset, already=occupied))

    def open_choices(self, s) -> list[DepictedChoice]:
        """Keep competing nearby pictures; exclude only conflicts with committed carriers."""
        open_ = [
            c
            for c in self.choices_of[s["key"]]
            if c.key not in self._used_choice_keys
            and (
                (s["key"], self.parts.of_asset(c.primary)) not in self.editorially_closed
                or self.starred_choice(c)
            )
            and any(
                self.free(a) and self.gate.has_required_context(a, s["weight"], s["key"])
                for a in c.members
            )
        ]
        return _spaced(open_, self._unit_by_asset, already=self.carriers, among_choices=False)

    def carrier_for(self, choice, s, index, candidates):
        for asset in candidates:
            # Recovery may keep a weak still, not override rejected motion or source context.
            if self.gate.rejected_motion(asset) or not self.gate.has_required_context(
                asset, s["weight"], s["key"]
            ):
                continue
            # Other stories may have committed carriers since picks were compared.
            # Recovery and an alternate source must obey the actual capture clock too.
            actual = DepictedChoice(
                choice.key,
                choice.episode,
                self._unit_by_asset[asset][1]["taken"],
                choice.content,
                asset,
            )
            if not _spaced([actual], self._unit_by_asset, already=self.carriers):
                continue
            if not self.free(asset):
                continue
            family, unit = self._unit_by_asset[asset]
            rest = [
                a
                for a in choice.members
                if a != asset
                and self.free(a)
                and (
                    self.parts.limit is None or self.parts.of_asset(a) == self.parts.of_asset(asset)
                )
            ]
            return asset, self._carrier_row(unit, family, s, choice, index, asset), rest
        return None, None, []

    def _carrier_row(self, unit, family, s, choice, index, asset) -> dict:
        return unit | {
            "event": family,
            "anchor": self._anchor_label.get(family, family),
            "chapter": index,
            "why": f"{s['title']}: {choice.content[:80]}",
            "event_intention": s.get("purpose") or "",
            "line": self._line_of(asset),
            "story_episode": s["key"],
            "story_role": WEIGHT_ROLE[s["weight"]],
            "story_weight": s["weight"],
            "depicted_moment": choice.key,
            # The rest of this moment, so a later stage can swap the frame without
            # losing it. Written by the reader that keeps a moment's siblings.
            "moment_alternatives": [a for a in choice.members if a != asset],
            "standing": self.gate.scores.get(asset),
        }

    # -- one pass ---------------------------------------------------------------------

    def _shortlists(self, funded, open_of, partition_grants) -> dict[str, list[DepictedChoice]]:
        return {
            s["key"]: shortlist_by_partition(
                open_of[s["key"]],
                partition_grants[s["key"]],
                self.parts,
                self._unit_by_asset,
                starred=self.starred_choice,
                life=self._life,
                kind_of=self._kind_marker,
            )
            for s in funded
        }

    def _nearby(self, funded, short_of, open_of) -> dict[str, list[DepictedChoice]]:
        return {
            s["key"]: [
                c
                for part, primary in self.parts.split(short_of[s["key"]]).items()
                for c in nearby_picture_alternatives(
                    primary,
                    self.parts.split(open_of[s["key"]])[part],
                    self._unit_by_asset,
                    starred=self.starred_choice,
                )
            ]
            for s in funded
        }

    def _weigh_standing(self, funded, short_of, extra_of) -> None:
        # Extra nearby pictures cannot alter the established timeline's standing
        # questions. Their admission remains independently assessed and cached.
        # They ride in the same blocks as the shortlisted primaries, so one pass packs
        # its twelves once instead of leaving four part-filled rounds behind.
        self.gate.ensure(
            [
                a
                for s in funded
                for c in short_of[s["key"]]
                for a in (c.members if self.gate.thin(s["key"]) else [c.primary])
                if self.free(a)
            ]
            + [c.primary for s in funded for c in extra_of[s["key"]] if self.free(c.primary)]
        )
        self.gate.ensure(
            self._alternatives_of_failed(funded, short_of)
            + self._alternatives_of_failed(funded, extra_of)
        )

    def _alternatives_of_failed(self, funded, offered) -> list[str]:
        return [
            a
            for s in funded
            for c in offered[s["key"]]
            if not self.gate.stands(c.primary, s["weight"], s["key"])
            for a in c.alternatives
            if self.free(a)
        ]

    def _standing_moments(self, s, short_of) -> list[DepictedChoice]:
        out = []
        for c in short_of[s["key"]]:
            good = [
                a for a in c.members if self.free(a) and self.gate.stands(a, s["weight"], s["key"])
            ]
            if not good:
                self.failed_standing.append(c.primary)
                continue
            # the gate orders what it does not remove: the favourite first, then the best-standing
            order = {a: i for i, a in enumerate(good)}
            good.sort(
                key=lambda a: (
                    not self._unit_by_asset[a][1].get("favourite"),
                    -self.gate.scores.get(a, 0),
                    order[a],
                )
            )
            out.append(
                DepictedChoice(
                    key=c.key,
                    episode=c.episode,
                    taken=c.taken,
                    content=c.content,
                    primary=good[0],
                    alternatives=[*good[1:], *[a for a in c.members if a not in good]],
                )
            )
        return out

    def _ask_pick(self, s, part, eligible, n) -> list[DepictedChoice]:
        calls_before = len(self._judge.calls)

        def record_pick(name, value, *, partition=part, story_key=s["key"]):
            if value.get("editorial_limit", value["count"]) < value["count"]:
                # An explicitly declined depth grant must not return through
                # the next pass's mechanical refill. Unseen favourites remain eligible.
                self.editorially_closed.add((story_key, partition))
            if self.parts.limit is None:
                self._record(name, value)
            else:
                self._record(
                    f"{name}-{hashlib.sha256(str(partition).encode()).hexdigest()[:12]}",
                    {**value, "partition": partition},
                )

        def allows_replacement(c: DepictedChoice) -> bool:
            """Check only proposed improvements, before dropping the original choice.
            A held candidate cannot create a hole."""
            return any(
                self.free(a) for a in c.members if self.gate.stands(a, s["weight"], s["key"])
            )

        picked = pick_story_moments(
            self._judge,
            story=s,
            choices=eligible,
            count=n,
            starred=self.starred_choice,
            contract=self._contract,
            record=record_pick,
            kind_of=self._kind_marker,
            compatible=self.compatible,
            motion_of=_unit_reader(self._motion_line, self._unit_by_asset),
            plays=lambda c: carries_motion(self._unit_by_asset[c.primary][1]),
            replacement_allowed=allows_replacement,
        )
        self.calls["pick_calls"] += len(self._judge.calls) - calls_before
        return picked

    def _repeat_pick(self, eligible, n, chosen) -> list[DepictedChoice]:
        """A story already asked in this partition refills mechanically, never with a new call.

        A story with more favourites than its grant spends it across the story's whole span;
        the favourites the spread passes over stay behind it, for when one cannot be placed.
        """
        stars = [c for c in eligible if self.starred_choice(c)]
        rest = [c for c in eligible if not self.starred_choice(c)]
        preferred = [*_spread(stars, n), *_spread(rest, max(0, n - len(stars)))]
        local: list[DepictedChoice] = []
        for c in (*preferred, *(c for c in (*stars, *rest) if c not in preferred)):
            if len(local) >= n or not self.compatible(c, [*chosen, *local]):
                continue
            local.append(c)
        return sorted(local, key=lambda c: c.taken)

    def _pick_story(self, s, short_of, partition_grants) -> list[DepictedChoice]:
        chosen: list[DepictedChoice] = []
        for part, eligible in self.parts.split(self._standing_moments(s, short_of)).items():
            n = partition_grants[s["key"]].get(part, 0)
            if not eligible or not n:
                continue
            pick_key = s["key"] if self.parts.limit is None else (s["key"], part)
            if self._mechanical_picks or self._picked_before.get(pick_key):
                chosen.extend(self._repeat_pick(eligible, n, chosen))
            else:
                self._picked_before[pick_key] = True
                chosen.extend(self._ask_pick(s, part, eligible, n))
        return _spaced(chosen, self._unit_by_asset, already=self.carriers)

    def _commit(self, picks, short_of, open_of) -> int:
        return sum(
            self._commit_one(s, c, index, short_of, open_of)
            for index, s in enumerate(self.stories, 1)
            for c in picks.get(s["key"], [])
        )

    def _commit_one(self, s, c, index, short_of, open_of) -> int:
        if self.parts.limit is not None and c.key in self._used_choice_keys:
            return 0
        self._used_choice_keys.add(c.key)
        good = [a for a in c.members if self.free(a) and self.gate.stands(a, s["weight"], s["key"])]
        if not good:
            return 0
        asset, carrier, rest = self.carrier_for(c, s, index, good)
        if carrier is None or self._repeats_the_story(s, c, index, asset, carrier):
            return 0
        if self._crowds_its_place(s, c, index, asset):
            return 0
        # A spare replaces this carrier rather than joining it, so the pool is read
        # while its own partition slot is still free.
        self._admit(s, c, carrier, [*rest, *self._spares(s, asset, short_of, open_of)])
        return 1

    def _admit(self, s, choice, carrier, alternatives) -> None:
        self._taken.add(carrier["asset_id"])
        self.carriers.append(carrier)
        self.chosen_by_story[s["key"]].append(choice.key)
        self.alternatives_of[carrier["asset_id"]] = alternatives
        self.places.took(s["key"], self._place_of(carrier["asset_id"]))

    def _repeats_the_story(self, s, choice, index, asset, carrier) -> bool:
        """A further picture of a story that looks like one it already holds waits its turn."""
        kept = [c for c in self.carriers if c["story_episode"] == s["key"]]
        repeated = self.lookalike.repeats(carrier, neighbours(carrier, kept)) if kept else None
        if repeated is None:
            return False

        def readmit() -> bool:
            asset_id, row, rest = self.carrier_for(choice, s, index, [asset])
            if row is None:
                return False
            self._admit(s, choice, row, rest)
            return True

        self.lookalike.refuse(s["key"], asset, repeated, readmit)
        return True

    def _crowds_its_place(self, s, choice, index, asset) -> bool:
        """A picture of a place that already holds its share of the film waits its turn."""
        place = self._place_of(asset)
        if not self.places.full(s["key"], place):
            return False
        self.places.refused(s["key"], place, asset)

        def readmit() -> bool:
            asset_id, row, rest = self.carrier_for(choice, s, index, [asset])
            if row is None:
                return False
            self._admit(s, choice, row, rest)
            return True

        self.lookalike.crowds(s["key"], asset, place, readmit)
        return True

    def _spares(self, s, asset, short_of, open_of) -> list[str]:
        """The pool the audience gate draws a replacement from: a spare must stand by itself
        under the same rule the carrier it would replace had to meet."""
        return [
            o.primary
            for o in short_of.get(s["key"], open_of[s["key"]])
            if o.key not in self._used_choice_keys
            and self.free(o.primary)
            and self.gate.stands(o.primary, s["weight"], s["key"])
            and (
                self.parts.limit is None
                or self.parts.of_asset(o.primary) == self.parts.of_asset(asset)
            )
        ]

    def _one_pass(self, passes: int) -> int:
        open_of = {s["key"]: self.open_choices(s) for s in self.stories}
        grants, partition_grants = self.parts.allocate(
            self.stories,
            open_of,
            self.slots - len(self.carriers),
            carriers=self.carriers,
            already={k: len(v) for k, v in self.chosen_by_story.items()},
        )
        # The standing gate first, over every open moment of a funded story (all members of a thin
        # story, the primary of each moment otherwise), so the pick chooses among pictures that
        # stand. A moment whose primary fails but whose alternative stands is carried by the alternative.
        funded = [s for s in self.stories if grants.get(s["key"], 0) > 0 and open_of[s["key"]]]
        short_of = self._shortlists(funded, open_of, partition_grants)
        extra_of = self._nearby(funded, short_of, open_of)
        self._record(
            f"story-shortlist-pass-{passes}",
            {
                s["key"]: {
                    "grant": grants[s["key"]],
                    "open": [c.key for c in open_of[s["key"]]],
                    "shortlisted": [c.key for c in short_of[s["key"]]],
                    "nearby_alternatives": [c.key for c in extra_of[s["key"]]],
                }
                for s in funded
            },
        )
        self._weigh_standing(funded, short_of, extra_of)
        for s in funded:
            short_of[s["key"]] = sorted(
                [*short_of[s["key"]], *extra_of[s["key"]]], key=lambda c: c.taken
            )
        picks = {s["key"]: self._pick_story(s, short_of, partition_grants) for s in funded}
        pass_record = {
            "pass": passes,
            "free_slots": self.slots - len(self.carriers),
            "grants": {k: n for k, n in grants.items() if n},
            "eligible": {s["key"]: len(picks.get(s["key"], [])) for s in funded},
        }
        added = self._commit(picks, short_of, open_of)
        pass_record["added"] = added
        self.pass_records.append(pass_record)
        return added

    def run(self) -> None:
        """Pick the moments that tell each story, then one picture per moment that stands by
        itself. A picture carries at most one moment."""
        passes = 0
        # A refusal for looking alike frees a slot, so it buys the pass that refills it.
        while len(self.carriers) < self.slots and passes < MAX_PASSES + len(self.lookalike.refused):
            passes += 1
            refused = len(self.lookalike.refused)
            if self._one_pass(passes) == 0 and len(self.lookalike.refused) == refused:
                break
        self.calls["selection_passes"] = passes
        self._keep_occasions()
        if self.lookalike.available:
            for index, s in enumerate(self.stories, 1):
                self._deepen_moments(index, s)
        self._favourites_before_the_unvouched()
        self.lookalike.readmit(lambda: len(self.carriers) < self.slots)
        self.calls["failed_standing"] = len(self.failed_standing)
        self.calls["kept_without_standing"] = len(self.kept_without_standing)
        self.carriers.sort(key=itemgetter("taken"))

    # -- the owner's star over a picture nothing vouches for ---------------------------

    def _favourites_before_the_unvouched(self) -> None:
        """A starred picture the place bound refused comes back before a picture nothing
        vouches for keeps the slot it freed.

        The bound is about proportions; the star is the owner's own judgement, and a picture
        with no star, no recorded video and no person Immich knows has nothing to set against
        it. Pictures that are vouched for keep the bound's variety.
        """
        waiting = self.lookalike.waiting_for_their_place(
            lambda asset: bool(self._unit_by_asset.get(asset, (None, {}))[1].get("favourite"))
        )
        for row in waiting:
            victim = self._weakest_unvouched()
            if victim is None:
                return
            self._release(victim)
            if not self.lookalike.readmit_one(row):
                self._restore(victim)

    def _weakest_unvouched(self) -> dict | None:
        stories = Counter(c["story_episode"] for c in self.carriers)
        order = {id(c): i for i, c in enumerate(self.carriers)}
        unvouched = [
            c
            for c in self.carriers
            if not self._vouched(c) and c["asset_id"] not in self.kept_without_standing
        ]
        # A story keeps its only picture while another story can give one up; inside that,
        # the weakest standing goes first, and the latest admitted before an earlier one.
        return min(
            unvouched,
            key=lambda c: (
                stories[c["story_episode"]] == 1,
                c.get("standing") or 0,
                -order[id(c)],
            ),
            default=None,
        )

    def _release(self, carrier: dict) -> None:
        asset = carrier["asset_id"]
        self.carriers.remove(carrier)
        self._taken.discard(asset)
        self.chosen_by_story[carrier["story_episode"]].remove(carrier["depicted_moment"])
        self.places.gave_back(carrier["story_episode"], self._place_of(asset))
        self.displaced.append(carrier)

    def _restore(self, carrier: dict) -> None:
        asset = carrier["asset_id"]
        self.displaced.remove(carrier)
        self.carriers.append(carrier)
        self._taken.add(asset)
        self.chosen_by_story[carrier["story_episode"]].append(carrier["depicted_moment"])
        self.places.took(carrier["story_episode"], self._place_of(asset))

    # -- depth inside moments ---------------------------------------------------------

    def _deepen_moments(self, index: int, s) -> None:
        """A film still short spends its free slots on further frames of the moments this story
        shows, only when they show something new (`editorial_story_depth`). A moment admitted as
        depth earns its own rungs, so the ladder is read again while it still adds a frame."""
        while self._deepen_once(index, s):
            pass

    def _offerable(self, s) -> list[DepictedChoice]:
        """This story's moments, holding only the pictures that could carry a frame.

        The model ranks a moment's members, so its ladder walks the top three by position.
        Ranked by capture facts alone, position says little, and a picture that cannot carry
        a frame at all must not spend one of the moment's three rungs: an eight-picture
        moment was shipping two frames with five usable ones left behind. The spares that
        remain are offered furthest first in capture time from the frames of the moment
        already in the cut.
        """
        choices = self.choices_of[s["key"]]
        if not self._mechanical_picks:
            return choices
        self.gate.ensure([a for c in choices for a in c.members if self.free(a)])
        carried = {row["asset_id"] for row in self.carriers}
        offerable = []
        for c in choices:
            good = [
                a
                for a in c.members
                if a in carried or (self.free(a) and self.gate.stands(a, s["weight"], s["key"]))
            ]
            if not good:
                continue
            kept = [row["taken"] for row in self.carriers if row["depicted_moment"] == c.key]
            spare = sorted(
                (a for a in good if a not in carried),
                key=lambda a: -_seconds_apart(self._unit_by_asset[a][1]["taken"], kept),
            )
            members = [*(a for a in good if a in carried), *spare]
            offerable.append(replace(c, primary=members[0], alternatives=members[1:]))
        return offerable

    def _deepen_once(self, index: int, s) -> bool:
        if (
            len(self.carriers) >= self.slots
            or s["weight"] not in WEIGHED_STORY_WEIGHTS
            or not self.chosen_by_story[s["key"]]
        ):
            return False
        ladder = list(
            depth_ladder(
                self._offerable(s),
                chosen=self.chosen_by_story[s["key"]],
                used=self._used_choice_keys,
                group_of=lambda asset: self._unit_by_asset[asset][1].get("moment"),
                frames_of=Counter(c["depicted_moment"] for c in self.carriers),
            )
        )
        self.gate.ensure([asset for _choice, asset in ladder if self.free(asset)])
        added = False
        for choice, asset in ladder:
            if len(self.carriers) >= self.slots:
                break
            if not (self.free(asset) and self.gate.stands(asset, s["weight"], s["key"])):
                continue
            if self.places.full(s["key"], self._place_of(asset)):
                continue
            family, unit = self._unit_by_asset[asset]
            row = self._carrier_row(unit, family, s, choice, index, asset)
            kept = [c for c in self.carriers if c["story_episode"] == s["key"]]
            if self.lookalike.shows_something_new(s["key"], row, neighbours(row, kept)):
                self._used_choice_keys.add(choice.key)
                self._admit(s, choice, row | {"depth": True}, [])
                added = True
        return added

    # -- occasion integrity -----------------------------------------------------------

    def _is_unshown_occasion(self, s) -> bool:
        """An OCCASION (a story the gate read remarkable, or one the owner starred) whose every
        candidate failed the gate may still show once through a weak still. Rejected motion is
        never rescued to fill a slot; a thin "maybe" day gets nothing."""
        return (
            s["weight"] in WEIGHED_STORY_WEIGHTS
            and (s["gate"] == "remarkable" or s["seen"]["favourites"] > 0)
            and not self.chosen_by_story[s["key"]]
            and len(self.carriers) < self.slots
        )

    def _last_resort_choices(self, s) -> list[DepictedChoice]:
        # the same rule as the gate: a minor occasion is kept only through a picture with life or a star
        candidates = [
            c
            for c in self.choices_of[s["key"]]
            if s["weight"] != "minor"
            or any(
                self._life(a) or self._unit_by_asset[a][1].get("favourite")
                for a in c.members
                if a in self._unit_by_asset
            )
        ]
        return sorted(
            candidates,
            key=lambda c: (
                not any(self._life(a) for a in c.members),
                -max((self.gate.scores.get(a, 0) for a in c.members), default=0),
                not self.starred_choice(c),
                c.taken,
            ),
        )

    def _keep_occasions(self) -> None:
        for index, s in enumerate(self.stories, 1):
            if not self._is_unshown_occasion(s):
                continue
            for c in self._last_resort_choices(s):
                members = sorted(
                    c.members,
                    key=lambda a: (
                        -self.gate.scores.get(a, 0),
                        not self._unit_by_asset[a][1].get("favourite"),
                    ),
                )
                asset, carrier, rest = self.carrier_for(c, s, index, members)
                if carrier is None:
                    continue
                self._taken.add(asset)
                self.carriers.append(carrier)
                self.chosen_by_story[s["key"]].append(c.key)
                self.kept_without_standing.append(asset)
                self.alternatives_of[asset] = rest
                break
