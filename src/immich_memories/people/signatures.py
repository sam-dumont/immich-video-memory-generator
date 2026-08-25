"""Reading a person's shape off their numbers alone.

No pixels are involved. A person is a count, a set of months they appear in,
and possibly a birth date — and that is enough to separate the household from
the people met once at a race, because volume is a burst and continuity is a
relationship. Every threshold here was measured on a real library and banked
on #745; the comments say which observation each one comes from.
"""

from __future__ import annotations

import itertools
import operator
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from enum import StrEnum

# WHY: cameras with a dead clock stamp 1970, and scanned negatives get whatever
# the scanner felt like. Nothing before this is an appearance, it is metadata
# damage, and left in it drags every span and onset back two decades.
EXIF_NOISE_FLOOR = date(2003, 1, 1)

# An arrival is a month with three more inside the year that follows it. One
# month alone is a visit; the measured case was a person photographed once in
# 2011 and then properly present from 2018 — the onset is 2018.
_SUSTAINING_MONTHS = 3

# Measured: an event companion ran ~160 pictures across 3-4 active months over
# scattered years, while family ran 5-15 pictures per active month across
# dozens of months. Few months + heavy per-month volume is a burst.
_EVENT_MAX_ACTIVE_MONTHS = 4
_EVENT_MIN_CONCENTRATION = 20.0

# "Dozens of months across many years" — the household and both extended
# families cleared this on the real roster; nobody episodic came close.
_INNER_MIN_ACTIVE_MONTHS = 24
_INNER_MIN_SPAN_YEARS = 3.0
# Continuity keeps a long thin thread out of the inner circle: a childhood
# friend spanning fifteen years at three months a year is recurring, not daily.
_INNER_MIN_CONTINUITY = 0.35

_RECURRING_MIN_ACTIVE_MONTHS = 12

# A child of the house is present in most months since they were born. A
# friend's child of the same age is present a handful of times a year, and
# without this floor the age-equals-span rule would promote both.
_HOUSEHOLD_CHILD_MIN_CONTINUITY = 0.5

# A couple shares roughly half of each other's pictures; a quarter is the
# floor that admitted every real pair on the measured roster and no stranger.
_DYAD_MIN_MUTUAL_SHARE = 0.25

# The owner's partner appears at the owner's own order of magnitude. A quarter
# of the busiest person's count keeps the household in and the visitors out.
_OWNER_SCALE_SHARE = 0.25
_OWNER_MIN_CURVE_PAIRING = 0.5

_DAYS_PER_YEAR = 365.25


class Tier(StrEnum):
    """How a person sits in the library, by spread and concentration."""

    INNER = "inner"
    RECURRING = "recurring"
    EPISODIC = "episodic"
    EVENT = "event"


@dataclass(frozen=True)
class PersonEvidence:
    """What the library knows about one person, before any interpretation."""

    person_id: str
    name: str
    count: int
    active_months: tuple[date, ...]
    birth_date: date | None = None

    @property
    def month_count(self) -> int:
        return len(self.active_months)

    @property
    def first_month(self) -> date | None:
        return self.active_months[0] if self.active_months else None

    @property
    def last_month(self) -> date | None:
        return self.active_months[-1] if self.active_months else None

    @property
    def span_years(self) -> float:
        """Years between the first and last month this person appears in."""
        first, last = self.first_month, self.last_month
        if first is None or last is None:
            return 0.0
        return (last - first).days / _DAYS_PER_YEAR

    @property
    def concentration(self) -> float:
        """Pictures per active month — the discriminator volume alone is not."""
        return self.count / self.month_count if self.month_count else 0.0

    @property
    def continuity(self) -> float:
        """Share of the months between arrival and last sighting they appear in."""
        elapsed = _months_between(self.first_month, self.last_month)
        return self.month_count / elapsed if elapsed else 0.0

    @property
    def onset(self) -> date | None:
        """The month this person entered the library for good."""
        return first_sustained_month(self.active_months)


