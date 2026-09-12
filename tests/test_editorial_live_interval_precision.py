"""Concatenation coordinates cannot grow an actual component's source bounds."""

from random import Random

import pytest

from immich_memories.processing.live_material import LiveRenderMaterial, LiveSourceEntry


def test_whole_material_preserves_exact_source_endpoints():
    rng = Random(17)
    for _ in range(300):
        entries = []
        for index in range(4):
            start = rng.randrange(0, 1000) / 1000
            entries.append(
                LiveSourceEntry(
                    f"s{index}",
                    f"v{index}",
                    float(index),
                    start,
                    start + rng.randrange(1, 3000) / 1000,
                )
            )
        material = LiveRenderMaterial(tuple(entries))
        assert material.displayed_interval(0, material.duration_seconds) == tuple(entries)


def test_partial_projection_stays_inside_exact_source_bounds():
    entries = (
        LiveSourceEntry("first", "v0", 0.0, 0.826, 2.069),
        LiveSourceEntry("second", "v1", 1.0, 0.677, 1.817),
    )
    material = LiveRenderMaterial(entries)
    start, end = 0.1, material.duration_seconds - 0.2
    projected = material.displayed_interval(start, end)
    assert projected[0].start == pytest.approx(entries[0].start + 0.1)
    assert projected[0].end == entries[0].end
    assert projected[1].start == entries[1].start
    assert projected[1].end == pytest.approx(entries[1].end - 0.2)
    assert sum(row.end - row.start for row in projected) == pytest.approx(end - start)
    for source, shown in zip(entries, projected, strict=True):
        assert source.start <= shown.start < shown.end <= source.end
