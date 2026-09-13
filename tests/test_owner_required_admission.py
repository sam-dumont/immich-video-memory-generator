"""A picture the owner ticks after a cut is admitted, not re-argued (#778)."""

from __future__ import annotations

from immich_memories.analysis.cull_answer import CullDecision
from immich_memories.analysis.editorial_contracts import DecisionProvenance
from immich_memories.analysis.selection_cull import run_cull_decisions
from immich_memories.analysis.selection_source import (
    EditorialDependencies,
    EditorialSelectionRequest,
    SourceScope,
    prepare_editorial_source,
)
from tests.conftest import make_asset


def _provenance(prepared) -> DecisionProvenance:
    return DecisionProvenance(
        pass_name="pass-1-cull",  # noqa: S106 -- editorial pass label, not a credential.
        pass_version="pass-1-v1",  # noqa: S106 -- provenance version, not a credential.
        schema_version="test",
        model_identity="test",
        input_ids=prepared.candidate_ids,
        sheet_hashes=(),
        request_key="test",
        cache_hit=False,
    )


def test_a_required_picture_is_recorded_on_the_source_and_an_unknown_one_is_named() -> None:
    kept, other = make_asset("kept"), make_asset("other")
    request = EditorialSelectionRequest(
        scope=SourceScope(), owner_required_asset_ids=("kept", "not-in-pool")
    )

    prepared = prepare_editorial_source(
        request, EditorialDependencies(source_fetcher=lambda _scope: (kept, other))
    )

    assert prepared.owner_required_asset_ids == ("kept",)
    assert any("not-in-pool" in warning for warning in prepared.trace.warnings)


def test_the_cull_cannot_remove_a_required_picture() -> None:
    kept, other = make_asset("kept"), make_asset("other")
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope(), owner_required_asset_ids=("kept",)),
        EditorialDependencies(source_fetcher=lambda _scope: (kept, other)),
    )

    culled = run_cull_decisions(
        prepared,
        (
            CullDecision(asset_id="kept", bucket="failed"),
            CullDecision(asset_id="other", bucket="failed"),
        ),
        provenance=_provenance(prepared),
    )

    assert [candidate.asset_id for candidate in culled.survivors] == ["kept"]
    assert any("required" in warning for warning in culled.warnings)


def test_the_timing_trim_never_drops_a_required_picture() -> None:
    from immich_memories.analysis.editorial_story_planner import trim_to_timing_budget

    carriers = [
        {
            "asset_id": "d1",
            "story_episode": "K1",
            "story_weight": "dominant",
            "taken": "2025-04-18T10:00",
        },
        {
            "asset_id": "m1",
            "story_episode": "K2",
            "story_weight": "minor",
            "taken": "2025-04-01T10:00",
        },
        {
            "asset_id": "m2",
            "story_episode": "K2",
            "story_weight": "minor",
            "taken": "2025-04-02T10:00",
        },
    ]

    kept, dropped = trim_to_timing_budget(
        carriers, lambda _cs: 7.0, 3.5, protected=frozenset({"m2"})
    )

    assert [c["asset_id"] for c in dropped] == ["m1"]
    assert [c["asset_id"] for c in kept] == ["d1", "m2"]


def _episode(key: str, moments: list[str]):
    from immich_memories.analysis.editorial_story_reading import StoryEpisode

    return StoryEpisode(
        key=key, title=key, account="", significance="", role="", uncertainty="", moments=moments
    )


def test_a_required_picture_the_read_dropped_joins_the_cut_in_its_own_story() -> None:
    from immich_memories.analysis.editorial_owner_required import admit_owner_required

    units = {
        "F1": [
            {
                "asset_id": "a",
                "moment": "M1",
                "taken": "2025-06-01T10:00",
                "kind": "still",
                "seconds": 3.5,
            },
            {
                "asset_id": "b",
                "moment": "M1",
                "taken": "2025-06-01T10:05",
                "kind": "still",
                "seconds": 3.5,
            },
        ],
        "F2": [
            {
                "asset_id": "c",
                "moment": "M2",
                "taken": "2025-06-02T09:00",
                "kind": "still",
                "seconds": 3.5,
            },
        ],
    }
    stories = [
        {"key": "S1", "title": "Picnic", "weight": "dominant", "episodes": ["E1"]},
        {"key": "S2", "title": "Woods", "weight": "minor", "episodes": ["E2"]},
    ]
    episodes = [_episode("E1", ["M1"]), _episode("E2", ["M2"])]
    carried = [units["F1"][0] | {"story_episode": "S1", "story_weight": "dominant", "event": "F1"}]

    carriers, record = admit_owner_required(
        carried,
        required=("b", "c", "zzz"),
        units=units,
        stories=stories,
        episodes=episodes,
        anchor_label={"F1": "the garden", "F2": "the woods"},
        line_of=lambda asset_id: f"line {asset_id}",
    )

    assert [c["asset_id"] for c in carriers] == ["a", "b", "c"]  # capture order
    admitted = {c["asset_id"]: c for c in carriers if c.get("owner_required")}
    assert admitted["b"]["story_episode"] == "S1" and admitted["b"]["story_weight"] == "dominant"
    assert admitted["c"]["story_episode"] == "S2" and admitted["c"]["chapter"] == 2
    assert admitted["c"]["anchor"] == "the woods" and admitted["c"]["line"] == "line c"
    assert "the owner" in admitted["b"]["why"]
    assert record == {"admitted": ["b", "c"], "already_carried": [], "not_in_material": ["zzz"]}