def first_sustained_month(
    active_months: Iterable[date], *, floor: date = EXIF_NOISE_FLOOR
) -> date | None:
    """The first month with three more active months inside the following year.

    Returns None for someone who never stayed — a handful of scattered months
    is a person who passed through, and dating their arrival would be fiction.
    """
    months = sorted(m for m in active_months if m >= floor)
    for index, month in enumerate(months):
        horizon = _plus_a_year(month)
        following = sum(1 for later in months[index + 1 :] if later <= horizon)
        if following >= _SUSTAINING_MONTHS:
            return month
    return None


def classify(evidence: PersonEvidence, *, today: date | None = None) -> Tier:
    """Which tier a person's spread and concentration put them in.

    Order matters: a burst is a burst whatever else is true of it, and only
    then does a birth date get to override spread for a child of the house.
    """
    if _is_burst(evidence):
        return Tier.EVENT
    if is_household_child(evidence, today=today):
        return Tier.INNER
    if (
        evidence.month_count >= _INNER_MIN_ACTIVE_MONTHS
        and evidence.span_years >= _INNER_MIN_SPAN_YEARS
        and evidence.continuity >= _INNER_MIN_CONTINUITY
    ):
        return Tier.INNER
    if evidence.month_count >= _RECURRING_MIN_ACTIVE_MONTHS:
        return Tier.RECURRING
    return Tier.EPISODIC


def is_household_child(evidence: PersonEvidence, *, today: date | None = None) -> bool:
    """A person whose whole life is in this library, and densely so.

    Someone born after the library started has a span that cannot exceed their
    age, so span ≈ age means they have been here since day one. That alone
    catches every friend's child too, hence the continuity floor.
    """
    if evidence.birth_date is None or evidence.first_month is None:
        return False
    age = ((today or date.today()) - evidence.birth_date).days / _DAYS_PER_YEAR
    if age <= 0:
        return False
    lived_here = (evidence.first_month - evidence.birth_date).days / _DAYS_PER_YEAR
    born_into_the_library = lived_here <= 1.0
    return born_into_the_library and evidence.continuity >= _HOUSEHOLD_CHILD_MIN_CONTINUITY


def _is_burst(evidence: PersonEvidence) -> bool:
    return (
        evidence.month_count <= _EVENT_MAX_ACTIVE_MONTHS
        and evidence.concentration >= _EVENT_MIN_CONCENTRATION
    )


def _plus_a_year(month: date) -> date:
    return month.replace(year=month.year + 1)


def _months_between(first: date | None, last: date | None) -> int:
    """Calendar months from first to last inclusive."""
    if first is None or last is None:
        return 0
    return (last.year - first.year) * 12 + (last.month - first.month) + 1


class LinkKind(StrEnum):
    """What one person's numbers say about their relation to another's."""

    TIGHT_DYAD = "tight-dyad"
    TWIN = "twin"
    DUPLICATE = "duplicate"


@dataclass(frozen=True)
class Link:
    """An inferred edge between two people, with what suggested it."""

    kind: LinkKind
    source_id: str
    target_id: str
    confidence: float
    via: str


def twin_links(people: Sequence[PersonEvidence]) -> list[Link]:
    """Same family name, same birth date — two records for one pair of twins.

    Worth detecting because face recognition merges identical faces: measured
    on a real library, one twin's record held 576 assets and the other 20, all
    of them recently hand-tagged. Neither count means anything on its own.
    """
    found: list[Link] = []
    for one, other in itertools.combinations(people, 2):
        if one.birth_date is None or one.birth_date != other.birth_date:
            continue
        if not _family_name(one.name) or _family_name(one.name) != _family_name(other.name):
            continue
        found.extend(_both_ways(LinkKind.TWIN, one, other, confidence=0.9, via="birth-date"))
    return found


def duplicate_links(people: Sequence[PersonEvidence]) -> list[Link]:
    """One name on two person records — a split face cluster, to merge in Immich.

    Not a relationship: a prompt. The graph reports it so the person editor can
    send the user back to Immich, which is the only place it can be fixed.
    """
    found: list[Link] = []
    for one, other in itertools.combinations(people, 2):
        if one.name.strip() and _same_name(one.name, other.name):
            found.extend(_both_ways(LinkKind.DUPLICATE, one, other, confidence=0.6, via="name"))
    return found


