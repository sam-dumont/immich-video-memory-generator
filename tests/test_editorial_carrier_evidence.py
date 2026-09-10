"""Source exclusions and year coverage precede picture density and motion preference.

The general worthiness-criterion tests belong to the retired thread lane and stay on the probe branch.
"""

from datetime import UTC, date, datetime

import pytest

from immich_memories.analysis.editorial_carrier_eligibility import excluded_carrier_sources
from immich_memories.analysis.editorial_intent import build_editorial_intent
from immich_memories.analysis.editorial_intent_validation import CarrierView, validate_intent
from immich_memories.analysis.editorial_shareability import partition_units
from immich_memories.analysis.editorial_structure_budget import partition_budgets
from immich_memories.timeperiod import DateRange


@pytest.mark.parametrize(
    "kind,favourite", [("video", False), ("still", True), ("live-motion", True)]
)
def test_document_evidence_cannot_gain_a_carrier_from_motion_or_favourite(kind, favourite):
    lines = {
        "graphic": "A sporting result | document=geographical_map, location=outdoor",
        "scene": "A group examines a map together | people=small-group",
    }
    rejected = excluded_carrier_sources(lines)
    assert rejected == {"graphic": "document-head:geographical_map"}
    units = [{"asset_id": key, "kind": kind, "favourite": favourite} for key in lines]
    kept, excluded = partition_units(units, rejected)
    assert [u["asset_id"] for u in kept] == ["scene"]
    assert [u["asset_id"] for u in excluded] == ["graphic"]
    assert "graphic" in lines  # The source remains evidence of the occasion.


def test_a_screen_member_cannot_hide_behind_a_burst_primary():
    rejected = excluded_carrier_sources({"companion": "A screenshot of a running app interface."})
    unit = {"asset_id": "primary", "members": ["primary", "companion"], "kind": "live-motion"}
    assert partition_units([unit], rejected) == ([], [unit])


@pytest.mark.parametrize(
    "line",
    [
        "Runners crossing a bridge | document=photograph, people=crowd",
        "A family studying a paper map in a garden | people=small-group",
        "A concert on a stage | document=icon, people=crowd",
        "A child drawing a house | people=one",
    ],
)
def test_real_scenes_are_not_rejected_by_subject_or_incidental_document_words(line):
    assert excluded_carrier_sources({"scene": line}) == {}


def intent(product="year_in_review", start=(2032, 1, 1), end=(2032, 12, 31)):
    return build_editorial_intent(
        product,
        [DateRange(datetime(*start, tzinfo=UTC), datetime(*end, 23, 59, 59, tzinfo=UTC))],
        brief="A truthful memory of the period.",
    )


def test_dense_month_cannot_take_the_year_budget_from_other_worthy_months():
    policy = intent()
    parts = [p.key for p in policy.required_partitions]
    capacity = dict.fromkeys(parts, 3)
    capacity[parts[1]] = 300
    capacity[parts[4]] = 0
    budgets = partition_budgets(parts, capacity, 24)
    assert len(parts) == 12
    assert budgets[parts[4]] == 0
    assert all(budgets[p] >= 1 for p in parts if capacity[p])
    assert sum(budgets.values()) == 24
    assert budgets[parts[1]] < sum(budgets.values()) / 2


def test_missing_worthy_month_is_reported_instead_of_a_successful_year():
    policy = intent()
    report = validate_intent(
        policy,
        carriers=[CarrierView(str(i), date(2032, 6, i), f"event-{i}", 4.0) for i in range(1, 4)],
        evidence_partitions={"month-2032-01", "month-2032-06"},
        requested_seconds=60,
    )
    assert report.status == "coverage_incomplete"
    assert any(v.partition == "month-2032-01" for v in report.violations)


def test_partial_year_months_are_clipped_and_leap_day_is_covered():
    policy = intent(start=(2032, 2, 12), end=(2032, 4, 5))
    assert [p.label for p in policy.partitions] == [
        "2032-02-12..2032-02-29",
        "2032-03-01..2032-03-31",
        "2032-04-01..2032-04-05",
    ]


@pytest.mark.parametrize("product", ["person_spotlight", "trip", "monthly_highlights"])
def test_other_product_contracts_keep_their_existing_partitioning(product):
    assert len(intent(product).partitions) == 1
