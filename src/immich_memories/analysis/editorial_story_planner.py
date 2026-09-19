"""Story-first selection over reusable episode assessments.

Episode importance comes from scoped admission or the existing monthly reading. Stories get
weighted slots capped by available moments, then carrier selection checks their pictures.
When the moments run out, the film is shorter.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from operator import itemgetter
from typing import Any

from immich_memories.analysis.editorial_person_period_facts import (
    arrival_notes,
    person_period_facts,
)
from immich_memories.analysis.editorial_story_carriers import (
    CarrierAdmission,
    StandingGate,
    choice_is_starred,
    shortlist_by_partition,
)
from immich_memories.analysis.editorial_story_lookalike import LookAlikeCheck, PairLooksAlike
from immich_memories.analysis.editorial_story_pick_contract import (
    carries_motion,
    source_kind_marker,
)
from immich_memories.analysis.editorial_story_places import place_shares
from immich_memories.analysis.editorial_story_reading import (
    PeriodStory,
    read_period_story,
    story_episode_rows,
)
from immich_memories.analysis.editorial_story_replies import WEIGHT_ROLE, WEIGHTS, relations_on
from immich_memories.analysis.editorial_story_shortlist import (
    DepictedChoice,
    _capture_group_moments,
)
from immich_memories.analysis.editorial_story_slots import PartitionedSlots
from immich_memories.analysis.editorial_story_threads import fold_threads, thread_scope
from immich_memories.analysis.editorial_story_trips import (
    FilmTrips,
    reserve_trip_depth,
    trip_fold,
)
from immich_memories.analysis.subject_framing import SubjectVisibility

STORY_PLANNER_VERSION = "story-first-selection-v7-prepared-candidates"
TIER_NAME = {0: "remarkable", 1: "maybe", 2: "background"}
GATE_ORDER = {"remarkable": 0, "maybe": 1, "background": 2}


@dataclass
class StorySelection:
    carriers: list[dict]
    story: PeriodStory
    episodes: list[dict]
    alternatives_of: dict[str, list[str]]
    slots: int
    calls: dict[str, int]
    # The annotation line of every unit, so a replacement carrier can describe itself.
    lines: Mapping[str, str]

    def record(self) -> dict[str, Any]:
        return {
            "version": STORY_PLANNER_VERSION,
            "slots": self.slots,
            "thesis": self.story.thesis,
            "priorities": self.story.priorities,
            "episodes": self.episodes,
            "calls": self.calls,
        }


class _MomentUnits:
    """The playable units of a moment, and what the memory-worthy gate read of its happening."""

    def __init__(
        self,
        event_units: Mapping[str, list[dict]],
        family_of_moment: Mapping[str, str],
        family_tier: Mapping[str, int],
    ) -> None:
        self._by_moment: dict[str, list[dict]] = {}
        for units in event_units.values():
            for u in units:
                self._by_moment.setdefault(u.get("moment") or "", []).append(u)
        self._family_of_moment = family_of_moment
        self._family_tier = family_tier

    def of(self, moment_keys) -> list[dict]:
        seen: set[str] = set()
        units = []
        for m in moment_keys:
            for u in self._by_moment.get(m, []):
                if u["asset_id"] not in seen:
                    seen.add(u["asset_id"])
                    units.append(u)
        return sorted(units, key=itemgetter("taken"))

    def gate_of(self, moment_keys) -> str:
        tiers = [
            self._family_tier[self._family_of_moment[m]]
            for m in moment_keys
            if self._family_of_moment.get(m) in self._family_tier
        ]
        return TIER_NAME[min(tiers)] if tiers else ""


def _episode_hints(
    episodes,
    *,
    units: _MomentUnits,
    place_of_moment: Mapping[Any, str],
    lines: Mapping[str, str],
    arrivals_of: Callable[[Sequence[str]], list[dict[str, str]]] = lambda _moments: [],
) -> dict[str, dict]:
    """What the synthesis sees beside each day episode: its size, its place and its company.

    `arrivals_of(moments)` is the people the library first holds in those moments' own month
    (`editorial_person_period_facts`). The relation counts say who was there; only this says
    that this is where someone starts, which is what a period can be about.
    """
    hints = {}
    for e in episodes:
        rows = units.of(e.moments)
        days = sorted({u["taken"][:10] for u in rows})
        places = [place_of_moment.get(m, "") for m in e.moments if place_of_moment.get(m)]
        hint: dict[str, Any] = {
            "day": days[0] if days else "",
            "moments": len({u.get("moment") for u in rows}),
            "pictures": len(rows),
            "favourites": sum(1 for u in rows if u.get("favourite")),
        }
        if places:
            # Equal counts keep the first source place, including across processes.
            hint["place"] = Counter(places).most_common(1)[0][0]
        hint |= {
            key: value
            for key, value in (
                ("relations", _relation_counts(rows, lines)),
                (
                    "gate",
                    units.gate_of(e.moments)
                    or {
                        "central": "remarkable",
                        "supporting": "maybe",
                        "incidental": "background",
                    }.get(e.page_role, ""),
                ),
                ("arrivals", arrivals_of(e.moments)),
            )
            if value
        }
        hints[e.key] = hint
    return hints


def _arrivals_from(tables: Mapping[str, Any]) -> Callable[[Sequence[str]], list[dict[str, str]]]:
    """Who the library first holds in a stretch's own month, from the sealed wall it came from.

    A production wall always carries a `people` table, empty or not. A caller that hands the
    planner no wall at all has no tagged people either, so nothing arrives in it.
    """
    if "people" not in tables:
        return lambda _moments: []
    return lambda moments: arrival_notes(person_period_facts(tables, moments))


def _relation_counts(rows, lines: Mapping[str, str]) -> dict[str, int]:
    """How many of a stretch's pictures show someone of each relation to the owner."""
    counts = Counter(rel for u in rows for rel in relations_on(lines.get(u["asset_id"], "")))
    return dict(counts)


