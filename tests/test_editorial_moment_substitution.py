"""A refused picture loses its frame, not its moment.

The favourite wins its moment in both readers. Only the reader that can compare pictures
can afford to throw the moment's other frames away: without one, a favourite refused as a
carrier takes the whole depicted moment out of the film and nothing is left to substitute.
"""

from datetime import UTC, datetime
from types import SimpleNamespace

from immich_memories.analysis.editorial_story_carriers import CarrierAdmission, StandingGate
from immich_memories.analysis.editorial_story_shortlist import DepictedChoice
from immich_memories.analysis.editorial_story_slots import PartitionedSlots
from immich_memories.analysis.editorial_structure_material import UnitBuilder
from immich_memories.config_loader import Config


def _asset(asset_id, *, minute, favourite=False):
    return SimpleNamespace(
        id=asset_id,
        is_favorite=favourite,
        people=[],
        faces=[],
        duration_seconds=None,
        type=SimpleNamespace(value="IMAGE"),
        file_created_at=datetime(2024, 2, 7, 10, minute, tzinfo=UTC),
    )


def _builder(assets, moments, *, rules):
    source = SimpleNamespace(
        assets={a.id: a for a in assets},
        motion_residuals={},
        speech_regions={},
        config=Config(),
        pixel_facts={a.id: (900.0, 118.0) for a in assets},
    )
    wall = SimpleNamespace(
        event_assets={"F01": [a.id for a in assets]},
        moment_of_asset=moments,
    )
    ports = SimpleNamespace(
        resolve_motion=None,
        thumbnail_hash=lambda _asset_id: None,
        rules=object() if rules else None,
    )
    return UnitBuilder(source, ports, wall, renderings={}, never_auto=set(), document_sources={})


def _units(*, rules):
    assets = [
        _asset("star", minute=0, favourite=True),
        _asset("sibling", minute=1),
        _asset("elsewhere", minute=40),
    ]
    moments = {"star": "M01", "sibling": "M01", "elsewhere": "M02"}
    return [u["asset_id"] for u in _builder(assets, moments, rules=rules).units_of("F01")]


def test_a_model_reader_keeps_only_the_favourite_of_its_moment():
    assert _units(rules=False) == ["star", "elsewhere"]


def test_the_rules_reader_keeps_the_moment_s_other_pictures_behind_the_favourite():
    assert _units(rules=True) == ["star", "sibling", "elsewhere"]


def _admission(*, standing, excluded=()):
    units = {
        "star": {
            "asset_id": "star",
            "moment": "M01",
            "taken": "2024-02-07T10:00:00",
            "favourite": True,
            "kind": "still",
            "seconds": 4.0,
        },
        "sibling": {
            "asset_id": "sibling",
            "moment": "M01",
            "taken": "2024-02-07T10:01:00",
            "favourite": False,
            "kind": "still",
            "seconds": 4.0,
        },
    }
    unit_by_asset = {a: ("F01", u) for a, u in units.items()}
    story = {
        "key": "S001",
        "title": "A day",
        "weight": "minor",
        "gate": "remarkable",
        "seen": {"favourites": 1},
        "purpose": "",
    }
    gate = StandingGate(
        None,
        contract="",
        period_label="",
        line_of=lambda a: f"line for {a}",
        life=lambda _a: False,
        unit_by_asset=unit_by_asset,
        pictures_of={"S001": 6},
        bank=None,
        save=None,
        calls={"standing_rounds": 0},
        score_of=standing.__getitem__,
    )
    choice = DepictedChoice(
        key="M01:cg",
        episode="S001",
        taken="2024-02-07T10:00:00",
        content="capture group",
        primary="star",
        alternatives=["sibling"],
    )
    return CarrierAdmission(
        None,
        stories=[story],
        choices_of={"S001": [choice]},
        unit_by_asset=unit_by_asset,
        anchor_label={"F01": "A day"},
        parts=PartitionedSlots(unit_by_asset),
        gate=gate,
        line_of=lambda a: f"line for {a}",
        life=lambda _a: False,
        excluded=dict.fromkeys(excluded, "document-head:table"),
        kind_marker=lambda _c: "",
        motion_line=None,
        contract="",
        record=lambda _name, _value: None,
        slots=4,
        calls={"pick_calls": 0, "standing_rounds": 0},
        mechanical_picks=True,
    )


def test_a_moment_whose_favourite_fails_standing_is_carried_by_its_other_picture():
    admission = _admission(standing={"star": 0, "sibling": 2})

    admission.run()

    assert [c["asset_id"] for c in admission.carriers] == ["sibling"]


def test_a_moment_whose_favourite_is_excluded_as_a_carrier_is_carried_by_its_other_picture():
    admission = _admission(standing={"star": 2, "sibling": 2}, excluded=("star",))

    admission.run()

    assert [c["asset_id"] for c in admission.carriers] == ["sibling"]


def test_the_carrier_row_names_the_other_pictures_of_its_own_moment():
    admission = _admission(standing={"star": 2, "sibling": 2})

    admission.run()

    assert admission.carriers[0]["asset_id"] == "star"
    assert admission.carriers[0]["moment_alternatives"] == ["sibling"]
