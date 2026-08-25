"""Building the people graph out of what Immich already knows.

Counts, names, birth dates, month curves and pairwise co-occurrence — no
pixels, no model, and no question asked of the user. The library's own
distribution says who matters; this reads it and writes down what it read,
along with the evidence, so a person can disagree with it later.
"""

from __future__ import annotations

import itertools
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol

from immich_memories.people.signatures import (
    EXIF_NOISE_FLOOR,
    Link,
    LinkKind,
    PersonEvidence,
    Tier,
    as_one_unit,
    classify,
    duplicate_links,
    owner_dyad_link,
    pair_key,
    tight_dyad_links,
    twin_links,
)

logger = logging.getLogger(__name__)

# Below this a person is a face Immich happened to cluster, not somebody the
# library has an opinion about — and every pair costs a query, so the roster
# bound is what keeps the pairwise pass to seconds rather than minutes.
DEFAULT_MIN_ASSETS = 25


class PeopleSource(Protocol):
    """The slice of the Immich client the graph needs."""

    def get_all_people(self, with_hidden: bool = False) -> Sequence[Any]: ...

    def get_time_buckets(self, **kwargs: Any) -> Sequence[Any]: ...

    def count_assets_with_people(self, person_ids: Sequence[str]) -> int: ...

    def get_current_user(self) -> Any: ...


@dataclass(frozen=True)
class Owner:
    """The person record belonging to whoever owns this library."""

    person_id: str | None
    name: str
    identified: str


@dataclass(frozen=True)
class PersonNode:
    """One person, as evidence plus what the evidence was read to mean."""

    evidence: PersonEvidence
    tier: Tier
    links: tuple[Link, ...] = ()
    counts_reliable: bool = True


@dataclass(frozen=True)
class PeopleGraph:
    """Everyone the library has an opinion about, and how they connect."""

    people: tuple[PersonNode, ...]
    owner: Owner | None = None
    built_at: datetime | None = None


def build_graph(
    source: PeopleSource,
    *,
    min_assets: int = DEFAULT_MIN_ASSETS,
    owner_name: str | None = None,
    today: date | None = None,
) -> PeopleGraph:
    """Read the whole roster, then read what its numbers mean.

    One timeline call per named person gives their count and their months
    together; the pairwise pass then costs one small query per pair, which is
    why the roster is bounded to people the library actually holds.
    """
    evidence = _roster(source, min_assets=min_assets)
    owner = identify_owner(source, evidence, owner_name=owner_name)
    links = _all_links(source, evidence, owner)
    return PeopleGraph(
        people=tuple(_nodes(evidence, links, today=today)),
        owner=owner,
        built_at=datetime.now(),
    )


def identify_owner(
    source: PeopleSource,
    evidence: Sequence[PersonEvidence],
    *,
    owner_name: str | None = None,
) -> Owner | None:
    """Which person record is the person whose library this is.

    Three ways down from certain: told, matched against the Immich account's
    own name, or — last — inferred from who has been here longest and appears
    most. The inference is recorded as an inference, because the whole
    photographer correction hangs off getting this right.
    """
    if owner_name:
        matched = _by_name(evidence, owner_name)
        return Owner(matched.person_id if matched else None, owner_name, "told")

    account = _account_name(source)
    if account:
        matched = _by_name(evidence, account)
        if matched:
            return Owner(matched.person_id, matched.name, "account")

    if not evidence:
        return None
    longest = max(evidence, key=lambda person: (person.span_years, person.count))
    return Owner(longest.person_id, longest.name, "inferred")


def _nodes(
    evidence: Sequence[PersonEvidence],
    links: Sequence[Link],
    *,
    today: date | None,
) -> list[PersonNode]:
    by_id = {person.person_id: person for person in evidence}
    grouped: dict[str, list[Link]] = {}
    for link in links:
        grouped.setdefault(link.source_id, []).append(link)

    nodes = []
    for person in evidence:
        mine = grouped.get(person.person_id, [])
        twin = _twin_of(person, mine, by_id)
        read = as_one_unit(person, twin) if twin else person
        nodes.append(
            PersonNode(
                evidence=person,
                tier=classify(read, today=today),
                links=tuple(mine),
                counts_reliable=twin is None,
            )
        )
    return nodes