def _first_day(story_units: Mapping[str, list[dict]], s) -> str:
    return min((u["taken"][:10] for u in story_units.get(s["key"]) or []), default="")


def funding_order(
    stories: Sequence[dict[str, Any]], priorities: Sequence[Mapping[str, Any]] = ()
) -> list[dict[str, Any]]:
    """The order a film funds its stories in: the weight word; inside a word, a detected trip
    first (a trip carries its own weight), then the memory-worthy gate's word and the moments the
    story holds, then the reader's own order of its stories (`priorities`), then the day it
    starts on.

    The reader's order breaks ties rather than leading: it is the order the grouping named its
    stories in, and a period the reader filed as one story and the day rule split comes first in
    it, which on a year of more stories than slots pushed a weekend away out of the film. A star is
    an indicator of a picture: it wins its moment inside a story, never the story's place here.
    """
    rank = {tuple(p["episodes"]): n for n, p in enumerate(priorities)}
    return sorted(
        stories,
        key=lambda s: (
            WEIGHTS.index(s["weight"]),
            not s.get("trip"),
            GATE_ORDER.get(s["gate"], 3),
            -s["seen"]["moments"],
            rank.get(tuple(s["episodes"]), len(rank)),
            s["first_day"],
        ),
    )


def _weighed_stories(
    story: PeriodStory, hints: Mapping[str, dict], units: _MomentUnits
) -> tuple[list[dict], dict[str, list[dict]]]:
    """The stories in funding order, with the units they span over every day they touch."""
    episode_of = {e.key: e for e in story.episodes}
    story_units: dict[str, list[dict]] = {}
    for s in story.stories:
        moment_keys = [m for key in s["episodes"] for m in episode_of[key].moments]
        story_units[s["key"]] = units.of(moment_keys)
        readings = (str(hints.get(k, {}).get("gate") or "") for k in s["episodes"])
        s["gate"] = units.gate_of(moment_keys) or min(
            readings, key=lambda value: GATE_ORDER.get(value, 3), default=""
        )
        s["seen"] = {
            "days": len({u["taken"][:10] for u in story_units[s["key"]]}),
            "moments": sum(int(hints.get(k, {}).get("moments", 0)) for k in s["episodes"]),
            "pictures": len(story_units[s["key"]]),
            "favourites": sum(1 for u in story_units[s["key"]] if u.get("favourite")),
        }
        s["first_day"] = _first_day(story_units, s)
    stories = funding_order(
        [s for s in story.stories if story_units.get(s["key"])], story.priorities
    )
    return stories, story_units