def as_one_unit(person: PersonEvidence, twin: PersonEvidence) -> PersonEvidence:
    """One twin's evidence recomputed from the pair, keeping their identity.

    Their split is an artefact of face recognition, not of how often either of
    them was actually there, so significance has to read the pair.
    """
    return replace(
        person,
        count=person.count + twin.count,
        active_months=tuple(sorted(set(person.active_months) | set(twin.active_months))),
    )


def _both_ways(
    kind: LinkKind, one: PersonEvidence, other: PersonEvidence, *, confidence: float, via: str
) -> list[Link]:
    return [
        Link(kind, one.person_id, other.person_id, confidence, via),
        Link(kind, other.person_id, one.person_id, confidence, via),
    ]


def _same_name(one: str, other: str) -> bool:
    return " ".join(one.casefold().split()) == " ".join(other.casefold().split())


def _family_name(name: str) -> str:
    parts = name.casefold().split()
    return parts[-1] if len(parts) > 1 else ""


def pair_key(one_id: str, other_id: str) -> tuple[str, str]:
    """The order-free key a pair of people is counted under."""
    return (one_id, other_id) if one_id <= other_id else (other_id, one_id)


def tight_dyad_links(
    people: Sequence[PersonEvidence],
    shared: Mapping[tuple[str, str], int],
    *,
    owner_id: str | None = None,
) -> list[Link]:
    """Pairs who are a large share of each other's pictures, both ways.

    Mutual is the whole point. Everyone in a household appears in the busiest
    person's frames, so a one-sided share says only that the other person is
    busy. A couple sits at a quarter or more of each other's library.

    Deliberately called a tight dyad and not a couple: the same shape comes out
    of a parent and a small child, and telling those apart needs generation
    cues this pass does not have.
    """
    found: list[Link] = []
    for one, other in itertools.combinations(people, 2):
        if owner_id in (one.person_id, other.person_id):
            continue
        together = shared.get(pair_key(one.person_id, other.person_id), 0)
        if not together:
            continue
        mutual = min(_share(together, one.count), _share(together, other.count))
        if mutual >= _DYAD_MIN_MUTUAL_SHARE:
            found.extend(
                _both_ways(LinkKind.TIGHT_DYAD, one, other, confidence=mutual, via="co-occurrence")
            )
    return found


def owner_dyad_link(owner: PersonEvidence, others: Sequence[PersonEvidence]) -> Link | None:
    """The owner's closest person, read from curves because frames will not say.

    Co-occurrence undercounts every pair containing the library's owner, since
    the owner is behind the camera. Measured: in the quarter the owner met
    their partner, the partner appears twenty-five times and they share zero
    frames — the first shared frame is months later, when somebody else shoots
    them. So for the owner the signal is presence at the owner's own scale
    plus a month curve that tracks theirs from the day they arrive.
    """
    if not others:
        return None
    at_scale = max(person.count for person in others) * _OWNER_SCALE_SHARE
    ranked = [
        (_curve_pairing(owner, person), person)
        for person in others
        if person.person_id != owner.person_id and person.count >= at_scale
    ]
    if not ranked:
        return None
    pairing, closest = max(ranked, key=operator.itemgetter(0))
    if pairing < _OWNER_MIN_CURVE_PAIRING:
        return None
    return Link(LinkKind.TIGHT_DYAD, owner.person_id, closest.person_id, pairing, "curve-pairing")


def _curve_pairing(owner: PersonEvidence, other: PersonEvidence) -> float:
    """How much of the owner's months since the other arrived they share.

    Measured from the other person's first month rather than the owner's,
    because a partner who arrived in the library's tenth year would otherwise
    be punished for the decade before they existed to it.
    """
    since = other.first_month
    if since is None:
        return 0.0
    mine = {month for month in owner.active_months if month >= since}
    theirs = set(other.active_months)
    union = mine | theirs
    return len(mine & theirs) / len(union) if union else 0.0


def _share(together: int, of: int) -> float:
    return together / of if of else 0.0
