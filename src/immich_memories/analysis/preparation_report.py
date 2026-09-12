"""What preparation cost, per picture and per producer, in units a person can act on.

On a low-power box preparation is the expensive half of a cut, and it is banked
per picture -- so the number that decides anything is seconds per picture per
producer, and what a stated library size therefore costs.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProducerCost:
    """One producer's share of a preparation pass.

    ``pending`` is the work that producer reported for itself, which is not
    always a count of pictures -- the detector stage counts one unit per
    detector per picture. Rates are therefore charged against the pictures in
    the scope, never against this number.
    """

    producer: str
    pending: int
    seconds: float

    def seconds_per_picture(self, pictures: int) -> float:
        return self.seconds / pictures if pictures else 0.0


class ProducerClock:
    """Charge preparation's wall clock to the producer that reported the progress.

    Preparation's stages run strictly in sequence and each reports under its own
    name, so the time between two reports belongs to whichever producer made the
    second one. A model load lands on that producer's first report, which is
    where it is actually paid.
    """

    def __init__(self, now: Callable[[], float] | None = None) -> None:
        self._now = now or time.perf_counter
        self._seconds: dict[str, float] = {}
        self._pending: dict[str, int] = {}
        self._order: list[str] = []
        self._mark = self._now()

    def report(self, producer: str, done: int, total: int) -> None:
        """Takes preparation's own `(stage, done, total)` progress callback shape."""
        moment = self._now()
        if producer not in self._seconds:
            self._order.append(producer)
            self._seconds[producer] = 0.0
        self._seconds[producer] += moment - self._mark
        self._mark = moment
        self._pending[producer] = max(self._pending.get(producer, 0), done, total)

    def costs(self) -> tuple[ProducerCost, ...]:
        return tuple(
            ProducerCost(producer, self._pending.get(producer, 0), self._seconds[producer])
            for producer in self._order
        )


def total_seconds_per_picture(costs: Sequence[ProducerCost], pictures: int) -> float:
    """What one picture of this scope cost through every producer that ran."""
    return sum(cost.seconds_per_picture(pictures) for cost in costs)


def human_duration(seconds: float) -> str:
    """Wall clock in the unit a person would say it in."""
    if seconds < 90:
        return f"{seconds:.0f} s"
    if seconds < 5400:
        return f"{seconds / 60:.0f} min"
    hours, remainder = divmod(round(seconds), 3600)
    return f"{hours} h {remainder // 60} min"


def rate_report(
    costs: Sequence[ProducerCost], *, pictures: int, library_size: int
) -> tuple[str, ...]:
    """The table a NAS owner reads: per producer, the total, and a library projection."""
    total = total_seconds_per_picture(costs, pictures)
    elapsed = sum(cost.seconds for cost in costs)
    lines = [
        f"{'producer':<14}{'pending':>9}{'s/picture':>12}{'share':>8}{'elapsed':>11}",
    ]
    for cost in costs:
        rate = cost.seconds_per_picture(pictures)
        share = f"{100 * cost.seconds / elapsed:.1f}%" if elapsed else "—"
        lines.append(
            f"{cost.producer:<14}{cost.pending:>9}{rate:>12.4f}{share:>8}"
            f"{human_duration(cost.seconds):>11}"
        )
    lines.extend(
        (
            f"{'total':<14}{pictures:>9}{total:>12.4f}{'100%':>8}{human_duration(elapsed):>11}",
            "",
            f"At this rate {library_size:,} pictures would take "
            f"{human_duration(total * library_size)}.",
        )
    )
    return tuple(lines)