def _capture_group_choices(
    stories: Sequence[Mapping[str, Any]],
    story_units: Mapping[str, list[dict]],
    **picking,
) -> dict[str, list[DepictedChoice]]:
    """Capture groups establish story capacity before its candidates are shortlisted."""
    choices_of: dict[str, list[DepictedChoice]] = {}
    for s in stories:
        out = _capture_group_moments(story_units[s["key"]], **picking)
        for c in out:
            c.episode = s["key"]
        choices_of[s["key"]] = out
    return choices_of


def _shortlisted_units(
    stories: Sequence[Mapping[str, Any]],
    granted: Mapping[str, int],
    partition_grants: Mapping[str, Mapping[str | None, int]],
    choices_of: Mapping[str, list[DepictedChoice]],
    parts: PartitionedSlots,
    unit_by_asset: Mapping[str, Any],
    *,
    starred: Callable[[DepictedChoice], bool],
    life: Callable[[str], bool],
    kind_of: Callable[[DepictedChoice], str],
) -> dict[str, set[str]]:
    """Per funded story, the pictures its slots can still land on: every member of the capture
    groups its own shortlist keeps. Keeping each group whole preserves nearby alternatives
    and usable depth without paying to reinterpret its prepared captions."""
    allowed: dict[str, set[str]] = {}
    for s in stories:
        if granted[s["key"]] == 0:
            continue
        short = shortlist_by_partition(
            choices_of[s["key"]],
            partition_grants[s["key"]],
            parts,
            unit_by_asset,
            starred=starred,
            life=life,
            kind_of=kind_of,
        )
        allowed[s["key"]] = {a for c in short for a in c.members}
    return allowed


def _candidate_scope(stories, granted, choices_of, allowed) -> dict[str, dict[str, Any]]:
    scope = {}
    for s in stories:
        if granted[s["key"]] == 0:
            continue
        spendable = allowed.get(s["key"], set())
        scope[s["key"]] = {
            "groups_offered": len(choices_of[s["key"]]),
            "groups_shortlisted": sum(
                1 for c in choices_of[s["key"]] if not spendable.isdisjoint(c.members)
            ),
            "units_offered": len(spendable),
        }
    return scope


def _caption_choices(stories, story_units, *, allowed, parts, partition_grants, description_of):
    choices = {}
    for story in stories:
        key = story["key"]
        candidates = [
            unit
            for unit in story_units[key]
            if unit["asset_id"] in allowed.get(key, set())
            and (
                parts.limit is None
                or partition_grants[key].get(parts.of_asset(unit["asset_id"]), 0)
            )
        ]
        if candidates:
            choices[key] = [
                DepictedChoice(
                    key=f"source:{unit['asset_id']}",
                    episode=key,
                    taken=unit["taken"],
                    content=description_of(unit),
                    primary=unit["asset_id"],
                )
                for unit in candidates
            ]
    return choices


def _near_home_of_moments(near_home, units: _MomentUnits, unit_by_asset):
    """Whether most of the pictures of some moments were taken near home; None when unknown."""

    def of(moments: Sequence[str]) -> bool | None:
        if near_home is None:
            return None
        votes = [near_home(unit_by_asset[u["asset_id"]][0]) for u in units.of(moments)]
        known = [vote for vote in votes if vote is not None]
        return 2 * sum(known) >= len(known) if known else None

    return of


def _photographed_days(event_units: Mapping[str, list[dict]]) -> int:
    return len({u["taken"][:10] for units in event_units.values() for u in units})


def _check_partition_request(partition_limit, partition_of) -> None:
    if partition_limit is not None and (
        type(partition_limit) is not int or partition_limit < 1 or partition_of is None
    ):
        raise ValueError(
            "A partition carrier limit needs a positive integer and a partition resolver"
        )


