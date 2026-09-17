"""From funded stories to admitted carriers.

A funded story offers its open moments; the standing gate asks whether each candidate picture
stands by itself; the pick chooses which moments tell the story; and one picture per chosen
moment is admitted as a carrier if it is free, in context and spaced from what is already
committed. The audience gate judges the finished cut, not every candidate. Freed slots are
re-granted across the stories in further passes, never to variants. An occasion whose every
candidate failed still shows once.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from operator import itemgetter
from typing import Any

from immich_memories.analysis.editorial_block_votes import judge_standing
from immich_memories.analysis.editorial_story_depth import depth_ladder, neighbours
from immich_memories.analysis.editorial_story_lookalike import LookAlikeCheck
from immich_memories.analysis.editorial_story_pick_contract import carries_motion
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

MAX_PASSES = 3
WEIGHED_STORY_WEIGHTS = ("dominant", "major", "minor")


def choice_is_starred(c: DepictedChoice, unit_by_asset: Mapping[str, Any]) -> bool:
    return any(unit_by_asset[a][1].get("favourite") for a in c.members if a in unit_by_asset)


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
    read: Callable[[dict], str] | None, unit_by_asset: Mapping[str, Any]
) -> Callable[[DepictedChoice], str] | None:
    """Read a moment's primary unit through an optional observation port."""
    if read is None:
        return None
    return lambda c: read(unit_by_asset[c.primary][1])


