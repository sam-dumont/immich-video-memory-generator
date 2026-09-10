"""Partition coverage and duration allocation for the production structure editor."""

from __future__ import annotations

import re

CONTENT_RESERVE_SECONDS = 7.5
MIN_CARRIER_SECONDS = 3.5
NOMINAL_STILL_SECONDS = 4.0


def required_voice_slot_budget(
    target_seconds, required_voices, *, nominal_seconds=NOMINAL_STILL_SECONDS
):
    """Budget the content at its actual pace, reserving physically playable required voices."""
    if nominal_seconds <= 0:
        raise ValueError("nominal carrier duration must be positive")
    content_seconds = max(0, target_seconds - CONTENT_RESERVE_SECONDS)
    nominal = int(content_seconds // nominal_seconds)
    playable = int(content_seconds // MIN_CARRIER_SECONDS)
    return min(playable, max(nominal, required_voices))


def _spread_scarce_voices(live: list[str], slots_total: int, budget: dict[str, int]) -> None:
    """Partitions arrive in chronology. A tied remainder must not erase the latest
    occurrence; spread the scarce voices across the whole available timeline."""
    if slots_total == 1:
        budget[live[(len(live) - 1) // 2]] = 1
        return
    span = slots_total - 1
    for i in range(slots_total):
        budget[live[(i * (len(live) - 1) + span // 2) // span]] = 1


def _fill_headroom(order, budget, capacity, left: int) -> int:
    """Hand the unusable slots to the partitions that still have room, in the given order."""
    for p in order:
        while left > 0 and budget[p] < capacity[p]:
            budget[p] += 1
            left -= 1
    return left


def partition_budgets(
    partitions: list[str],
    capacity: dict[str, int],
    slots_total: int,
    *,
    max_per_partition: int | None = None,
) -> dict[str, int]:
    """D14: an equal voice for every required partition that holds worthy evidence, capped by what it can
    carry; slots a partition cannot use go to the partitions with headroom, largest capacity first."""
    if max_per_partition is not None:
        if type(max_per_partition) is not int or max_per_partition <= 0:
            raise ValueError("partition carrier limit must be a positive integer")
        capacity = {p: min(n, max_per_partition) for p, n in capacity.items()}
    live = [p for p in partitions if capacity.get(p, 0) > 0]
    budget = dict.fromkeys(partitions, 0)
    if not live or slots_total <= 0:
        return budget
    if slots_total < len(live):
        _spread_scarce_voices(live, slots_total, budget)
        return budget
    for p, b in zip(
        live, largest_remainder([100.0 / len(live)] * len(live), slots_total), strict=False
    ):
        budget[p] = min(b, capacity[p])
    _fill_headroom(
        sorted(live, key=lambda x: -capacity[x]),
        budget,
        capacity,
        slots_total - sum(budget.values()),
    )
    return budget


def cell_budgets(
    shares: list[float],
    cell_capacity: list[dict[str, int]],
    partition_budget: dict[str, int],
    voice: list[dict[str, int]] | None = None,
) -> list[dict[str, int]]:
    """D14: inside each partition, its slots follow the weighing's shares across the beats that have material
    there, capped per cell; slots left over spill inside the same partition by share order. Never across.
    D14d: a cell flagged as a voice (a story beat holding a remarkable happening of that partition) is reserved one
    slot before the proportional split, so no beat deepens while a remarkable happening has no picture."""
    out: list[dict[str, int]] = [{} for _ in shares]
    for p, budget in partition_budget.items():
        got = _cells_of_partition(p, shares, cell_capacity, budget, voice)
        for i in range(len(shares)):
            out[i][p] = got[i]
    return out


def _cells_of_partition(
    p: str,
    shares: list[float],
    cell_capacity: list[dict[str, int]],
    budget: int,
    voice: list[dict[str, int]] | None,
) -> dict[int, int]:
    idx = [i for i, cc in enumerate(cell_capacity) if cc.get(p, 0) > 0]
    got = dict.fromkeys(range(len(shares)), 0)
    if not idx or budget <= 0:
        return got
    for i in [i for i in idx if voice and voice[i].get(p, 0) > 0][:budget]:
        got[i] = 1
    total = sum(shares[i] for i in idx) or 1.0
    for i, b in zip(
        idx,
        largest_remainder([100.0 * shares[i] / total for i in idx], budget - sum(got.values())),
        strict=False,
    ):
        got[i] = min(got[i] + b, cell_capacity[i][p])
    _fill_headroom(
        sorted(idx, key=lambda i: -shares[i]),
        got,
        {i: cell_capacity[i][p] for i in idx},
        budget - sum(got.values()),
    )
    return got


def partition_cells(
    chapter_families: list[list], part_key: dict, units_of: dict, cap: int, partitions: list[str]
) -> list[dict[str, int]]:
    """D14b: how many slots each (beat, partition) cell can carry, counting EVERY beat's families: a required
    partition whose only worthy material sits in an unfunded or reservoir beat still gets its voice."""
    return [
        {
            p: sum(min(cap, units_of.get(f, 0)) for f in fams if part_key.get(f) == p)
            for p in partitions
        }
        for fams in chapter_families
    ]


def respread(
    freed: int, shares: list[float], budgets: list[int], capacities: list[int]
) -> list[int]:
    """D15f: slots freed by a structural removal go back to the remaining funded beats in proportion to their weighed
    shares, capped by capacity; what a beat cannot absorb spills by share. Never all into one beat."""
    out = budgets.copy()
    if freed <= 0 or not out:
        return out
    weights = shares if sum(shares) > 0 else [1.0] * len(shares)
    for i, extra in enumerate(largest_remainder(weights, freed)):
        add = min(extra, max(0, capacities[i] - out[i]))
        out[i] += add
        freed -= add
    _fill_headroom(
        sorted(range(len(out)), key=lambda i: -shares[i]),
        out,
        dict(enumerate(capacities)),
        freed,
    )
    return out


def partition_has_room(partition, carriers: list, part_key: dict, budgets: dict) -> bool:
    """D14c: under partition-first allocation, refill may add to a partition only below its budget."""
    if partition is None or partition not in budgets:
        return True
    return sum(1 for c in carriers if part_key.get(c["event"]) == partition) < budgets[partition]


def voice_slots(
    chapters: list, *, tier: dict, unused: int, reservoir: set, units_of: dict, cap: int
) -> int:
    """D14d: the contract's order across beats. An unfunded story beat (not a reservoir) that holds a remarkable
    happening gets ONE slot before any funded beat deepens; most remarkable happenings first. Returns the slots left."""
    candidates = [
        c
        for c in chapters
        if c.get("budget", 0) == 0
        and c.get("beat") not in reservoir
        and any(tier.get(f, 2) == 0 for f in c.get("families", []))
    ]
    for c in sorted(
        candidates, key=lambda x: -sum(1 for f in x["families"] if tier.get(f, 2) == 0)
    ):
        if unused <= 0:
            break
        c["capacity"] = sum(min(cap, units_of.get(f, 0)) for f in c["families"])
        if c["capacity"] <= 0:
            continue
        c["budget"] = 1
        c["voice_slot"] = True
        c.pop("unfunded", None)
        unused -= 1
    return unused


def refill_candidates(families: list, tier: dict, alive: dict, carried: set) -> list:
    """D14: refill draws from remarkable and maybe happenings only; background is never funded to fill time."""
    return sorted(
        (f for f in families if tier.get(f, 2) <= 1 and alive.get(f) and f not in carried),
        key=lambda f: tier[f],
    )


def largest_remainder(shares: list[float], total: int) -> list[int]:
    raw = [s * total / 100.0 for s in shares]
    base = [int(x) for x in raw]
    rem = total - sum(base)
    order = sorted(range(len(raw)), key=lambda i: -(raw[i] - base[i]))
    for i in order[: max(0, rem)]:
        base[i] += 1
    return base


def allocate_cell_carriers(
    *,
    fams,
    cell_slots,
    cap,
    prior_take,
    tier,
    event_units,
    unit_line,
    shows_life,
    ladder,
    planned_take=None,
    funded_ladder=None,
):
    """Fund primary voices before depth, reading ladders only when they can take a slot."""
    rungs: dict = {}

    def get_rungs(f):
        if f not in rungs:
            rungs[f] = ladder(f)
        return rungs[f]

    if planned_take is not None:
        return _planned_take(
            fams,
            cell_slots=cell_slots,
            cap=cap,
            prior_take=prior_take,
            tier=tier,
            planned_take=planned_take,
            ladder=ladder,
            funded_ladder=funded_ladder,
            rungs=rungs,
        )
    take = {
        f: min(prior_take[f], len(get_rungs(f))) if prior_take.get(f, 0) > 0 else 0 for f in fams
    }
    ranking = _FamilyRanking(event_units, unit_line, shows_life)
    left = max(cell_slots - sum(take.values()), 0)
    # one primary per anchor first (by picture count), then descend ladders by favourites then pictures
    order = sorted(fams, key=lambda x: (tier[x], *ranking.primary_key(x)))
    left = _fund_primaries(order, take, tier, get_rungs, left)
    _deepen_ladders(sorted(order, key=ranking.depth_key), take, tier, cap, get_rungs, left)
    return take, rungs


def _fund_primaries(order, take, tier, get_rungs, left: int) -> int:
    """D14: one voice per anchor before any depth; background never takes a first-pass slot."""
    for f in order:
        if left <= 0:
            break
        if tier[f] == 2 or take[f] > 0 or not get_rungs(f):
            continue
        take[f] = 1
        left -= 1
    return left


def _deepen_ladders(order, take, tier, cap, get_rungs, left: int) -> int:
    """D14: background takes no depth either; a short film beats a padded one."""
    for f in order:
        if left <= 0:
            break
        if tier[f] == 2:
            continue
        while left > 0 and take[f] < min(cap, len(get_rungs(f))):
            take[f] += 1
            left -= 1
    return left


def _planned_take(
    fams,
    *,
    cell_slots,
    cap,
    prior_take,
    tier,
    planned_take,
    ladder,
    funded_ladder,
    rungs,
):
    """A valid editorial decision must not be overwritten by activity/count ranking.
    Its demand reaches acquisition before a short first page can erase funded depth."""
    take = dict.fromkeys(fams, 0)
    for f in fams:
        requested = prior_take.get(f, 0)
        if tier[f] <= 1:
            requested = min(cap, max(requested, planned_take.get(f, 0)))
        if requested > 0:
            rungs[f] = funded_ladder(f, requested) if funded_ladder else ladder(f)
            take[f] = min(requested, len(rungs[f]))
    if sum(take.values()) > cell_slots:
        raise ValueError("planned event funding exceeds cell budget")
    return take, rungs


class _FamilyRanking:
    """How an anchor competes for a first slot and then for depth."""

    def __init__(self, event_units, unit_line, shows_life):
        self._units = event_units
        self._line = unit_line
        self._life = shows_life

    def _favourites(self, x) -> int:
        return sum(1 for u in self._units[x] if u.get("favourite"))

    def _life_score(self, x) -> int:
        return sum(1 for u in self._units[x] if self._life(u))

    def _posed_only(self, x) -> bool:
        acts = set()
        for u in self._units[x]:
            m = re.search(r"activity=([a-z\-]+)", self._line(u))
            acts.add(m.group(1) if m else "-")
        return acts <= {"posing", "-"}

    def primary_key(self, x) -> tuple:
        return (
            self._posed_only(x),
            self._life_score(x) == 0,
            -self._favourites(x),
            -self._life_score(x),
            -len(self._units[x]),
        )

    def depth_key(self, x) -> tuple:
        return (self._life_score(x) == 0, -self._favourites(x), -len(self._units[x]))