def _episodes_record(
    stories, story_units, choices_of, groups_offered, chosen_by_story
) -> list[dict]:
    return [
        {
            "episode": s["key"],
            "title": s["title"],
            "role": WEIGHT_ROLE[s["weight"]],
            "weight": s["weight"],
            "gate": s["gate"],
            "purpose": s.get("purpose") or "",
            "day": _first_day(story_units, s),
            "seen": s["seen"],
            "day_episodes": s["episodes"],
            "units": len(story_units[s["key"]]),
            # Available captioned candidates beside the story's capture-group count.
            "depicted_moments": len(choices_of[s["key"]]),
            "groups_offered": groups_offered[s["key"]],
            "granted": len(chosen_by_story[s["key"]]),
            "chosen": chosen_by_story[s["key"]],
        }
        | _kind_of_story(s)
        for s in stories
    ]


def _kind_of_story(s) -> dict[str, Any]:
    """A trip or a recurring thread says so on its row, with what it was allowed."""
    if s.get("trip"):
        return {"kind": "trip", "trip": s["trip"], "reserve": s.get("reserve", 0)}
    if s.get("thread"):
        return {"kind": "thread", "thread": s["thread"]}
    return {"kind": "story"}


def _kind_marker_of(unit_by_asset, lines) -> Callable[[DepictedChoice], str]:
    """What the moment's picture is (a long clip, a live photo) and who is in it by relation to the
    owner, so the choice between moments carries both."""

    def marker(c: DepictedChoice) -> str:
        unit = unit_by_asset.get(c.primary, (None, {}))[1]
        text = source_kind_marker(unit)
        relations: list[str] = []
        for a in c.members:
            for rel in relations_on(lines.get(a, "")):
                if rel not in relations:
                    relations.append(rel)
        if relations:
            text += " | with: " + ", ".join(relations[:4])
        return text

    return marker


def _read_the_period(
    judge,
    rules,
    *,
    evidence: list[dict],
    contract: str,
    record: Callable[[str, Mapping[str, Any]], None],
    enrich: Callable[[list], Mapping[str, Mapping[str, Any]]],
    stories_across_gaps: bool,
    journey: bool,
    trips: FilmTrips | None,
    moment_assets: Mapping[str, Sequence[str]],
    film_span: tuple[date, date] | None,
    lines: list[str],
    calls: dict[str, int],
    near_home: Callable[[Sequence[str]], bool | None],
) -> PeriodStory:
    """The weighed stories of the period, with its trips and recurring activities folded."""
    read_story = rules.read_story if rules is not None else read_period_story
    trips, fold = trip_fold(trips, moment_assets, rules=rules is not None)
    story = read_story(
        judge,
        evidence=evidence,
        contract=contract,
        prior={},
        record=lambda value: record("period-story", value),
        enrich=enrich,
        allow_gaps=stories_across_gaps,
        journey=journey,
        fold=fold,
    )
    record("trip-stories", trips.record())
    calls["thread_questions"] = fold_threads(
        judge,
        story,
        contract=contract,
        span=film_span,
        lines=lines,
        record=record,
        near_home=near_home,
        skip=thread_scope(rules=rules is not None, journey=journey, gapped=stories_across_gaps),
    )
    return story


