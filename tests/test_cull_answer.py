"""Strict parsing for the two Cull buckets, asked inside each episode's scope."""

from __future__ import annotations

import json

import pytest


def test_the_bucket_derives_the_reason_so_model_prose_never_actuates() -> None:
    """A rejection's human explanation is local data, not text the model supplied."""
    from immich_memories.analysis.cull_answer import CullDecision

    assert CullDecision("asset", "notes").reason == "taken as a note rather than as a moment"
    assert CullDecision("asset", "failed").reason == "the picture did not come out"


@pytest.mark.parametrize(
    "bucket",
    (
        "A relative alternative is stronger.",
        "This is an uninteresting selfie.",
        "repetitive",
        "ordinary",
        "NOTES",
        "",
    ),
)
def test_only_the_two_known_buckets_can_remove_a_visual(bucket: str) -> None:
    """Cull may sort into buckets; it may not invent a reason to reject something."""
    from immich_memories.analysis.cull_answer import CullDecision

    with pytest.raises(ValueError, match="known bucket"):
        CullDecision("asset", bucket)


def test_a_decision_needs_a_stable_asset() -> None:
    """A fate with nothing to attach to is not a fate."""
    from immich_memories.analysis.cull_answer import CullDecision

    with pytest.raises(ValueError, match="stable asset"):
        CullDecision("   ", "notes")


def test_reads_two_buckets_inside_each_episode_scope() -> None:
    """The unit of the question decides whether a small model can answer it.

    Probed on one real 57-tile pack, three repeats each: a flat answer over the
    whole pack parsed once in three and gave fifty-five of fifty-seven tiles the
    same label. The same question asked inside each episode's own scope parsed
    three times in three and returned identical tiles every run.
    """
    from immich_memories.analysis.cull_answer import read_cull_namespaces

    parsed = read_cull_namespaces(
        json.dumps(
            {
                "schema_version": "episode-scan-v4",
                "pack": 1,
                "cull_rejects": [
                    {"episode": 1, "notes": [1], "failed": [2]},
                    {"episode": 2, "notes": [], "failed": []},
                ],
            }
        ),
        pack_alias=1,
        tile_map={1: "a-screen", 2: "a-smeared-frame", 3: "a-moment"},
        episode_tiles={1: (1, 2), 2: (3,)},
    )

    assert parsed is not None
    assert parsed.cull_valid
    assert {d.asset_id: d.reason for d in parsed.cull_rejects} == {
        "a-screen": "taken as a note rather than as a moment",
        "a-smeared-frame": "the picture did not come out",
    }


def test_an_empty_answer_is_a_valid_answer_that_removes_nothing() -> None:
    """Most tiles are neither junk nor failed, so empty is the normal reply."""
    from immich_memories.analysis.cull_answer import read_cull_namespaces

    parsed = read_cull_namespaces(
        json.dumps(
            {
                "schema_version": "episode-scan-v4",
                "pack": 1,
                "cull_rejects": [{"episode": 1, "notes": [], "failed": []}],
            }
        ),
        pack_alias=1,
        tile_map={1: "kept"},
        episode_tiles={1: (1,)},
    )

    assert parsed is not None
    assert parsed.cull_valid
    assert parsed.cull_rejects == ()


