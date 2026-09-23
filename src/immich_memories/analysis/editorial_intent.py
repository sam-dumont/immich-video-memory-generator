"""Typed product obligations the editorial planner must satisfy before it selects anything.

The product brief explains a product in prose; this module states what can be checked: which
partitions of the scope must be represented, what the film must make visible, what supporting
texture is admissible, and when to abstain with ``insufficient_material`` instead of inflating
whatever is at hand. Prompts render the contract; validators enforce the checkable part.
"""

from __future__ import annotations

import calendar
import hashlib
from collections.abc import Collection, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta
from operator import itemgetter

from immich_memories.analysis.special_event_scope import SpecialEventAdmission
from immich_memories.timeperiod import DateRange

__all__ = ["EditorialIntent", "IntentPartition", "build_editorial_intent"]

ERA_THRESHOLD_DAYS = (
    548  # a person span longer than ~18 months is read as eras, one per calendar year
)


@dataclass(frozen=True)
class IntentPartition:
    """A stretch of the scope that must have a voice when it holds evidence."""

    key: str
    start: date
    end: date
    required: bool

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError("intent partition ends before it starts")

    @property
    def label(self) -> str:
        return (
            self.start.isoformat()
            if self.start == self.end
            else f"{self.start.isoformat()}..{self.end.isoformat()}"
        )

    def covers(self, when: date) -> bool:
        return self.start <= when <= self.end


@dataclass(frozen=True)
class EditorialIntent:
    product: str
    scope: str
    narrative_objective: str
    partitions: tuple[IntentPartition, ...]
    coverage_requirements: tuple[str, ...]
    selection_priorities: tuple[str, ...]
    allowed_texture: str
    abstention_policy: str
    subject: str | None = None
    max_carriers_per_partition: int | None = None

    def partition_for(self, when: date) -> IntentPartition | None:
        return next((part for part in self.partitions if part.covers(when)), None)

    @property
    def required_partitions(self) -> tuple[IntentPartition, ...]:
        return tuple(part for part in self.partitions if part.required)

    def prompt_block(self) -> str:
        """The contract as every semantic prompt sees it; the binding subject of a custom memory included."""
        lines = [
            f"PRODUCT CONTRACT ({self.product}):",
            f"scope: {self.scope}",
            f"objective: {self.narrative_objective}",
        ]
        if self.subject:
            lines.append(f"binding subject: {self.subject}")
        lines.extend(
            (
                "must cover: " + "; ".join(self.coverage_requirements),
                "priorities: " + "; ".join(self.selection_priorities),
                f"texture allowed: {self.allowed_texture}",
                f"abstain when: {self.abstention_policy}",
            )
        )
        if self.max_carriers_per_partition is not None:
            lines.append(
                f"selection limit: at most {self.max_carriers_per_partition} selected carrier per calendar year"
            )
        if len(self.partitions) > 1:
            lines.append(
                "partitions: "
                + "; ".join(
                    f"{p.label}{' (required)' if p.required else ''}" for p in self.partitions
                )
            )
        return "\n".join(lines)

    def identity(self) -> str:
        return hashlib.sha256(self.prompt_block().encode("utf-8")).hexdigest()

    def story_prompt_block(self) -> str:
        """The type part: what an episode is for this memory type, what is central, what more time adds."""
        return f"STORY PART ({self.product}): " + _STORY_PARTS.get(
            self.product, _STORY_PART_DEFAULT
        )

    def admission_prompt_block(self, ranges: Sequence[DateRange]) -> str:
        """Judge the requested occurrences before applying the final per-year cut limit."""
        if self.product != "on_this_day":
            return self.prompt_block()
        if not ranges:
            raise ValueError("on-this-day admission needs its captured occurrence ranges")
        spans = sorted(((r.start.date(), r.end.date()) for r in ranges), key=itemgetter(0))
        return replace(
            self,
            partitions=_per_range(spans, "occurrence", required=True),
            max_carriers_per_partition=None,
        ).prompt_block()


