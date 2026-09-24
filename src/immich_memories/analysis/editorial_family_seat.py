"""A close family member the period is full of gets one seat in its film.

Grants follow favourites, so someone photographed all month and never starred could end up in
no shot at all: the stories holding their pictures were funded for one favourite each. After
the draft, every close family member (partner, child or parent, as the people file names them)
who is on enough of the period's pictures and in none of its shots gets one: their best frame by
the rules' own standing, in the story holding most of their pictures. Nothing is asked of a model.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from itertools import chain
from typing import Any

from immich_memories.analysis.editorial_rule_banked_facts import (
    BankedFacts,
    standing_with_bank,
    withheld_by_bank,
)
from immich_memories.analysis.editorial_rule_reader import RuleStructureReader
from immich_memories.analysis.editorial_shareability_audience import exposure_flagged
from immich_memories.analysis.editorial_story_replies import close_family_on, people_on
from immich_memories.analysis.editorial_story_standing import StandingGate
from immich_memories.analysis.editorial_structure_budget import MIN_CARRIER_SECONDS
from immich_memories.analysis.editorial_structure_contract import StructurePlanningInput
from immich_memories.speech.cuts import minimum_duration

FAMILY_SEAT_VERSION = "family-seat-v1"
PERSON_FILMS = frozenset({"person_spotlight", "multi_person"})


@dataclass(frozen=True)
class FamilySeatPolicy:
    """Who is owed a seat: on at least `min_pictures` of the period, or `min_share` of it."""

    min_pictures: int = 20
    min_share: float = 0.05

    def owed(self, pictures: int, scope: int) -> bool:
        return pictures >= self.min_pictures or (scope > 0 and pictures / scope >= self.min_share)


@dataclass(frozen=True)
class FamilySeatInputs:
    """What the seat reads. `candidates_of(story_key)` is every picture of a story as a carrier
    row; `stands(asset, story)` is the story's standing bar, `score_of` the rule standing that
    ranks a person's frames, `refused` every hold that applies to this film, `has_room`
    whether the film can take one more carrier without dropping one, and `close_family` who on
    a line counts as close family in this film."""

    stories: Sequence[Mapping[str, Any]]
    candidates_of: Callable[[str], list[dict]]
    line_of: Callable[[str], str]
    scope: Sequence[str]
    stands: Callable[[str, Mapping[str, Any]], bool]
    score_of: Callable[[str], float]
    refused: Callable[[str], bool]
    has_room: Callable[[list[dict]], bool]
    policy: FamilySeatPolicy = FamilySeatPolicy()
    close_family: Callable[[str], Mapping[str, str]] = close_family_on


def seat_close_family(
    carriers: list[dict], inputs: FamilySeatInputs
) -> tuple[list[dict], dict[str, Any]]:
    """The film with one seat for every close family member it owes one, and the record of why.

    A seat is appended when the film has room; otherwise it replaces the weakest non-favourite
    of its own story. A favourite is never displaced, nor the only shot of another close family
    member. A person with no frame that clears the story's bar, or no seat to take, stays out,
    and the record says so.
    """
    on = {
        asset: inputs.close_family(inputs.line_of(asset)) for asset in dict.fromkeys(inputs.scope)
    }
    counts = Counter(chain.from_iterable(on.values()))
    relation = {name: rel for people in on.values() for name, rel in people.items()}
    film = carriers.copy()
    seats: list[dict[str, Any]] = []
    for name, pictures in counts.most_common():
        if not inputs.policy.owed(pictures, len(on)) or _shots_of(name, film, inputs):
            continue
        seats.append(
            {"relation": relation[name], "pictures": pictures} | _seat_one(name, film, on, inputs)
        )
    return film, {
        "version": FAMILY_SEAT_VERSION,
        "scope_pictures": len(on),
        "min_pictures": inputs.policy.min_pictures,
        "min_share": inputs.policy.min_share,
        "seats": seats,
    }


def _shots_of(name: str, film: Sequence[dict], inputs: FamilySeatInputs) -> int:
    return sum(name in inputs.close_family(inputs.line_of(c["asset_id"])) for c in film)


def _seat_one(name, film: list[dict], on, inputs: FamilySeatInputs) -> dict[str, Any]:
    shown = Counter(
        story["key"]
        for story in inputs.stories
        for row in inputs.candidates_of(story["key"])
        if name in on.get(row["asset_id"], ())
    )
    taken = {c["asset_id"] for c in film}
    by_key = {story["key"]: story for story in inputs.stories}
    for key, _count in shown.most_common():
        story = by_key[key]
        frames = [
            row
            for row in inputs.candidates_of(key)
            if name in on.get(row["asset_id"], ())
            and row["asset_id"] not in taken
            and not inputs.refused(row["asset_id"])
            and inputs.stands(row["asset_id"], story)
        ]
        if not frames:
            continue
        best = max(frames, key=lambda row: inputs.score_of(row["asset_id"]))
        seat = best | {"family_seat": True}
        if inputs.has_room([*film, seat]):
            film.append(seat)
            return {"story": key, "asset_id": best["asset_id"], "placed": "appended"}
        victim = _weakest_replaceable(key, film, inputs)
        if victim is not None:
            film[film.index(victim)] = seat
            return {
                "story": key,
                "asset_id": best["asset_id"],
                "placed": "replaced",
                "replaced": victim["asset_id"],
            }
    return {"placed": None, "reason": "no frame clears a story's bar with a seat to take"}


def _weakest_replaceable(key: str, film: list[dict], inputs: FamilySeatInputs) -> dict | None:
    """The story's weakest carrier that is neither a favourite nor someone's only shot."""
    victims = [
        c
        for c in film
        if c.get("story_episode") == key
        and not c.get("favourite")
        and not c.get("family_seat")
        and not any(
            _shots_of(other, film, inputs) == 1
            for other in inputs.close_family(inputs.line_of(c["asset_id"]))
        )
    ]
    if not victims:
        return None
    return min(victims, key=lambda c: (inputs.score_of(c["asset_id"]), c.get("taken") or ""))


def film_close_family(source: StructurePlanningInput) -> Callable[[str], Mapping[str, str]]:
    """Who on a line counts as close family in this film.

    The owner's partner, children and parents always do. A film about people adds each
    subject's own partner, children and parents, as the people file links them: in a film of
    the owner's partner, their parents are close family though the owner calls them in-laws.
    """
    if source.case.product not in PERSON_FILMS or source.people is None or not source.case.people:
        return close_family_on
    theirs = source.people.close_family_of(source.case.people)

    def close_family(line: str) -> Mapping[str, str]:
        found = close_family_on(line)
        for name in people_on(line):
            if name in theirs:
                found[name] = f"{theirs[name]} of the film's subject"
        return found

    return close_family


@dataclass(frozen=True)
class FilmSeatSource:
    """The planning run the seat reads: its source, the rules reader (None on a run the model
    planned whole), the draft's selection, the playable units and what earlier runs banked."""

    source: Any
    rules: Any
    selection: Any
    units: Mapping[str, list[dict]]
    banked: BankedFacts