@pytest.mark.parametrize(
    "rejects",
    (
        # a tile belonging to another episode: the answer stopped tracking scope
        [{"episode": 1, "notes": [3], "failed": []}],
        # a tile nothing on this sheet shows
        [{"episode": 1, "notes": [99], "failed": []}],
        # an episode this pack does not have
        [{"episode": 7, "notes": [1], "failed": []}],
        # the same episode answered twice
        [
            {"episode": 1, "notes": [1], "failed": []},
            {"episode": 1, "notes": [2], "failed": []},
        ],
        # the same tile in both buckets
        [{"episode": 1, "notes": [1], "failed": [1]}],
        # a boolean is not a tile alias
        [{"episode": 1, "notes": [True], "failed": []}],
        # an unexpected key
        [{"episode": 1, "notes": [], "failed": [], "why": "blurry"}],
        # a bucket that is not a list
        [{"episode": 1, "notes": 1, "failed": []}],
    ),
)
def test_an_answer_that_left_its_scope_removes_nothing(rejects: list[dict[str, object]]) -> None:
    """Scope is the whole mechanism; failing open here is what makes Cull safe."""
    from immich_memories.analysis.cull_answer import read_cull_namespaces

    parsed = read_cull_namespaces(
        json.dumps({"schema_version": "episode-scan-v4", "pack": 1, "cull_rejects": rejects}),
        pack_alias=1,
        tile_map={1: "a", 2: "b", 3: "c"},
        episode_tiles={1: (1, 2), 2: (3,)},
    )

    assert parsed is not None
    assert not parsed.cull_valid
    assert parsed.cull_rejects == ()


@pytest.mark.parametrize(
    "raw",
    (
        "",
        "I cannot help with that.",
        '{"schema_version":"episode-scan-v4","pack":1,"cull_rejects":[{"episode":1,',
        # a stale schema: v3 answers must never be reinterpreted as v4
        '{"schema_version":"episode-scan-v3","pack":1,"cull_rejects":[]}',
        # another pack's answer
        '{"schema_version":"episode-scan-v4","pack":2,"cull_rejects":[]}',
        # no pack alias at all
        '{"schema_version":"episode-scan-v4","cull_rejects":[]}',
    ),
)
def test_a_malformed_or_foreign_envelope_has_no_decisions_at_all(raw: str) -> None:
    """Refusal, truncation, a stale schema or another pack's answer kills nothing."""
    from immich_memories.analysis.cull_answer import read_cull_namespaces

    assert (
        read_cull_namespaces(
            raw,
            pack_alias=1,
            tile_map={1: "a"},
            episode_tiles={1: (1,)},
        )
        is None
    )


def test_a_decision_about_pixels_nobody_could_see_is_not_a_decision() -> None:
    """An unreadable tile is not a tile nobody looked at, and cannot be actuated."""
    from immich_memories.analysis.cull_answer import read_cull_namespaces

    parsed = read_cull_namespaces(
        json.dumps(
            {
                "schema_version": "episode-scan-v4",
                "pack": 1,
                "cull_rejects": [{"episode": 1, "notes": [1], "failed": [2]}],
            }
        ),
        pack_alias=1,
        tile_map={1: "unreadable", 2: "visible"},
        episode_tiles={1: (1, 2)},
        unavailable_asset_ids=frozenset({"unreadable"}),
    )

    assert parsed is not None
    assert parsed.cull_valid
    assert tuple(d.asset_id for d in parsed.cull_rejects) == ("visible",)
    assert parsed.warnings == ("!! unavailable Cull decision: unreadable",)


def _wire_objects_in(prompt: str) -> tuple[dict[str, object], ...]:
    """Every complete JSON object the prompt shows the model."""
    decoder = json.JSONDecoder()
    found: list[dict[str, object]] = []
    for index, character in enumerate(prompt):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(prompt, index)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            found.append(value)
    return tuple(found)


def test_the_prompt_shows_the_exact_shape_its_parser_accepts() -> None:
    """Prose asks for a shape; the parser demands one; nothing keeps them equal.

    Measured against the local model: the prompt asked for "function ... and
    visible reason", the model sent "visible_reason", the parser required
    "reason", and every real record namespace was voided on every answer.
    """
    from immich_memories.analysis.cull_answer import read_cull_namespaces
    from immich_memories.analysis.episode_scan_request import episode_response_shape

    shown = _wire_objects_in(episode_response_shape(episode_alias=1, tile=1))
    envelope = next(item for item in shown if "cull_rejects" in item)

    parsed = read_cull_namespaces(
        json.dumps(envelope),
        pack_alias=1,
        tile_map={1: "asset"},
        episode_tiles={1: (1,)},
    )

    assert parsed is not None
    assert parsed.cull_valid
    # The shown envelope decides nothing, which is the honest default.
    assert parsed.cull_rejects == ()
