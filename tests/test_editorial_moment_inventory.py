"""Representation repairs preserve the inventory's complete-source requirement."""

import json

import pytest

from immich_memories.analysis.editorial_moment_inventory import (
    DepictedMoment,
    read_inventory_page,
)


def page(raw, *, new_ids=("U0002", "U0003")):
    return read_inventory_page(
        raw,
        new_ids=set(new_ids),
        existing={"K0001": DepictedMoment("K0001", "S0001", "A visit", "U0001")},
        units={"U0001": {}, "U0002": {}, "U0003": {"favourite": True}},
    )


@pytest.mark.parametrize("fenced", [False, True])
def test_complete_outer_array_preserves_groups_alternatives_and_favourite(fenced):
    rows = [
        {
            "same_as": "K0001",
            "sources": ["U0002", "U0003"],
            "primary": "U0002",
            "content": "Family at a visit",
        }
    ]
    raw = json.dumps(rows)
    if fenced:
        raw = f"```json\n{raw}\n```"
    assert page(raw) == page(json.dumps({"moments": rows}))
    assert page(raw) == [("K0001", "U0003", ["U0001", "U0002"], "Family at a visit")]


@pytest.mark.parametrize("fenced", [False, True])
def test_outer_array_with_missing_sources_still_fails_coverage(fenced):
    raw = json.dumps(
        [{"same_as": None, "sources": ["U0002"], "primary": "U0002", "content": "A visit"}]
    )
    if fenced:
        raw = f"```json\n{raw}\n```"
    with pytest.raises(ValueError, match="coverage is incomplete"):
        page(raw)


def test_incomplete_outer_array_is_not_completed_from_its_first_object():
    raw = '[{"same_as":null,"sources":["U0002","U0003"],"primary":"U0002","content":"A visit"}'
    with pytest.raises(ValueError):
        page(raw)


def test_outer_array_retains_every_separate_moment():
    rows = [
        {"same_as": "K0001", "sources": ["U0002"], "primary": "U0001", "content": "A visit"},
        {"same_as": None, "sources": ["U0003"], "primary": "U0003", "content": "A later walk"},
    ]
    assert page(json.dumps(rows)) == [
        ("K0001", "U0001", ["U0002"], "A visit"),
        (None, "U0003", [], "A later walk"),
    ]


def test_outer_array_with_unknown_sources_cannot_satisfy_coverage():
    rows = [{"sources": ["U0002", "U9999"], "primary": "U0002", "content": "A visit"}]
    with pytest.raises(ValueError, match="coverage is incomplete"):
        page(json.dumps(rows))


def test_moment_facts_are_reused_across_films_but_changed_evidence_is_read_again(
    tmp_path, monkeypatch
):
    from immich_memories.analysis import editorial_text_gateway as gateway
    from immich_memories.analysis.editorial_moment_inventory import inventory_event
    from immich_memories.analysis.editorial_structure_io import StructureTextJudge
    from immich_memories.config_loader import Config

    sent = []

    async def answer(prompt, _config, **_):
        sent.append(prompt)
        return json.dumps(
            {
                "moments": [
                    {
                        "sources": ["U0001", "U0002"],
                        "primary": "U0001",
                        "content": "Two people share a cake.",
                    }
                ]
            }
        )

    # WHY: only the provider transport is fake; the production inventory,
    # request identity, answer bank and source remapping are real.
    monkeypatch.setattr(gateway, "query_llm", answer)
    config = Config(
        llm={
            "provider": "openai-compatible",
            "model": "test-model",
            "base_url": "http://reader.test/v1",
        }
    )
    units = [
        {
            "asset_id": f"picture-{i}",
            "moment": "M001",
            "taken": f"2030-02-05T12:00:0{i}",
            "facts": "Two people share a cake.",
        }
        for i in range(2)
    ]

    def run(name, sources, context, people=""):
        (tmp_path / name).mkdir()
        judge = StructureTextJudge(config, tmp_path / name, cache_path=tmp_path / "facts.sqlite")
        moments, _ = inventory_event(
            judge,
            event=name,
            units=sources,
            context=context,
            line=lambda unit: unit["facts"],
            people_context=people,
        )
        return moments, judge

    original, cold = run("month", units, "A birthday in February")
    relabelled = [unit | {"moment": "M147"} for unit in units]
    repeated, warm = run("year", relabelled, "A year of family celebrations")

    assert len(sent) == 1
    assert cold.calls[0]["cache_hit"] is False
    assert warm.calls[0]["cache_hit"] is True
    assert original[0].sources == repeated[0].sources == ["picture-0", "picture-1"]
    assert repeated[0].event == "year"
    _, changed = run(
        "updated",
        [relabelled[0] | {"facts": "Two people cut a cake."}, relabelled[1]],
        "A year of family celebrations",
    )
    assert len(sent) == 2
    assert changed.calls[0]["cache_hit"] is False
    _, corrected = run(
        "corrected",
        units,
        "A birthday in February",
        people="P1:name=Taylor Example|relationship=friend|source=confirmed",
    )
    assert len(sent) == 3
    assert corrected.calls[0]["cache_hit"] is False
    assert "relationship=friend|source=confirmed" in sent[-1]
