"""Empty Live slices retain source lineage without becoming displayed footage.

The two source-projection cases arrive with the slice that ports
`selection_source`, `editorial_source_route` and `editorial_live_render`.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from immich_memories.processing.live_material import LiveRenderMaterial, LiveSourceEntry


@pytest.mark.parametrize(
    "field,value",
    [
        ("start", -1),
        ("start", float("nan")),
        ("end", float("inf")),
        ("end", -1),
        ("end", True),
        ("shutter_timestamp", float("nan")),
        ("video_id", ""),
        ("still_id", ""),
    ],
)
def test_invalid_source_entries_reject_before_becoming_a_manifest(field, value):
    with pytest.raises(ValueError):
        replace(LiveSourceEntry("a", "v", 1, 0, 2), **{field: value})


@pytest.mark.parametrize(
    "entries",
    [
        (LiveSourceEntry("a", "v", 1, 1, 1),),
        (LiveSourceEntry("a", "v", 1, 0, 1), LiveSourceEntry("b", "v", 1, 1, 2)),
        (LiveSourceEntry("a", "v", 1, 1, 1), LiveSourceEntry("b", "v", 2, 1, 2)),
        (LiveSourceEntry("a", "v", 1, 0, 1), LiveSourceEntry("a", "v2", 2, 1, 2)),
    ],
)
def test_no_motion_or_repeated_positive_conflicts_are_not_silently_repaired(entries):
    with pytest.raises(ValueError):
        LiveRenderMaterial(entries)


def test_selected_interval_uses_exact_segments_not_an_entire_last_companion():
    material = LiveRenderMaterial(
        (
            LiveSourceEntry("a", "v1", 1, 0, 2),
            LiveSourceEntry("b", "v2", 2, 1, 3),
        )
    )
    assert material.displayed_interval(1.5, 2.5) == (
        LiveSourceEntry("a", "v1", 1, 1.5, 2),
        LiveSourceEntry("b", "v2", 2, 1, 1.5),
    )
    for start, end in ((0, 5), (-1, 1), (0, 0), (0, float("inf"))):
        with pytest.raises(ValueError):
            material.displayed_interval(start, end)


def test_only_exact_declared_centisecond_rounding_can_adjust_live_source_end():
    material = LiveRenderMaterial((LiveSourceEntry("a", "v", 1, 0, 1.826),))
    assert material.selected_interval(1.83, raw_seconds=1.83) == (0, 1.826)
    assert material.selected_interval(1, start=0.5) == (0.5, 1.5)
    for kwargs in (
        {},
        {"raw_seconds": 1.82},
        {"start": 0.01, "raw_seconds": 1.83},
        {"end": 1.826, "raw_seconds": 1.83},
    ):
        with pytest.raises(ValueError):
            material.selected_interval(1.83, **kwargs)