class StandingGate:
    """Does a picture stand by itself, and may it serve as context inside its story?"""

    def __init__(
        self,
        judge,
        *,
        contract: str,
        period_label: str,
        line_of: Callable[[str], str],
        life: Callable[[str], bool],
        unit_by_asset: Mapping[str, Any],
        pictures_of: Mapping[str, int],
        bank: dict | None,
        save: Callable[[], None] | None,
        calls: dict[str, int],
        score_of: Callable[[str], int] | None = None,
    ) -> None:
        self._judge = judge
        self._score_of = score_of
        self._contract = contract
        self._period_label = period_label
        self._line_of = line_of
        self._life = life
        self._unit_by_asset = unit_by_asset
        self._pictures_of = pictures_of
        self._bank = bank
        self._save = save
        self._calls = calls
        self.scores: dict[str, int] = {}
        self.context_rejected: set[tuple[str, str]] = set()

    def ensure(self, assets: Sequence[str]) -> None:
        unknown = [a for a in dict.fromkeys(assets) if a not in self.scores and self._line_of(a)]
        if not unknown:
            return
        if self._score_of is not None:
            self.scores.update({a: self._score_of(a) for a in unknown})
            return
        self._calls["standing_rounds"] += 1
        votes = judge_standing(
            self._judge,
            pictures=unknown,
            line_of=self._line_of,
            contract=self._contract,
            period_label=self._period_label,
            bank=self._bank,
            save=self._save,
        )
        for a, (n, _why) in votes.items():
            self.scores[a] = n
        for a in unknown:
            self.scores.setdefault(a, 0)

    def _starred(self, asset: str) -> bool:
        return (
            bool(self._unit_by_asset[asset][1].get("favourite"))
            if asset in self._unit_by_asset
            else False
        )

    def thin(self, story_key: str) -> bool:
        """A story of one or two pictures has no context for a weak picture to serve."""
        return self._pictures_of.get(story_key, 0) <= 2

    def has_required_context(self, asset: str, weight: str, story_key: str) -> bool:
        """The existing context requirement is eligibility, not a recoverable weak vote."""
        starred = bool(self._unit_by_asset[asset][1].get("favourite"))
        allowed = (
            self._life(asset)
            or starred
            or (weight in WEIGHED_STORY_WEIGHTS and self._pictures_of.get(story_key, 0) > 2)
        )
        if not allowed:
            self.context_rejected.add((story_key, asset))
        return allowed

    def stands(self, asset: str, weight: str, story_key: str = "") -> bool:
        """A glimpse, or a story of one or two pictures, has no context to serve, so its picture
        must stand entirely alone (named weak by neither order). Inside a dominant or major story a
        picture with people or animals in it serves its purpose with context and is only ORDERED by
        the gate, never removed; a lifeless one (a room, an object) needs one order's approval. In a
        minor story a picture with life needs one order, a lifeless one both."""
        score = self.scores.get(asset, 0)
        lively = self._life(asset)
        thin = self.thin(story_key)
        if not self.has_required_context(asset, weight, story_key):
            return False
        if not lively and not self._starred(asset) and weight == "minor":
            # Context pictures in a minor story still need both standing votes.
            return score == 2
        if weight == "glimpse" or thin:
            return score == 2
        if weight in ("dominant", "major"):
            return lively or score >= 1
        return score >= 1


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
        motion_line: Callable[[dict], str] | None,
        contract: str,
        record: Callable[[str, Mapping[str, Any]], None],
        slots: int,
        calls: dict[str, int],
        mechanical_picks: bool = False,
        lookalike: LookAlikeCheck | None = None,
    ) -> None:
        self._judge = judge
        self._mechanical_picks = mechanical_picks
        self.lookalike = lookalike or LookAlikeCheck(None, slots=slots)
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
            # Recovery may waive standing votes, never this eligibility invariant.
            if not self.gate.has_required_context(asset, s["weight"], s["key"]):
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
        """A story already asked in this partition refills mechanically, never with a new call."""
        stars = [c for c in eligible if self.starred_choice(c)]
        rest = [c for c in eligible if not self.starred_choice(c)]
        preferred = [*stars, *_spread(rest, max(0, n - len(stars[:n])))]
        local: list[DepictedChoice] = []
        for c in (*preferred, *(c for c in rest if c not in preferred)):
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
        added = 0
        for index, s in enumerate(self.stories, 1):
            for c in picks.get(s["key"], []):
                if self.parts.limit is not None and c.key in self._used_choice_keys:
                    continue
                self._used_choice_keys.add(c.key)
                good = [
                    a
                    for a in c.members
                    if self.free(a) and self.gate.stands(a, s["weight"], s["key"])
                ]
                if not good:
                    continue
                asset, carrier, rest = self.carrier_for(c, s, index, good)
                if carrier is None or self._repeats_the_story(s, c, index, asset, carrier):
                    continue
                # A spare replaces this carrier rather than joining it, so the pool is read
                # while its own partition slot is still free.
                self._admit(s, c, carrier, [*rest, *self._spares(s, asset, short_of, open_of)])
                added += 1
        return added

    def _admit(self, s, choice, carrier, alternatives) -> None:
        self._taken.add(carrier["asset_id"])
        self.carriers.append(carrier)
        self.chosen_by_story[s["key"]].append(choice.key)
        self.alternatives_of[carrier["asset_id"]] = alternatives

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
        self.lookalike.readmit(lambda: len(self.carriers) < self.slots)
        self.calls["failed_standing"] = len(self.failed_standing)
        self.calls["kept_without_standing"] = len(self.kept_without_standing)
        self.carriers.sort(key=itemgetter("taken"))

    # -- depth inside moments ---------------------------------------------------------

    def _deepen_moments(self, index: int, s) -> None:
        """A film still short spends a free slot on another frame of a moment this story shows,
        only when it shows something new (`editorial_story_depth`)."""
        if (
            len(self.carriers) >= self.slots
            or s["weight"] not in WEIGHED_STORY_WEIGHTS
            or not self.chosen_by_story[s["key"]]
        ):
            return
        ladder = list(
            depth_ladder(
                self.choices_of[s["key"]],
                chosen=self.chosen_by_story[s["key"]],
                used=self._used_choice_keys,
                group_of=lambda asset: self._unit_by_asset[asset][1].get("moment"),
                frames_of=Counter(c["depicted_moment"] for c in self.carriers),
            )
        )
        self.gate.ensure([asset for _choice, asset in ladder if self.free(asset)])
        for choice, asset in ladder:
            if len(self.carriers) >= self.slots:
                return
            if not (self.free(asset) and self.gate.stands(asset, s["weight"], s["key"])):
                continue
            family, unit = self._unit_by_asset[asset]
            row = self._carrier_row(unit, family, s, choice, index, asset)
            kept = [c for c in self.carriers if c["story_episode"] == s["key"]]
            if self.lookalike.shows_something_new(s["key"], row, neighbours(row, kept)):
                self._used_choice_keys.add(choice.key)
                self._admit(s, choice, row | {"depth": True}, [])

    # -- occasion integrity -----------------------------------------------------------

    def _is_unshown_occasion(self, s) -> bool:
        """An OCCASION (a story the gate read remarkable, or one the owner starred) whose every
        candidate failed the gate still shows once, rather than vanishing. A weak frame beats a
        missing occasion; a thin "maybe" day gets nothing."""
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