def _twin_of(
    person: PersonEvidence, links: Sequence[Link], by_id: dict[str, PersonEvidence]
) -> PersonEvidence | None:
    for link in links:
        if link.kind is LinkKind.TWIN and link.target_id in by_id:
            return by_id[link.target_id]
    return None


def _all_links(
    source: PeopleSource, evidence: Sequence[PersonEvidence], owner: Owner | None
) -> list[Link]:
    owner_id = owner.person_id if owner else None
    links = twin_links(evidence) + duplicate_links(evidence)
    links += tight_dyad_links(
        evidence, _shared_counts(source, evidence, owner_id), owner_id=owner_id
    )

    owner_person = next((p for p in evidence if p.person_id == owner_id), None)
    if owner_person is not None:
        partner = owner_dyad_link(owner_person, [p for p in evidence if p is not owner_person])
        if partner is not None:
            links.extend(
                (
                    partner,
                    Link(
                        partner.kind,
                        partner.target_id,
                        partner.source_id,
                        partner.confidence,
                        partner.via,
                    ),
                )
            )
    return links


def _shared_counts(
    source: PeopleSource, evidence: Sequence[PersonEvidence], owner_id: str | None
) -> dict[tuple[str, str], int]:
    """How many assets hold both of each pair, one small query per pair.

    Pairs containing the owner are not asked about at all: the owner is behind
    the camera, so the answer would be a floor rather than a measurement, and
    it would still cost a query.
    """
    counts: dict[tuple[str, str], int] = {}
    for one, other in itertools.combinations(evidence, 2):
        if owner_id in (one.person_id, other.person_id):
            continue
        try:
            counts[pair_key(one.person_id, other.person_id)] = source.count_assets_with_people(
                [one.person_id, other.person_id]
            )
        except Exception as exc:  # noqa: BLE001, PERF203 - one bad pair is not the scan
            logger.warning("Co-occurrence for one pair failed: %s", type(exc).__name__)
    return counts


def _roster(source: PeopleSource, *, min_assets: int) -> list[PersonEvidence]:
    """Every named person the library holds more than a handful of pictures of.

    Unnamed people are left out on purpose: the graph cannot say anything
    useful about a face nobody has claimed, and naming them is work that
    belongs in Immich.
    """
    roster: list[PersonEvidence] = []
    for person in source.get_all_people():
        if not person.name.strip():
            continue
        months = _active_months(source, person.id)
        count = sum(months.values())
        if count < min_assets:
            continue
        roster.append(
            PersonEvidence(
                person_id=person.id,
                name=person.name,
                count=count,
                active_months=tuple(sorted(months)),
                birth_date=_as_date(person.birth_date),
            )
        )
    return roster


def _active_months(source: PeopleSource, person_id: str) -> dict[date, int]:
    """The months this person appears in, and how much of each.

    The timeline endpoint answers in months whatever size is asked of it, so
    month is the grain the whole graph is built on — enough to separate a
    relationship from a burst, and one call instead of paging every asset.
    """
    months: dict[date, int] = {}
    for bucket in source.get_time_buckets(size="MONTH", person_id=person_id):
        month = _as_month(bucket.time_bucket)
        if month is not None and month >= EXIF_NOISE_FLOOR:
            months[month] = months.get(month, 0) + bucket.count
    return months


def _by_name(evidence: Sequence[PersonEvidence], name: str) -> PersonEvidence | None:
    wanted = " ".join(name.casefold().split())
    return next((p for p in evidence if " ".join(p.name.casefold().split()) == wanted), None)


def _account_name(source: PeopleSource) -> str:
    try:
        return str(source.get_current_user().name or "")
    except Exception as exc:  # noqa: BLE001 - an unreachable account is not fatal
        logger.warning("Could not read the Immich account name: %s", type(exc).__name__)
        return ""


def _as_month(raw: object) -> date | None:
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date().replace(day=1)
    except ValueError:
        logger.warning("Ignoring an unreadable timeline bucket: %r", raw)
        return None


def _as_date(raw: object) -> date | None:
    if isinstance(raw, datetime):
        return raw.date()
    return raw if isinstance(raw, date) else None