def build_editorial_intent(
    product: str,
    ranges: Sequence[DateRange],
    *,
    brief: str,
    people: Sequence[str] = (),
    event_admission: SpecialEventAdmission | None = None,
    material: Collection[date] | None = None,
) -> EditorialIntent:
    """Derive the contract from the product, its date ranges, and (for a custom memory) its brief.

    `material` is the days the film's own pictures were taken. A person film read as eras then
    names only the years that hold some: a window from a birth date holds many years with no
    picture of the person, and those are not parts of her film.
    """
    if not product.strip():
        raise ValueError("editorial intent needs a product")
    if not ranges:
        raise ValueError("editorial intent needs at least one date range")
    spans = sorted(((r.start.date(), r.end.date()) for r in ranges), key=itemgetter(0))
    whole = (spans[0][0], spans[-1][1])
    who = ", ".join(p for p in people if p.strip())
    if event_admission is not None and product != "special_day":
        raise ValueError("event admission requires the special_day product")
    builder = (
        _accepted_special_day if event_admission is not None else _BUILDERS.get(product, _generic)
    )
    intent = builder(product, spans, whole, brief=brief, who=who)
    if material is None or builder is not _person:
        return intent
    years = {day.year for day in material}
    kept = tuple(
        p for p in intent.partitions if not p.key.startswith("year-") or p.start.year in years
    )
    return replace(intent, partitions=kept) if kept else intent


def _per_range(spans, prefix: str, *, required: bool) -> tuple[IntentPartition, ...]:
    return tuple(
        IntentPartition(
            key=f"{prefix}-{i}-{s[0].isoformat()}", start=s[0], end=s[1], required=required
        )
        for i, s in enumerate(spans, start=1)
    )


def _per_year(whole, *, required: bool) -> tuple[IntentPartition, ...]:
    parts = []
    for year in range(whole[0].year, whole[1].year + 1):
        start = max(whole[0], date(year, 1, 1))
        end = min(whole[1], date(year, 12, 31))
        parts.append(IntentPartition(key=f"year-{year}", start=start, end=end, required=required))
    return tuple(parts)


def _single(whole) -> tuple[IntentPartition, ...]:
    return (IntentPartition(key="scope", start=whole[0], end=whole[1], required=True),)


def _per_month(whole) -> tuple[IntentPartition, ...]:
    parts = []
    start = whole[0]
    while start <= whole[1]:
        end = min(
            whole[1], date(start.year, start.month, calendar.monthrange(start.year, start.month)[1])
        )
        parts.append(
            IntentPartition(key=f"month-{start:%Y-%m}", start=start, end=end, required=True)
        )
        if end == whole[1]:
            break
        start = end + timedelta(days=1)
    return tuple(parts)


def _recurring(product, spans, whole, *, brief, who):
    day = "this holiday" if product == "holiday" else "this day of the year"
    partitions = _per_range(spans, "occurrence", required=True)
    if product == "on_this_day":
        partitions = tuple(
            part
            for part in _per_year(whole, required=True)
            if any(start <= part.end and end >= part.start for start, end in spans)
        )
    return EditorialIntent(
        product=product,
        scope=f"{day} across {len(spans)} occurrences, {whole[0].year}..{whole[1].year}",
        narrative_objective=f"how {day} recurred and changed: each represented occurrence gets a voice, early, middle and late",
        partitions=partitions,
        coverage_requirements=(
            "early, middle and late occurrences where evidence exists",
            "no cluster on the latest years",
            "recurrence or change is expressed, not implied",
        ),
        selection_priorities=(
            "the ritual and its participants",
            "what differs between occurrences",
            "the setting that marks the day",
        ),
        allowed_texture="the festive or ritual setting of the day itself",
        abstention_policy="fewer than two occurrences hold usable material: insufficient_material rather than a single-year film",
        max_carriers_per_partition=1 if product == "on_this_day" else None,
    )


