"""Nested people selection keeps set semantics and bounds without live API calls."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, dataclass
from datetime import UTC, datetime
from unittest.mock import Mock

import pytest

from immich_memories.api.person_expression import (
    MAX_DEPTH,
    MAX_EXPRESSION_CHARACTERS,
    MAX_LEAF_CHARACTERS,
    MAX_LEAVES,
    MAX_NODES,
    PersonExpression,
    parse_person_expression,
)
from immich_memories.api.person_scope import photos_in_window, videos_in_window
from immich_memories.timeperiod import DateRange


def person(value):
    return {"person": value}


def test_nested_sets_and_unique_first_seen_fetches():
    expression = PersonExpression.from_dict(
        {
            "all": [
                {"any": [person("a"), {"all": [person("b"), person("c")]}]},
                {"any": [person("d"), person("a")]},
            ]
        }
    )
    answers = {"a": {1, 2}, "b": {2, 3, 4}, "c": {3, 4}, "d": {3, 5}}
    fetch = Mock(side_effect=lambda leaf: answers[leaf])
    assert expression.evaluate(fetch) == {1, 2, 3}
    assert expression.leaf_values == ("a", "b", "c", "d")
    assert [call.args[0] for call in fetch.call_args_list] == ["a", "b", "c", "d"]


def test_roundtrip_is_immutable_and_independent_of_input_objects():
    source = {"all": [{"any": [person("a"), person("b")]}, person("c")]}
    expression = PersonExpression.from_dict(source)
    assert PersonExpression.from_dict(json.loads(json.dumps(expression.to_dict()))) == expression
    assert hash(expression) == hash(PersonExpression.from_dict(source))
    source["all"].clear()
    exported = expression.to_dict()
    exported["all"].clear()
    assert expression.leaf_values == ("a", "b", "c")
    with pytest.raises(FrozenInstanceError):
        expression.value = "changed"


def test_merged_face_ids_replace_a_leaf_with_any_without_flattening_and():
    expression = PersonExpression.parse('("Parent A" OR "Parent B") AND "Child"')
    merged = {
        "Parent A": {"any": [person("a-young"), person("a-adult")]},
        "Parent B": person("b"),
        "Child": {"any": [person("child-infant"), person("child-current")]},
    }
    resolved = expression.map_leaves(lambda value: PersonExpression.from_dict(merged[value]))
    groups = {
        "a-young": {"together"},
        "a-adult": {"adult-alone"},
        "b": {"b-with-child"},
        "child-infant": {"together"},
        "child-current": {"b-with-child", "child-alone"},
    }
    assert resolved.evaluate(groups.__getitem__) == {"together", "b-with-child"}
    assert resolved.to_dict()["all"][0]["any"][0] == merged["Parent A"]


def test_mapping_resolves_each_name_once_and_can_coalesce_shared_ids():
    expression = PersonExpression.parse('"A" OR ("A" AND "B")')
    resolve = Mock(return_value="merged-face")
    resolved = expression.map_leaves(resolve)
    assert [call.args[0] for call in resolve.call_args_list] == ["A", "B"]
    fetch = Mock(return_value={"asset"})
    assert resolved.evaluate(fetch) == {"asset"}
    fetch.assert_called_once_with("merged-face")


@pytest.mark.parametrize(
    "bad",
    [
        None,
        [],
        {},
        {"not": person("a")},
        {"person": "a", "any": [person("b")]},
        {"person": []},
        {"person": ""},
        {"person": "  "},
        {"all": []},
        {"any": []},
        {"all": ["a"]},
        {"any": (person("a"),)},
        {"person": "x" * (MAX_LEAF_CHARACTERS + 1)},
    ],
)
def test_strict_ast_rejects_invalid_nodes(bad):
    with pytest.raises(ValueError):
        PersonExpression.from_dict(bad)


def test_limits_reject_deep_cyclic_and_large_ast_before_fetching():
    deep = person("a")
    for _ in range(MAX_DEPTH):
        deep = {"all": [deep]}
    cyclic = {"all": []}
    cyclic["all"].append(cyclic)
    for data in (
        deep,
        cyclic,
        {"any": [person("a")] * MAX_NODES},
        {"any": [person(str(i)) for i in range(MAX_LEAVES + 1)]},
    ):
        with pytest.raises(ValueError):
            PersonExpression.from_dict(data)


def test_mapping_cannot_expand_beyond_global_leaf_limit():
    expression = PersonExpression.parse('"A" AND "B"')
    with pytest.raises(ValueError, match="leaf limit"):
        expression.map_leaves(
            lambda name: PersonExpression.from_dict(
                {"any": [person(name + str(i)) for i in range(MAX_LEAVES)]}
            )
        )


@pytest.mark.parametrize(
    "args",
    [
        ("unknown", None, ()),
        ("person", "a", []),
        ("all", None, ()),
        ("all", "unexpected", (PersonExpression("person", value="a"),)),
        ("all", None, ("not-an-expression",)),
    ],
)
def test_direct_constructor_cannot_bypass_immutable_ast_contract(args):
    with pytest.raises(ValueError):
        PersonExpression(*args)


def test_and_precedence_and_explicit_parentheses_are_distinct():
    a = parse_person_expression('"A" OR "B" AND "C"')
    b = parse_person_expression('("A" OR "B") AND "C"')
    groups = {"A": {"A-only"}, "B": {"BC"}, "C": {"BC"}}
    assert a.evaluate(groups.__getitem__) == {"A-only", "BC"}
    assert b.evaluate(groups.__getitem__) == {"BC"}
    assert a.display_label == '("A" OR ("B" AND "C"))'
    assert b.display_label == '(("A" OR "B") AND "C")'
    assert PersonExpression.parse(b.display_label) == b


def test_json_escaping_and_operator_words_inside_names_remain_literal():
    names = ['Person "Quoted"', "Back\\Slash", "AND OR (team)", "Zoë\nLine"]
    text = " or ".join(json.dumps(name) for name in names)
    expression = PersonExpression.parse(text)
    assert expression.leaf_values == tuple(names)
    assert PersonExpression.parse(expression.display_label) == expression


@pytest.mark.parametrize(
    "text",
    [
        "",
        " ",
        '"a" "b"',
        '"a" AND',
        'AND "a"',
        '"a" NOT "b"',
        'a OR "b"',
        '"a" XOR "b"',
        '("a"',
        '"a")',
        "()",
        "'a'",
        '"bad\\q"',
        '"a"; print(1)',
        '"__import__(\\"os\\")"()',
        '""',
        "(" * (MAX_DEPTH + 1) + '"a"' + ")" * (MAX_DEPTH + 1),
        " " * (MAX_EXPRESSION_CHARACTERS + 1),
    ],
)
def test_parser_rejects_invalid_or_unsafe_syntax(text):
    with pytest.raises(ValueError):
        PersonExpression.parse(text)


@dataclass(frozen=True)
class FetchedAsset:
    id: str
    file_created_at: datetime
    media_type: str


WINDOW = DateRange(datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 12, 31, tzinfo=UTC))


@pytest.mark.parametrize(
    "media_type,fetcher", [("photo", photos_in_window), ("video", videos_in_window)]
)
def test_expression_fetches_each_face_once_preserves_objects_and_sorts(media_type, fetcher):
    early = FetchedAsset("early", WINDOW.start, media_type)
    late_b = FetchedAsset("late-b", WINDOW.end, media_type)
    late_a = FetchedAsset("late-a", WINDOW.end, media_type)
    excluded = FetchedAsset("parent-alone", WINDOW.start, media_type)
    groups = {"a": [late_b, early, excluded], "b": [late_a, late_b], "c": [late_b, late_a, early]}
    client = Mock()
    client.get_photos_for_date_range.side_effect = lambda _date_range, **kw: groups[kw["person_id"]]
    client.get_videos_for_person_and_date_range.side_effect = lambda face, _date_range: groups[face]
    expression = PersonExpression.parse('("a" OR "b" OR "a") AND "c"')
    selected = fetcher(client, [], WINDOW, person_expression=expression)
    assert selected == [early, late_a, late_b]
    assert selected[0] is early
    if media_type == "photo":
        calls = client.get_photos_for_date_range.call_args_list
        assert [call.kwargs for call in calls] == [
            {"person_id": value} for value in ("a", "b", "c")
        ]
        assert all(call.args == (WINDOW,) for call in calls)
    else:
        assert [
            call.args for call in client.get_videos_for_person_and_date_range.call_args_list
        ] == [(value, WINDOW) for value in ("a", "b", "c")]
        client.get_videos_for_all_persons.assert_not_called()
        client.get_videos_for_date_range.assert_not_called()


@pytest.mark.parametrize("fetcher", [photos_in_window, videos_in_window])
def test_empty_intersection_does_not_fall_back_to_whole_window(fetcher):
    client = Mock()
    client.get_photos_for_date_range.return_value = []
    client.get_videos_for_person_and_date_range.return_value = []
    assert (
        fetcher(client, [], WINDOW, person_expression=PersonExpression.parse('"a" AND "b"')) == []
    )
    client.get_videos_for_date_range.assert_not_called()
    client.get_videos_for_all_persons.assert_not_called()


@pytest.mark.parametrize("fetcher", [photos_in_window, videos_in_window])
def test_mixed_flat_and_nested_scope_is_rejected_before_fetch(fetcher):
    client = Mock()
    with pytest.raises(ValueError, match="nonempty person_ids"):
        fetcher(client, ["other"], WINDOW, person_expression=PersonExpression.parse('"a"'))
    assert client.mock_calls == []


@pytest.mark.parametrize(
    "ids,match", [([], "and"), (["a"], "and"), (["a", "b"], "and"), (["a", "b"], "or")]
)
def test_flat_photo_endpoint_arguments_are_unchanged(ids, match):
    client = Mock()
    client.get_photos_for_date_range.return_value = []
    photos_in_window(client, ids, WINDOW, person_match=match)
    expected = (
        [{"person_id": "a"}, {"person_id": "b"}]
        if match == "or"
        else [
            {
                "person_id": ids[0] if len(ids) == 1 else None,
                "person_ids": ids if len(ids) > 1 else None,
            }
        ]
    )
    assert [call.kwargs for call in client.get_photos_for_date_range.call_args_list] == expected


@pytest.mark.parametrize(
    "ids,match,method,args",
    [
        ([], "and", "get_videos_for_date_range", [(WINDOW,)]),
        (["a"], "and", "get_videos_for_person_and_date_range", [("a", WINDOW)]),
        (["a", "b"], "and", "get_videos_for_all_persons", [(["a", "b"], WINDOW)]),
        (["a", "b"], "or", "get_videos_for_person_and_date_range", [("a", WINDOW), ("b", WINDOW)]),
    ],
)
def test_flat_video_endpoint_choices_are_unchanged(ids, match, method, args):
    client = Mock()
    getattr(client, method).return_value = []
    videos_in_window(client, ids, WINDOW, person_match=match)
    assert [call.args for call in getattr(client, method).call_args_list] == args
    assert len(client.mock_calls) == len(args)
