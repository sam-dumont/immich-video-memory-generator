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


def test_foreign_media_is_a_bucket_of_its_own() -> None:
    """2007's stock opener and archival closer: files saved into the library, never
    taken by or of this life. Provenance gates are structurally blind to them (real
    camera exif on stock; the historical bypass on old clips) — the cull question is
    the layer that can see it."""
    from immich_memories.analysis.cull_answer import CULL_BUCKETS, CullDecision

    decision = CullDecision("asset", "foreign")
    assert "this life" in decision.reason or "saved" in decision.reason
    assert "foreign" in CULL_BUCKETS
