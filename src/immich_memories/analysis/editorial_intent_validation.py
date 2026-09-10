"""Deterministic checks of a plan against its EditorialIntent (tracker D09, D14).

What a validator can decide without a model: whether every required partition that holds worthy
evidence carries something, whether a two-sided product came back one-sided, whether a recurring
product collapsed onto its latest years, and whether the material is too thin to call a memory.
Everything semantic (does a funded beat really concern the requested subject) stays with the
prompts and the structural review.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import date

from immich_memories.analysis.editorial_intent import EditorialIntent

__all__ = ["CarrierView", "IntentReport", "Violation", "validate_intent"]

MIN_CARRIERS = 3
MIN_CONTENT_SHARE = 0.20
DOMINANCE_SHARE = 0.80


@dataclass(frozen=True)
class CarrierView:
    asset_id: str
    taken: date
    event: str
    seconds: float


@dataclass(frozen=True)
class Violation:
    code: str
    severity: str  # "structural" invalidates the plan; "coverage" must be explained in the plan
    partition: str | None
    detail: str


@dataclass(frozen=True)
class IntentReport:
    status: str  # ok | insufficient_material | structural_violation | coverage_incomplete
    violations: tuple[Violation, ...]
    requested_seconds: float
    usable_seconds: float
    shortfall_seconds: float
    reason: str | None
    coverage: dict[str, int]

    def as_record(self) -> dict:
        return {
            "status": self.status,
            "requested_seconds": self.requested_seconds,
            "usable_seconds": round(self.usable_seconds, 2),
            "shortfall_seconds": round(self.shortfall_seconds, 2),
            "reason": self.reason,
            "coverage": self.coverage.copy(),
            "violations": [vars(v) for v in self.violations],
        }


def _coverage(intent: EditorialIntent, carriers: Sequence[CarrierView]) -> dict[str, int]:
    counts = {part.key: 0 for part in intent.partitions}
    for carrier in carriers:
        part = intent.partition_for(carrier.taken)
        if part is not None:
            counts[part.key] += 1
    return counts


def _over_limit(intent: EditorialIntent, coverage: dict[str, int]) -> list[Violation]:
    limit = intent.max_carriers_per_partition
    if limit is None:
        return []
    return [
        Violation(
            "partition_carrier_limit",
            "structural",
            part.key,
            f"{part.label} carries {coverage[part.key]} pictures; the limit is {limit}",
        )
        for part in intent.partitions
        if coverage[part.key] > limit
    ]


def _uncovered(
    intent: EditorialIntent,
    coverage: dict[str, int],
    evidence_partitions: Collection[str],
    *,
    two_sided: bool,
) -> list[Violation]:
    return [
        Violation(
            code="uncovered_partition",
            severity="structural" if two_sided else "coverage",
            partition=part.key,
            detail=f"{part.label} holds worthy evidence and carries nothing",
        )
        for part in intent.required_partitions
        if part.key in evidence_partitions and coverage[part.key] == 0
    ]


def _one_sided(coverage: dict[str, int], carriers: Sequence[CarrierView]) -> list[Violation]:
    if not carriers:
        return []
    top = max(coverage.values())
    if top / len(carriers) <= DOMINANCE_SHARE:
        return []
    return [
        Violation(
            "one_sided_comparison",
            "structural",
            None,
            f"{top} of {len(carriers)} carriers fall on one side of the gap",
        )
    ]


def _latest_year_cluster(
    intent: EditorialIntent, coverage: dict[str, int], evidence_partitions: Collection[str]
) -> list[Violation]:
    with_evidence = [p for p in intent.partitions if p.key in evidence_partitions]
    if len(with_evidence) < 3:
        return []
    covered = [p for p in with_evidence if coverage[p.key] > 0]
    latest = with_evidence[-2:]
    if not covered or any(p not in latest for p in covered):
        return []
    return [
        Violation(
            "latest_year_cluster",
            "coverage",
            None,
            f"only the latest {len(covered)} of {len(with_evidence)} occurrences with evidence are carried",
        )
    ]


def _verdict(
    intent: EditorialIntent,
    violations: Sequence[Violation],
    *,
    carriers: Sequence[CarrierView],
    usable: float,
    requested_seconds: float,
) -> tuple[str, str | None]:
    structural = [v.detail for v in violations if v.severity == "structural"]
    if structural:
        return "structural_violation", "; ".join(structural)
    if len(carriers) < MIN_CARRIERS or (
        requested_seconds > 0 and usable < MIN_CONTENT_SHARE * requested_seconds
    ):
        return "insufficient_material", (
            f"{len(carriers)} carrier(s), {usable:.1f} s usable of {requested_seconds:g} s requested: "
            "the material does not establish the requested memory; " + intent.abstention_policy
        )
    if violations:
        return "coverage_incomplete", "; ".join(v.detail for v in violations)
    return "ok", None


def validate_intent(
    intent: EditorialIntent,
    *,
    carriers: Sequence[CarrierView],
    evidence_partitions: Collection[str],
    # A captured case counts its seconds as an integer; the report always states a float.
    requested_seconds: float | int,
) -> IntentReport:
    """Judge the plan's shape against the contract. Sparse material is reported, never padded."""
    usable = float(sum(c.seconds for c in carriers))
    coverage = _coverage(intent, carriers)
    two_sided = intent.product == "then_and_now"
    violations = [
        *_over_limit(intent, coverage),
        *_uncovered(intent, coverage, evidence_partitions, two_sided=two_sided),
        *(_one_sided(coverage, carriers) if two_sided else []),
        *(
            _latest_year_cluster(intent, coverage, evidence_partitions)
            if intent.product in ("on_this_day", "holiday") and carriers
            else []
        ),
    ]
    status, reason = _verdict(
        intent,
        violations,
        carriers=carriers,
        usable=usable,
        requested_seconds=requested_seconds,
    )
    return IntentReport(
        status=status,
        violations=tuple(violations),
        requested_seconds=float(requested_seconds),
        usable_seconds=usable,
        shortfall_seconds=max(0.0, requested_seconds - usable),
        reason=reason,
        coverage=coverage,
    )
