"""The wizard preserves grouped people scope from editing through source demand."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from immich_memories.api.models import Asset, AssetType, Person
from immich_memories.api.person_expression import PersonExpression
from immich_memories.config_loader import Config
from immich_memories.memory_types.factory import create_preset
from immich_memories.memory_types.registry import MemoryType
from immich_memories.timeperiod import calendar_year
from immich_memories.ui.pages import (
    clip_pipeline,
    step1_config,
    step1_people,
    step1_presets,
    step2_loading,
)
from immich_memories.ui.state import AppState

EXPRESSION = '("Person A" OR "Person B") AND "Person C"'


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("offline UI test attempted network work")

    monkeypatch.setattr("socket.socket.connect", forbidden)
    monkeypatch.setattr("socket.create_connection", forbidden)


def state() -> AppState:
    return AppState(
        memory_type="multi_person",
        date_ranges=[calendar_year(2024)],
        memory_preset_params={"year": 2024},
        people=[
            Person(id="face-a1", name="Person A"),
            Person(id="face-a2", name="Person A"),
            Person(id="face-b", name="Person B"),
            Person(id="face-c", name="Person C"),
        ],
    )


def test_preset_and_json_roundtrip_preserve_name_ast_and_every_matching_face():
    original = state()
    expression = PersonExpression.parse(EXPRESSION)
    original.apply_preset(
        create_preset(MemoryType.MULTI_PERSON, year=2024, person_expression=expression)
    )
    restored = state()
    restored.memory_preset_params = json.loads(json.dumps(original.memory_preset_params))
    assert restored.person_expression == expression
    assert restored.person_ids == []
    assert restored.selected_person is None
    assert restored.resolved_person_expression().to_dict() == {
        "all": [
            {
                "any": [
                    {"any": [{"person": "face-a1"}, {"person": "face-a2"}]},
                    {"person": "face-b"},
                ]
            },
            {"person": "face-c"},
        ]
    }
    assert restored.memory_preset_params["person_names"] == ["Person A", "Person B", "Person C"]


def test_all_time_preset_path_retains_groups_instead_of_rebuilding_flat_and():
    value = state()
    step1_people._set_grouped_condition(value, EXPRESSION)
    value.memory_preset_params["year"] = 0
    with patch.object(step1_presets, "get_app_state", return_value=value):
        step1_presets._apply_preset_to_state(MemoryType.MULTI_PERSON)
    assert value.person_expression == PersonExpression.parse(EXPRESSION)
    assert value.date_ranges and value.scope_is_selected


@pytest.mark.parametrize("text", ['"Missing Person" AND "Person A"', '"Person A" AND ('])
def test_invalid_edit_blocks_loading_even_with_an_older_valid_expression(text):
    value = state()
    step1_people._set_grouped_condition(value, EXPRESSION)
    with pytest.raises(ValueError):
        step1_people._set_grouped_condition(value, text)
    assert value.person_expression_error
    assert not value.scope_is_selected
    # WHY: the expression must be rejected before any fetch, so the client is armed to fail.
    with (
        # WHY: raises if Immich is reached at all — that is the assertion, not a stand-in.
        patch.object(step2_loading, "SyncImmichClient", side_effect=AssertionError("fetch")),
        pytest.raises(ValueError),
    ):
        step2_loading._fetch_assets(value)
    # A complete corrected edit releases the block, without carrying face IDs in the name AST.
    step1_people._set_grouped_condition(value, EXPRESSION)
    assert value.person_expression_error is None
    assert value.scope_is_selected


@pytest.mark.parametrize("product", ["person_spotlight", "trip", "album"])
def test_unsupported_product_is_rejected_before_any_media_fetch(product):
    value = state()
    value.memory_type = product
    value.memory_preset_params["person_expression"] = PersonExpression.parse(EXPRESSION).to_dict()
    with patch.object(step2_loading, "SyncImmichClient", side_effect=AssertionError("fetch")):
        with pytest.raises(ValueError, match="not supported"):
            step2_loading._fetch_assets(value)
        with pytest.raises(ValueError, match="not supported"):
            step2_loading._fetch_photos(value)
        if product == "album":
            with pytest.raises(ValueError, match="not supported"):
                step2_loading._fetch_album(value)


def test_birthday_mode_and_malformed_stored_tree_cannot_fall_back_to_flat_names():
    value = state()
    value.memory_preset_params["use_birthday"] = True
    with pytest.raises(ValueError, match="birthday"):
        step1_people._set_grouped_condition(value, EXPRESSION)
    value.person_expression_error = None
    value.memory_preset_params["person_expression"] = {}
    # WHY: the expression must be rejected before any fetch, so the client is armed to fail.
    with (
        # WHY: raises if Immich is reached at all — that is the assertion, not a stand-in.
        patch.object(step2_loading, "SyncImmichClient", side_effect=AssertionError("fetch")),
        pytest.raises(ValueError),
    ):
        step2_loading._fetch_assets(value)


@pytest.mark.parametrize("photos", [False, True])
def test_real_scoped_fetch_intersects_assets_not_members_of_the_same_event(photos):
    value = state()
    full_roster = value.people.copy()
    full_roster[1] = full_roster[1].model_copy(update={"is_hidden": True})
    value.people = [person for person in full_roster if not person.is_hidden]
    step1_people._set_grouped_condition(value, EXPRESSION)
    when = value.date_ranges[0].start
    assets = {
        key: Asset(
            id=key,
            type=AssetType.IMAGE if photos else AssetType.VIDEO,
            fileCreatedAt=when,
            fileModifiedAt=when,
            updatedAt=when,
        )
        for key in ("a-and-c", "a2-and-c", "b-and-c", "a-alone", "c-alone")
    }
    by_face = {
        "face-a1": [assets["a-and-c"], assets["a-alone"]],
        "face-a2": [assets["a2-and-c"]],
        "face-b": [assets["b-and-c"]],
        "face-c": [assets[k] for k in ("a-and-c", "a2-and-c", "b-and-c", "c-alone")],
    }
    calls = []

    def fetch(face, window):
        calls.append((face, window))
        return by_face[face]

    client = MagicMock()
    client.get_all_people.return_value = full_roster
    client.get_videos_for_person_and_date_range.side_effect = fetch
    client.get_photos_for_date_range.side_effect = lambda window, *, person_id: fetch(
        person_id, window
    )
    client.get_videos_for_date_range.side_effect = AssertionError("unfiltered query")
    client.get_videos_for_all_persons.side_effect = AssertionError("flattened AND query")
    with patch.object(step2_loading, "SyncImmichClient") as factory:
        factory.return_value.__enter__.return_value = client
        actual = (
            step2_loading._fetch_photos(value) if photos else step2_loading._fetch_assets(value)
        )
    assert {a.id for a in actual} == {"a-and-c", "a2-and-c", "b-and-c"}
    assert [face for face, _ in calls] == ["face-a1", "face-a2", "face-b", "face-c"]
    client.get_all_people.assert_called_once_with(with_hidden=True)


def test_authoritative_fetch_roster_missing_a_named_person_fails_without_media_query():
    value = state()
    step1_people._set_grouped_condition(value, EXPRESSION)
    client = MagicMock()
    client.get_all_people.return_value = [p for p in value.people if p.name != "Person C"]
    with patch.object(step2_loading, "SyncImmichClient") as factory:
        factory.return_value.__enter__.return_value = client
        with pytest.raises(ValueError, match="Person C"):
            step2_loading._fetch_assets(value)
    client.get_videos_for_person_and_date_range.assert_not_called()
    client.get_videos_for_date_range.assert_not_called()


@pytest.mark.parametrize("identity_source", ["fallback", "run", "output"])
def test_same_flat_leaves_different_groups_have_distinct_run_identity(identity_source):
    value = state()
    if identity_source == "run":
        value.active_run_id = "existing-run"
    elif identity_source == "output":
        value.output_path = Path("/tmp/memory.mp4")
    keys = []
    for text in (EXPRESSION, '"Person A" OR ("Person B" AND "Person C")'):
        step1_people._set_grouped_condition(value, text)
        keys.append(
            clip_pipeline._ui_editorial_key(
                value,
                product=value.memory_type,
                date_ranges=tuple(value.date_ranges),
                people=value.person_expression.leaf_values,
                full_sources=(),
            )
        )
    assert keys[0] != keys[1]


def test_context_carries_names_and_grouped_label_not_face_ids():
    value = state()
    step1_people._set_grouped_condition(value, EXPRESSION)
    context = clip_pipeline._build_ui_editorial_context(value, Config(), [], [])
    assert context.person_expression == PersonExpression.parse(EXPRESSION)
    assert context.people == ("Person A", "Person B", "Person C")
    assert context.label == value.person_expression.display_label
    assert "face-" not in json.dumps(context.person_expression.to_dict())


@pytest.mark.parametrize("flat_control", ["people", "match"])
def test_explicit_flat_widget_choice_clears_stale_expression_and_error(flat_control):
    value = state()
    step1_people._set_grouped_condition(value, EXPRESSION)
    with patch.object(step1_people, "ui", MagicMock()) as ui:
        apply = MagicMock()
        step1_people.render_multi_person_params(value, apply)
        if flat_control == "people":
            callback = next(
                call.kwargs["on_change"]
                for call in ui.select.call_args_list
                if call.kwargs.get("label") == "People (select 2+)"
            )
            selected = ["Person A", "Person C"]
        else:
            callback = ui.toggle.call_args.kwargs["on_change"]
            selected = "or"
        value.person_expression_error = "unfinished condition"
        callback(SimpleNamespace(value=selected))
    assert value.person_expression is None
    assert value.person_expression_error is None
    assert apply.called


def test_clearing_grouped_text_removes_the_filter_and_flat_preset_stays_legacy():
    value = state()
    step1_people._set_grouped_condition(value, EXPRESSION)
    step1_people._set_grouped_condition(value, "")
    assert value.person_expression is None
    assert value.person_ids == []
    value.apply_preset(
        create_preset(
            MemoryType.MULTI_PERSON,
            year=2024,
            person_names=["Person B", "Person C"],
            person_match="or",
        )
    )
    assert value.person_expression is None
    assert value.person_match == "or"
    assert value.person_ids == ["face-b", "face-c"]


def test_legacy_single_picker_explicitly_replaces_a_grouped_condition():
    value = state()
    step1_people._set_grouped_condition(value, EXPRESSION)
    with patch.object(step1_config, "ui", MagicMock()) as ui:
        step1_config._render_person_filter(value, MagicMock(), [None])
        select = ui.select.return_value.classes.return_value
        callback = select.on_value_change.call_args.args[0]
        callback(SimpleNamespace(value="face-c"))
    assert value.person_expression is None
    assert value.selected_person.id == "face-c"
    assert value.person_ids == ["face-c"]
    assert clip_pipeline._ui_editorial_people(value) == ("Person C",)