def _person(product, spans, whole, *, brief, who):
    eras = (whole[1] - whole[0]).days > ERA_THRESHOLD_DAYS and len(spans) == 1
    partitions = (
        _per_year(whole, required=True)
        if eras
        else (_per_range(spans, "window", required=True) if len(spans) > 1 else _single(whole))
    )
    subject = who or None
    return EditorialIntent(
        product=product,
        scope=(f"{who}, " if who else "")
        + f"{whole[0].isoformat()}..{whole[1].isoformat()}"
        + (f" in {len(spans)} windows" if len(spans) > 1 else ""),
        narrative_objective="the person through change, relationships, places and occasions; portraits are punctuation, not the story",
        partitions=partitions,
        coverage_requirements=(("every era with material has a voice",) if eras else ())
        + (
            ("every window with material has a voice; window provenance survives",)
            if len(spans) > 1
            else ()
        )
        + ("relationships and activities, not only faces",),
        selection_priorities=(
            "occasions and firsts",
            "relationships and shared activity",
            "visible change over the span",
            "portraits last",
        ),
        allowed_texture="domestic life only when it shows a relationship or a change; presence alone is not merit",
        abstention_policy="a required era or window has no usable material: say so in the plan; do not fill it with another era",
        subject=subject,
    )


def _trip(product, spans, whole, *, brief, who):
    return EditorialIntent(
        product=product,
        scope=f"trip {whole[0].isoformat()}..{whole[1].isoformat()}",
        narrative_objective="the trip as a journey: route and progression, the place's fabric, lived activity, atmosphere and the group in it",
        partitions=_single(whole),
        coverage_requirements=(
            "a beginning and an end",
            "progression through the days, not one day taking the film",
            "place and activity, not only faces",
        ),
        selection_priorities=(
            "legs and places in order",
            "lived activity over posed portraits",
            "the light and food of somewhere else as fabric",
        ),
        allowed_texture="landscapes, streets, food and beach life are the trip's fabric and earn their place",
        abstention_policy="never; a short trip makes a short film",
    )


def _special_day(product, spans, whole, *, brief, who):
    return EditorialIntent(
        product=product,
        scope=f"one day, {whole[0].isoformat()}",
        narrative_objective="one out-of-the-ordinary event, visibly established; ordinary camp days, family life, cake bursts and normal live concerts do not qualify",
        partitions=_single(whole),
        coverage_requirements=(
            "the defining occasion is visible",
            "its participants and setting are shown",
        ),
        selection_priorities=("the occasion itself", "reactions and participants", "the setting"),
        allowed_texture="none that does not belong to the occasion",
        abstention_policy="no qualifying exceptional event: insufficient_material; a coherent ordinary occasion is not enough; never inflate a mundane frame into a memory",
    )


def _accepted_special_day(product, spans, whole, *, brief, who):
    return EditorialIntent(
        product=product,
        scope=f"one selected event, {whole[0].isoformat()}",
        narrative_objective="edit the already admitted event from its visible action, participants and setting; its admission is settled, not every picture's contribution",
        partitions=_single(whole),
        coverage_requirements=(
            "the selected occasion is visible",
            "its participants and setting are shown where evidence exists",
        ),
        selection_priorities=("the occasion itself", "reactions and participants", "the setting"),
        allowed_texture="only material that helps show the selected occasion",
        abstention_policy="the selected event has no usable contributing material: insufficient_material; sparse material makes a short memory, never unrelated padding",
    )


def _custom(product, spans, whole, *, brief, who):
    return EditorialIntent(
        product=product,
        scope=f"{whole[0].isoformat()}..{whole[1].isoformat()}"
        + (f" in {len(spans)} ranges" if len(spans) > 1 else ""),
        narrative_objective="exactly what was asked for, chronologically, from beginning to result",
        # owner ruling 2026-09-05: a multi-range custom memory (works periods across years) must give every
        # year of its span a voice; one year of a ten-year renovation is not the memory that was asked for
        partitions=_per_year(whole, required=True) if len(spans) > 1 else _single(whole),
        coverage_requirements=(
            "every funded beat visibly concerns the requested subject",
            "beginning and result state where the ask implies progression",
        )
        + (("every year of the span that holds material has a voice",) if len(spans) > 1 else ()),
        selection_priorities=(
            "material that shows the subject",
            "stages of progression",
            "people only when the subject is visible with them",
        ),
        allowed_texture="only what concerns the subject",
        abstention_policy="the subject is not visible in the material: insufficient_material, not a film about something else",
        subject=brief.strip(),
    )