def seat_in_film(
    carriers: list[dict],
    film: FilmSeatSource,
    *,
    candidates_of: Callable[[str], list[dict]],
    life: Callable[[str], bool],
    excluded: Mapping[str, str],
    record: Callable[[str, Mapping[str, Any]], None],
) -> list[dict]:
    """Run the seat over a planned cut with the rules' own standing: no model is asked."""
    source, selection = film.source, film.selection
    unit_by_asset = {u["asset_id"]: (f, u) for f, rows in film.units.items() for u in rows}

    def favourite(asset: str) -> bool:
        return bool(unit_by_asset.get(asset, (None, {}))[1].get("favourite"))

    rules = film.rules if film.rules is not None else RuleStructureReader(source)
    standing = standing_with_bank(rules.standing, film.banked, favourite=favourite)
    gate = StandingGate(
        None,
        line_of=lambda asset: selection.lines.get(asset, ""),
        life=life,
        unit_by_asset=unit_by_asset,
        pictures_of={s["key"]: s["seen"]["pictures"] for s in selection.story.stories},
        bank=None,
        save=None,
        calls={"standing_rounds": 0},
        score_of=standing,
    )

    def stands(asset: str, story: Mapping[str, Any]) -> bool:
        gate.ensure([asset])
        return gate.stands(asset, story["weight"], story["key"])

    withheld = withheld_by_bank(film.banked, favourite=favourite)

    def refused(asset: str) -> bool:
        heads = source.audience_annotations.get(asset)
        shared_hold = source.audience != "family" and exposure_flagged(
            dict(heads.heads) if heads else {}
        )
        return asset in excluded or withheld(asset) or shared_hold

    def has_room(cut: list[dict]) -> bool:
        if len(cut) > selection.slots:
            return False
        if source.render_timing is None:
            return True
        budget = source.render_timing.resolve(cut, source.assets).content_budget
        return sum(minimum_duration(c, MIN_CARRIER_SECONDS) for c in cut) <= budget + 1e-6

    policy = source.config.editorial.people
    seated, audit = seat_close_family(
        carriers,
        FamilySeatInputs(
            stories=selection.story.stories,
            candidates_of=candidates_of,
            line_of=lambda asset: selection.lines.get(asset, ""),
            scope=list(chain.from_iterable(source.moment_asset_ids.values())),
            stands=stands,
            score_of=standing,
            refused=refused,
            has_room=has_room,
            policy=FamilySeatPolicy(policy.seat_min_pictures, policy.seat_min_share),
            close_family=film_close_family(source),
        ),
    )
    record("family-seat", audit)
    return seated
