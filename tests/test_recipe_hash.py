"""A render is named after its recipe, so re-running the same one replaces it."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from immich_memories.filename_builder import apply_recipe_hash, recipe_hash

RECIPE = {
    "memory_type": "monthly_highlights",
    "date_start": date(2026, 6, 1),
    "date_end": date(2026, 6, 30),
    "target_duration": 60.0,
    "clips": [("asset-a", 0.0, 4.0), ("asset-b", 2.5, 7.25)],
    "extras": {"music": "auto", "container": "mp4"},
}


def test_the_same_recipe_hashes_the_same_way() -> None:
    assert recipe_hash(**RECIPE) == recipe_hash(**RECIPE)


def test_a_different_clip_changes_the_hash() -> None:
    other = {**RECIPE, "clips": [("asset-a", 0.0, 4.0), ("asset-c", 2.5, 7.25)]}
    assert recipe_hash(**other) != recipe_hash(**RECIPE)


def test_clip_order_changes_the_hash() -> None:
    """Order is the edit. Two videos with the same clips in a different sequence
    are different videos."""
    other = {**RECIPE, "clips": list(reversed(RECIPE["clips"]))}
    assert recipe_hash(**other) != recipe_hash(**RECIPE)


def test_sub_frame_float_noise_does_not_change_the_hash() -> None:
    """Segment boundaries are floats. A rerun that lands 3 ms apart is the same
    edit and must not produce a second file."""
    other = {**RECIPE, "clips": [("asset-a", 0.0, 4.001), ("asset-b", 2.4999, 7.25)]}
    assert recipe_hash(**other) == recipe_hash(**RECIPE)


def test_applying_the_hash_is_idempotent() -> None:
    """Re-running must overwrite one file, not accumulate _a1b2_c3d4_e5f6."""
    first = apply_recipe_hash(Path("/out/all_monthly_20260601-20260630.mp4"), "a1b2c3d4")
    again = apply_recipe_hash(first, "a1b2c3d4")
    swapped = apply_recipe_hash(first, "9f8e7d6c")

    assert first.name == "all_monthly_20260601-20260630_a1b2c3d4.mp4"
    assert again == first
    assert swapped.name == "all_monthly_20260601-20260630_9f8e7d6c.mp4"
