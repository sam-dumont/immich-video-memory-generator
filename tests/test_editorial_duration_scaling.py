"""A longer requested edit can buy more event depth without inventing usable pictures.

The ladder-acquisition tests belong to the retired ladder planner and stay on the probe branch; the depth-cap tests remain.
"""

import pytest

from immich_memories.analysis.editorial_picture_ladders import (
    depth_cap,
)
from immich_memories.analysis.editorial_structure_budget import (
    CONTENT_RESERVE_SECONDS,
    MIN_CARRIER_SECONDS,
    NOMINAL_STILL_SECONDS,
    allocate_cell_carriers,
    required_voice_slot_budget,
)


@pytest.mark.parametrize("target,expected", [(60, 13), (120, 28), (180, 43), (600, 148)])
def test_still_edit_budget_uses_the_content_allowance_at_its_actual_pace(target, expected):
    slots = required_voice_slot_budget(target, 0)
    assert slots == expected
    spare = target - CONTENT_RESERVE_SECONDS - slots * NOMINAL_STILL_SECONDS
    assert 0 <= spare < NOMINAL_STILL_SECONDS
    assert depth_cap(target) >= slots
    assert depth_cap(target) * MIN_CARRIER_SECONDS <= target - CONTENT_RESERVE_SECONDS


def test_explicit_media_pace_changes_picture_demand_without_spending_title_time():
    assert required_voice_slot_budget(180, 0, nominal_seconds=6) == 28
    assert required_voice_slot_budget(180, 0, nominal_seconds=4) == 43
    assert required_voice_slot_budget(180, 0, nominal_seconds=2) == depth_cap(180)
    with pytest.raises(ValueError, match="positive"):
        required_voice_slot_budget(180, 0, nominal_seconds=0)


@pytest.mark.parametrize("target", [0, 3, CONTENT_RESERVE_SECONDS])
def test_no_content_time_creates_no_event_capacity(target):
    assert depth_cap(target) == required_voice_slot_budget(target, 0) == 0


def rung(key, *, members=()):
    return ({"asset_id": key, "members": members}, "A distinct part of the event")


def allocate(planned, funded_ladder, *, prior=None):
    return allocate_cell_carriers(
        fams=["occasion", "routine"],
        cell_slots=12,
        cap=depth_cap(180),
        prior_take=prior or {},
        tier={"occasion": 0, "routine": 0},
        event_units={"occasion": [{}] * 20, "routine": [{}] * 20},
        unit_line=lambda _: "",
        shows_life=lambda _: True,
        ladder=lambda f: [rung(f)],
        planned_take=planned,
        funded_ladder=funded_ladder,
    )


def test_valid_funding_requests_full_depth_before_a_short_first_page_can_truncate_it():
    calls = []

    def funded(family, requested):
        calls.append((family, requested))
        return [rung(str(i)) for i in range(requested)]

    take, _ = allocate({"occasion": 8}, funded, prior={"occasion": 2})
    assert take == {"occasion": 8, "routine": 0}
    assert calls == [("occasion", 8)]


def test_fallback_does_not_invoke_the_funded_callback_or_invent_a_request():
    def unexpected(*_):
        pytest.fail("the funded callback needs an explicit editorial decision")

    take, _ = allocate(None, unexpected)
    assert take == {"occasion": 1, "routine": 1}
    take, _ = allocate({}, unexpected)
    assert take == {"occasion": 0, "routine": 0}
