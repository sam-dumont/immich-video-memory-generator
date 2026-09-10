"""Grouped people conditions survive production boundaries without widening demand.

The matrix replays stay on the probe branch.

The wall-source and period-card probe replays stay on the probe branch.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from immich_memories.analysis.editorial_case import Case
from immich_memories.analysis.editorial_runtime import EditorialRunContext
from immich_memories.analysis.editorial_source import (
    filter_named_expression,
)
from immich_memories.analysis.selection_trace import Trace
from immich_memories.api.models import AssetType, Person
from immich_memories.api.person_expression import PersonExpression
from immich_memories.memory_types.factory import create_preset
from immich_memories.memory_types.presets import person_filter_for
from immich_memories.memory_types.registry import MemoryType
from immich_memories.timeperiod import DateRange
from tests import test_editorial_source_route_integration as source_fixture
from tests.conftest import make_asset
from tests.test_editorial_duration_planner_integration import source as captured_source

EXPRESSION = PersonExpression.parse('("Adult A" OR "Adult B") AND "Child"')
WINDOW = DateRange(datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 12, 31, tzinfo=UTC))


def _case(**changes):
    return Case(
        "grouped",
        "Grouped memory",
        "year_in_review",
        (WINDOW,),
        60,
        "Keep the worthwhile events.",
        **changes,
    )


def _person(face, name):
    return Person(id=face, name=name)


def _asset(key, people=(), *, video=False):
    asset = make_asset(key, file_created_at=WINDOW.start)
    asset.type = AssetType.VIDEO if video else AssetType.IMAGE
    asset.people = list(people)
    return asset


@pytest.mark.parametrize(
    "memory_type,parameters",
    [
        (MemoryType.YEAR_IN_REVIEW, {"year": 2024}),
        (MemoryType.MONTHLY_HIGHLIGHTS, {"year": 2024, "month": 3}),
        (MemoryType.MULTI_PERSON, {"year": 2024}),
    ],
)
@pytest.mark.parametrize("serialized", [False, True])
def test_presets_preserve_group_ast_and_derive_all_names(memory_type, parameters, serialized):
    value = EXPRESSION.to_dict() if serialized else EXPRESSION
    preset = create_preset(memory_type, person_expression=value, **parameters)
    assert preset.person_filter.person_expression == EXPRESSION
    assert preset.person_filter.person_names == list(EXPRESSION.leaf_values)
    assert preset.person_filter.mode == "expression"
    assert not preset.person_filter.require_co_occurrence
    if memory_type == MemoryType.MULTI_PERSON:
        assert EXPRESSION.display_label in preset.name


def test_preset_rejects_names_that_disagree_with_grouped_condition():
    with pytest.raises(ValueError, match="disagree"):
        create_preset(
            MemoryType.YEAR_IN_REVIEW,
            year=2024,
            person_names=["Other"],
            person_expression=EXPRESSION,
        )


@pytest.mark.parametrize("existing_names", [(), ("Child", "Adult B", "Adult A", "Adult A")])
def test_case_and_context_normalize_names_to_unique_expression_order(tmp_path, existing_names):
    case = _case(people=existing_names, person_expression=EXPRESSION)
    context = EditorialRunContext(
        "grouped",
        "Grouped",
        "year_in_review",
        (WINDOW,),
        60,
        tmp_path,
        people=existing_names,
        person_expression=EXPRESSION,
    )
    assert case.people == context.people == EXPRESSION.leaf_values
    assert case.person_expression is context.person_expression is EXPRESSION


def test_case_and_context_reject_inconsistent_names_and_untyped_expression(tmp_path):
    for changes in (
        {"people": ("Other",), "person_expression": EXPRESSION},
        {"person_expression": EXPRESSION.to_dict()},
    ):
        with pytest.raises(ValueError):
            _case(**changes)
        with pytest.raises(ValueError):
            EditorialRunContext(
                "grouped", "Grouped", "year_in_review", (WINDOW,), 60, tmp_path, **changes
            )


def test_named_filter_requires_cooccurrence_in_one_asset_not_across_the_window():
    sources = [_asset("adult", [_person("a", "Adult A")]), _asset("child", [_person("c", "Child")])]
    assert filter_named_expression(sources, EXPRESSION) == ()


@pytest.mark.parametrize("matching", [True, False])
def test_actual_runtime_filters_demand_but_keeps_full_canonical_context(
    tmp_path, monkeypatch, matching
):
    real_context = source_fixture.EditorialRunContext
    monkeypatch.setattr(
        source_fixture,
        "EditorialRunContext",
        lambda *args, **kwargs: real_context(*args, **kwargs, person_expression=EXPRESSION),
    )
    sources, _config, build, calls, captures, readers, _images, _warm = (
        source_fixture.setup_runtime(tmp_path, monkeypatch)
    )
    child = _person("child", "Child")
    sources[0].people = [_person("a-old", "Adult A"), child] if matching else []
    sources[1].people = [_person("a-new", "Adult A"), child] if matching else []
    sources[2].people = [_person("b", "Adult B"), child] if matching else []
    sources[3].people = [_person("a-alone", "Adult A")]
    sources[4].people = [child]
    source_bytes = [a.model_dump(mode="json") for a in sources]
    try:
        result = build().plan_source(sources, trace=Trace(), include_live_photos=False)
        expected = {a.id for a in sources[:3]} if matching else set()
        assert {row.clip.asset.id for row in result.candidates} == expected
        assert set(result.plan.selected_asset_ids).issubset(expected)
        assert len(calls["acquire"]) == 1
        assert not calls["acquire"][0].asset_ids
        if matching:
            assert captures
            assert set(captures[0].assets) == {a.id for a in sources}
            assert {key for ids in captures[0].moment_asset_ids.values() for key in ids} == expected
            assert captures[0].case.person_expression == EXPRESSION
        else:
            assert not result.plan.selections
        assert [a.model_dump(mode="json") for a in sources] == source_bytes
    finally:
        for reader in readers:
            reader.close()


def test_structure_input_rejects_unmatching_selected_asset_but_allows_context(tmp_path):
    captured = captured_source(tmp_path, seconds=60, pictures=2)
    first, second = captured.assets.values()
    first = first.model_copy(update={"people": [_person("a", "Adult A"), _person("c", "Child")]})
    second = second.model_copy(update={"people": [_person("a", "Adult A")]})
    case = replace(captured.case, person_expression=EXPRESSION)
    assets = {first.id: first, second.id: second}
    with pytest.raises(ValueError, match="outside the grouped people condition"):
        replace(captured, case=case, assets=assets)
    alias = next(iter(captured.moment_asset_ids))
    narrowed = replace(captured, case=case, assets=assets, moment_asset_ids={alias: (first.id,)})
    assert set(narrowed.assets) == {first.id, second.id}
    assert narrowed.moment_asset_ids == {alias: (first.id,)}


def test_flat_preset_filters_keep_existing_and_or_behavior():
    both = person_filter_for(["Adult A", "Child"])
    either = person_filter_for(["Adult A", "Child"], person_match="or")
    assert (both.mode, both.require_co_occurrence, both.person_expression) == ("all_of", True, None)
    assert (either.mode, either.require_co_occurrence, either.person_expression) == (
        "any",
        False,
        None,
    )