def _generic(product, spans, whole, *, brief, who):
    period = {
        "year_in_review": "the year's arc through its distinctive occasions, firsts and changes",
        "season": "the season's own character across its trips and home life",
        "monthly_highlights": "the month as it was lived, its specific happenings",
        "album": "the album's own story in order",
    }.get(product, "the period's remarkable happenings in order")
    return EditorialIntent(
        product=product,
        scope=f"{whole[0].isoformat()}..{whole[1].isoformat()}",
        narrative_objective=period,
        partitions=_per_month(whole) if product == "year_in_review" else _single(whole),
        coverage_requirements=(
            "remarkable happenings before anything ordinary",
            "the span's special days are present when they exist",
        )
        + (
            (
                "months with worthy material each have a voice; a dense period must not erase the rest of the year",
            )
            if product == "year_in_review"
            else ()
        ),
        selection_priorities=(
            "occasions, firsts and changes",
            "activity over presence",
            "background last and only in support of a funded story",
        ),
        allowed_texture="daily life supports the arc; it never floods it",
        abstention_policy="a quiet period makes a short film; never fill to the target",
    )


# The story part of the prompt: one paragraph per memory type, generic, shared by the period reader
# and the selection. It says what an episode is for this type, what counts as central, and what
# more duration adds. No person, date, place or object is ever named here.
_STORY_PARTS = {
    "monthly_highlights": (
        "An episode is one occasion of the month: an outing, a visit, a celebration, a milestone, a trip day, a project's visible step. A trip or a stay of several days inside the month is one story and usually what the month is about. Central: the occasions this month would be remembered by; a routine at home (a meal, a drink, a selfie, a chore) is texture unless the owner starred it. More time adds the next occasion, then a second distinct moment of a central one; never a second angle of the same moment."
    ),
    "year_in_review": (
        "An episode is one occasion of a day. Central: the year's consequential changes and its occasions "
        "(trips, celebrations, milestones, firsts); no month is owed equal time. More time adds occasions not yet "
        "shown across the year, then depth on the central ones."
    ),
    "season": (
        "An episode is one occasion of a day. Central: the season's outings, trips and gatherings; home routine is "
        "texture. More time adds more outings, then their distinct moments."
    ),
    "trip": (
        "An episode is one place or one activity of the journey, in order. Central: every day's distinct place "
        "and activity; transit and hotel details are texture. More time adds the next place or activity, never "
        "another angle of one already shown."
    ),
    "album": (
        "An episode is one step of the album's own story. Central: the steps that change something; repeated "
        "views of one step are texture. More time adds the next step."
    ),
    "person_spotlight": (
        "An episode is one occasion where the person appears. Central: the person's changes and milestones "
        "across the range that the facts support, spread over time; the same pose in another room is texture. "
        "More time adds earlier and later occasions before depth."
    ),
    "multi_person": (
        "An episode is one occasion where the requested people appear together. Central: their shared occasions "
        "across the range, spread over time. More time adds more shared occasions before depth."
    ),
    "on_this_day": (
        "An episode is one year's occurrence of this day. Central: each year's one distinct moment; a year with "
        "only background is left out rather than filled. More time adds another year, never a second picture "
        "of one year."
    ),
    "holiday": (
        "An episode is one year's celebration of this holiday. Central: the celebration itself when it is "
        "visible; a year without it is left out rather than filled with the days around it. More time adds "
        "another year."
    ),
    "special_day": (
        "An episode is one stage of the day's occasion. Central: the occasion itself; if the day holds one "
        "occasion, its distinct moments are the depth. More time adds the next stage of the same occasion."
    ),
    "custom": (
        "An episode is one occasion that concerns the requested subject. The subject's stages across the whole span are one story even with weeks between them. Central: the subject's visible stages and changes; anything not about the subject is texture even when attractive. More time adds the next stage of the subject."
    ),
}
_STORY_PART_DEFAULT = (
    "An episode is one occasion of a day. Central: the occasions the period would be remembered by; home "
    "routine is texture unless the owner starred it. More time adds the next occasion, then a second distinct "
    "moment of a central one; never another angle of the same moment."
)


_BUILDERS = {
    "on_this_day": _recurring,
    "holiday": _recurring,
    "person_spotlight": _person,
    "multi_person": _person,
    "trip": _trip,
    "special_day": _special_day,
    "custom": _custom,
}
