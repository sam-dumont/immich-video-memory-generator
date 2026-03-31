"""Tests for clip merging — interleaving photo/video types after assembly."""

from __future__ import annotations

from pathlib import Path

from immich_memories.generate import _interleave_clip_types
from immich_memories.processing.assembly_config import AssemblyClip


def _make_assembly_clip(
    asset_id: str,
    date: str = "2021-07-22T14:00:00",
    is_photo: bool = False,
) -> AssemblyClip:
    return AssemblyClip(
        path=Path(f"/tmp/{asset_id}.mp4"),
        duration=5.0,
        date=date,
        asset_id=asset_id,
        is_photo=is_photo,
    )


class TestInterleaveClipTypes:
    """Break up consecutive same-type clip runs."""

    def test_three_consecutive_photos_broken_up(self):
        clips = [
            _make_assembly_clip("p1", "2021-07-22T10:00:00", is_photo=True),
            _make_assembly_clip("p2", "2021-07-22T10:05:00", is_photo=True),
            _make_assembly_clip("p3", "2021-07-22T10:10:00", is_photo=True),
            _make_assembly_clip("v1", "2021-07-22T12:00:00", is_photo=False),
            _make_assembly_clip("v2", "2021-07-22T12:05:00", is_photo=False),
        ]
        result = _interleave_clip_types(clips, max_consecutive=2)

        # No run of 3+ photos
        for i in range(2, len(result)):
            if all(result[j].is_photo for j in range(i - 2, i + 1)):
                raise AssertionError(
                    f"Run of 3 photos at index {i}: {[c.asset_id for c in result]}"
                )

    def test_already_interleaved_unchanged(self):
        clips = [
            _make_assembly_clip("p1", "2021-07-22T10:00:00", is_photo=True),
            _make_assembly_clip("v1", "2021-07-22T11:00:00", is_photo=False),
            _make_assembly_clip("p2", "2021-07-22T12:00:00", is_photo=True),
            _make_assembly_clip("v2", "2021-07-22T13:00:00", is_photo=False),
        ]
        result = _interleave_clip_types(clips, max_consecutive=2)
        assert [c.asset_id for c in result] == ["p1", "v1", "p2", "v2"]

    def test_all_same_type_unchanged(self):
        """When only photos exist, nothing can be interleaved."""
        clips = [
            _make_assembly_clip(f"p{i}", f"2021-07-22T{10 + i}:00:00", is_photo=True)
            for i in range(5)
        ]
        result = _interleave_clip_types(clips, max_consecutive=2)
        assert len(result) == 5

    def test_two_consecutive_allowed(self):
        """max_consecutive=2 means runs of exactly 2 are OK."""
        clips = [
            _make_assembly_clip("p1", is_photo=True),
            _make_assembly_clip("p2", is_photo=True),
            _make_assembly_clip("v1", is_photo=False),
            _make_assembly_clip("v2", is_photo=False),
        ]
        result = _interleave_clip_types(clips, max_consecutive=2)
        assert [c.asset_id for c in result] == ["p1", "p2", "v1", "v2"]

    def test_bretagne_scenario_mixed_blocks(self):
        """4 photos then 4 videos → interleaved so max run ≤ 2."""
        clips = [
            _make_assembly_clip(f"p{i}", f"2021-09-24T{10 + i}:00:00", is_photo=True)
            for i in range(4)
        ] + [
            _make_assembly_clip(f"v{i}", f"2021-09-24T{14 + i}:00:00", is_photo=False)
            for i in range(4)
        ]
        result = _interleave_clip_types(clips, max_consecutive=2)

        max_run = 1
        current_run = 1
        for i in range(1, len(result)):
            if result[i].is_photo == result[i - 1].is_photo:
                current_run += 1
                max_run = max(max_run, current_run)
            else:
                current_run = 1

        assert max_run <= 2, f"Max consecutive run is {max_run}, expected <= 2"

    def test_short_list_unchanged(self):
        clips = [_make_assembly_clip("v1", is_photo=False)]
        result = _interleave_clip_types(clips, max_consecutive=2)
        assert len(result) == 1

    def test_empty_list(self):
        result = _interleave_clip_types([], max_consecutive=2)
        assert result == []