def test_a_required_picture_already_in_the_cut_is_left_alone() -> None:
    from immich_memories.analysis.editorial_owner_required import admit_owner_required

    units = {"F1": [{"asset_id": "a", "moment": "M1", "taken": "2025-06-01T10:00"}]}
    carried = [units["F1"][0] | {"story_episode": "S1", "story_weight": "dominant", "event": "F1"}]

    carriers, record = admit_owner_required(
        carried, required=("a",), units=units, stories=[], episodes=[], anchor_label={}, line_of=str
    )

    assert carriers == carried
    assert record == {"admitted": [], "already_carried": ["a"], "not_in_material": []}


def test_a_required_picture_outside_every_story_borrows_the_nearest_carrier_story() -> None:
    from immich_memories.analysis.editorial_owner_required import admit_owner_required

    units = {
        "F1": [
            {"asset_id": "a", "moment": "M1", "taken": "2025-06-01T10:00", "members": ["a", "a2"]}
        ],
        "F9": [{"asset_id": "stray", "moment": "M9", "taken": "2025-06-01T11:00"}],
        "F8": [{"asset_id": "undated", "moment": "M8", "taken": "not a date"}],
    }
    stories = [
        {"key": "S1", "title": "Picnic", "weight": "dominant", "episodes": ["E1"]},
        {"key": "S2", "title": "Lake", "weight": "minor", "episodes": ["E2"]},
    ]
    episodes = [_episode("E1", ["M1"]), _episode("E2", ["M2"])]
    carried = [units["F1"][0] | {"story_episode": "S1", "story_weight": "dominant", "event": "F1"}]

    carriers, record = admit_owner_required(
        carried,
        required=("a2", "stray", "undated"),
        units=units,
        stories=stories,
        episodes=episodes,
        anchor_label={},
        line_of=str,
    )

    # a2 is a member of the carried live unit, so it counts as carried already.
    assert record["already_carried"] == ["a2"]
    by_id = {c["asset_id"]: c for c in carriers}
    assert by_id["stray"]["story_episode"] == "S1" and by_id["stray"]["chapter"] == 1
    assert by_id["undated"]["story_episode"] == "S1"


def test_a_required_picture_with_no_stories_at_all_still_joins_the_cut() -> None:
    from immich_memories.analysis.editorial_owner_required import admit_owner_required

    units = {"F1": [{"asset_id": "only", "moment": "M1", "taken": "2025-06-01T10:00"}]}

    carriers, record = admit_owner_required(
        [], required=("only",), units=units, stories=[], episodes=[], anchor_label={}, line_of=str
    )

    assert record["admitted"] == ["only"]
    assert carriers[0]["story_weight"] == "none" and carriers[0]["story_role"] == "incidental"


def test_nothing_required_leaves_the_carriers_untouched() -> None:
    from immich_memories.analysis.editorial_owner_required import admit_owner_required

    carried = [{"asset_id": "a", "taken": "2025-06-01T10:00"}]

    carriers, record = admit_owner_required(
        carried, required=(), units={}, stories=[], episodes=[], anchor_label={}, line_of=str
    )

    assert carriers is carried
    assert record == {"admitted": [], "already_carried": [], "not_in_material": []}


def test_the_timing_trim_stops_when_only_required_pictures_remain() -> None:
    from immich_memories.analysis.editorial_story_planner import trim_to_timing_budget

    carriers = [
        {
            "asset_id": "r1",
            "story_episode": "K1",
            "story_weight": "minor",
            "taken": "2025-04-01T10:00",
        },
        {
            "asset_id": "r2",
            "story_episode": "K1",
            "story_weight": "minor",
            "taken": "2025-04-02T10:00",
        },
    ]

    kept, dropped = trim_to_timing_budget(
        carriers, lambda _cs: 3.5, 3.5, protected=frozenset({"r1", "r2"})
    )

    assert dropped == []
    assert [c["asset_id"] for c in kept] == ["r1", "r2"]