def select_story_first(
    *,
    judge: Any,
    tables: Mapping[str, Any],
    aliases: Sequence[str],
    factual_rows_fn: Callable[[Mapping[str, Any], Sequence[str]], list[dict]],
    moment_assets: Mapping[str, Sequence[str]],
    lines: Mapping[str, str],
    contract: str,
    event_units: Mapping[str, list[dict]],
    family_of_moment: Mapping[str, str],
    anchor_label: Mapping[str, str],
    description_of: Callable[[dict], str],
    quality: Callable[[str], float],
    target_seconds: float,
    seconds_per_slot: float,
    record: Callable[[str, Mapping[str, Any]], None],
    flagged: Callable[[str], bool] = lambda _asset: False,
    subject: Callable[[str], SubjectVisibility] = lambda _asset: SubjectVisibility(0, 0.0),
    full_lines: Mapping[str, str] | None = None,
    life: Callable[[str], bool] = lambda _asset: True,
    family_tier: Mapping[str, int] | None = None,
    period_label: str = "",
    standing_bank: dict | None = None,
    standing_save: Callable[[], None] | None = None,
    excluded: Mapping[str, str] | None = None,
    allow_story_gaps: bool = False,
    journey: bool = False,
    partition_of: Callable[[str], str | None] | None = None,
    partition_limit: int | None = None,
    motion_line: Callable[[Mapping[str, Any]], str] | None = None,
    motion_identity: str = "",
    episode_readings: Mapping[str, Any] | None = None,
    rules=None,
    trips: FilmTrips | None = None,
    looks_alike: PairLooksAlike | None = None,
    film_span: tuple[date, date] | None = None,
    near_home: Callable[[str], bool | None] | None = None,
) -> StorySelection:
    """Read the period into weighed stories, fund them, and choose captioned pictures.

    `moment_assets` maps a moment alias to its selectable asset ids; `family_of_moment` maps a
    moment alias to the time-and-place family whose `event_units` hold the playable units.
    `family_tier` is the memory-worthy gate's reading per family (0 remarkable, 1 maybe, 2
    background); it is shown to the synthesis and weighs episodes the synthesis left unplaced.
    `record(name, payload)` persists a derived decision under the run's audit directory.
    `trips` are the journeys detected in the pool; each becomes one story before the weighing.
    `looks_alike(candidate, keeper)` refuses a story's further picture that repeats one it holds.
    `film_span` is the requested period; a recurring activity is one thread per era of it.
    `near_home(family)` says whether a happening was photographed near the home base.
    """
    calls = {
        "story_pages": 0,
        "story_pages_fresh": 0,
        "pick_calls": 0,
        "standing_rounds": 0,
    }
    _check_partition_request(partition_limit, partition_of)
    unit_by_asset = {u["asset_id"]: (f, u) for f, units in event_units.items() for u in units}
    parts = PartitionedSlots(unit_by_asset, partition_of=partition_of, limit=partition_limit)
    units = _MomentUnits(event_units, family_of_moment, dict(family_tier or {}))
    place_of_moment = {
        row.get("moment_id"): str(row.get("places") or "").strip()
        for row in factual_rows_fn(tables, aliases)
    }
    story_lines = full_lines or lines

    def line_of(asset: str) -> str:
        return story_lines.get(asset, "")

    # 1. The period story: day episodes from the banked 90-minute episode readings, a month per
    #    page, then the synthesis sees each episode with the numbers and the gate's reading and
    #    groups them into weighed stories.
    evidence = story_episode_rows(
        factual_rows_fn(tables, aliases),
        readings=episode_readings or {},
        sources=moment_assets,
        lines=lines,
        favourite=lambda asset: bool(unit_by_asset.get(asset, (None, {}))[1].get("favourite")),
    )
    story = _read_the_period(
        judge,
        rules,
        evidence=evidence,
        contract=contract,
        record=record,
        enrich=lambda episodes: _episode_hints(
            episodes,
            units=units,
            place_of_moment=place_of_moment,
            lines=story_lines,
            arrivals_of=_arrivals_from(tables),
        ),
        stories_across_gaps=allow_story_gaps,
        journey=journey,
        trips=trips,
        moment_assets=moment_assets,
        film_span=film_span,
        lines=[story_lines.get(asset, "") for asset in unit_by_asset],
        calls=calls,
        near_home=_near_home_of_moments(near_home, units, unit_by_asset),
    )
    calls["story_pages"] = len(story.audit.get("pages") or [])
    calls["story_pages_fresh"] = (story.audit.get("reading_calls") or {}).get("fresh", 0)

    # 2. Stories: their units over every day they span, in weight order.
    stories, story_units = _weighed_stories(story, story.audit.get("hints") or {}, units)

    # 3. Capture groups set capacity; captioned candidates preserve their available depth.
    picking: dict[str, Any] = {
        "quality": quality,
        "flagged": flagged,
        "life": life,
        "plays": carries_motion,
        "subject": subject,
    }
    choices_of = _capture_group_choices(stories, story_units, **picking)
    groups_offered = {s["key"]: len(choices_of[s["key"]]) for s in stories}
    slots = max(1, int(target_seconds // seconds_per_slot))
    reserve_trip_depth(stories, slots=slots, film_days=_photographed_days(event_units))

    def place_of(asset: str) -> str:
        moment = unit_by_asset.get(asset, (None, {}))[1].get("moment")
        return str(place_of_moment.get(moment) or "").split(";")[0].split(":", 1)[-1].strip()

    places = place_shares(stories, story_units, place_of=place_of, slots=slots, journey=journey)
    granted, partition_grants = parts.allocate(stories, choices_of, slots)

    # 4. Reuse prepared descriptions within the funded stories' existing shortlists.
    kind_of = _kind_marker_of(unit_by_asset, story_lines)
    if rules is None:
        allowed = _shortlisted_units(
            stories,
            granted,
            partition_grants,
            choices_of,
            parts,
            unit_by_asset,
            starred=lambda c: choice_is_starred(c, unit_by_asset),
            life=life,
            kind_of=kind_of,
        )
        record("story-candidate-scope", _candidate_scope(stories, granted, choices_of, allowed))
        choices_of.update(
            _caption_choices(
                stories,
                story_units,
                allowed=allowed,
                parts=parts,
                partition_grants=partition_grants,
                description_of=description_of,
            )
        )

    # 5. Pick the moments that tell each story, then one picture per moment that stands by
    #    itself. The audience gate judges the cut afterwards, not every candidate.
    gate = StandingGate(
        judge,
        contract=contract,
        period_label=period_label,
        line_of=line_of,
        life=life,
        unit_by_asset=unit_by_asset,
        pictures_of={s["key"]: s["seen"]["pictures"] for s in stories},
        score_of=rules.standing if rules is not None else None,
        bank=standing_bank,
        save=standing_save,
        calls=calls,
        motion_line=motion_line,
        motion_identity=motion_identity,
    )
    admission = CarrierAdmission(
        judge,
        stories=stories,
        choices_of=choices_of,
        unit_by_asset=unit_by_asset,
        anchor_label=anchor_label,
        parts=parts,
        gate=gate,
        line_of=line_of,
        life=life,
        # documents, screenshots, face close-ups, care items: evidence, never carriers
        excluded=dict(excluded or {}),
        kind_marker=kind_of,
        mechanical_picks=rules is not None,
        motion_line=motion_line,
        contract=contract,
        record=record,
        slots=slots,
        calls=calls,
        lookalike=LookAlikeCheck(looks_alike, slots=slots),
        places=places,
        place_of=place_of,
    )
    admission.run()
    record("story-places", places.record())

    selection = StorySelection(
        admission.carriers,
        story,
        _episodes_record(
            stories, story_units, choices_of, groups_offered, admission.chosen_by_story
        ),
        admission.alternatives_of,
        slots,
        calls,
        story_lines,
    )
    record(
        "story-selection",
        selection.record()
        | {
            "standing": gate.scores,
            "passes": admission.pass_records,
            "lookalike": admission.lookalike.record(),
            "failed_standing": admission.failed_standing,
            "kept_without_standing": admission.kept_without_standing,
            "editorially_closed": [
                {"story": s, "partition": p}
                for s, p in sorted(admission.editorially_closed, key=str)
            ],
            "context_rejected": [
                {"story": s, "asset_id": a} for s, a in sorted(gate.context_rejected)
            ],
        },
    )
    return selection


def alternatives_pool(
    selection: StorySelection, event_units: Mapping[str, list[dict]]
) -> Callable[[Mapping[str, Any]], list[dict]]:
    """For the audience gate: when a carrier is held, offer the same moment's other pictures."""
    unit_by_asset = {u["asset_id"]: u for units in event_units.values() for u in units}

    def pool_for(carrier: Mapping[str, Any]) -> list[dict]:
        return [
            unit_by_asset[a]
            | {
                "event": carrier.get("event"),
                "anchor": carrier.get("anchor"),
                "chapter": carrier.get("chapter"),
                "why": carrier.get("why"),
                # The page and the sheet print this under the thumbnail. A replacement
                # describes itself; it never borrows the refused picture's description.
                "line": selection.lines.get(a) or "Replaces a picture the audience gate refused",
            }
            for a in selection.alternatives_of.get(carrier["asset_id"], [])
            if a in unit_by_asset
        ]

    return pool_for


def story_plan_fields(selection: StorySelection) -> dict[str, Any]:
    """Keys the story-first branch adds to the plan dict."""
    return {"story": json.loads(json.dumps(selection.record(), ensure_ascii=False))}
