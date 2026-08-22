"""A burst merge must not decimate the clips it merges.

Concat needs one frame rate across its inputs, and that rate was pinned to 30
with a comment that iPhone Live Photos always are. Probing this library's own
cache found components at 23.94, 29.97, 120 and 240 fps, so the pin silently
threw away three quarters of a 120 fps clip.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from immich_memories.processing.live_photo_merger import build_merge_command, burst_fps


def test_a_burst_normalises_to_its_fastest_clip() -> None:
    # WHY: ffprobe is the external boundary — no fixture files at four rates.
    with patch(
        "immich_memories.processing.live_photo_merger.probe_clip_fps",
        side_effect=[29.97, 120.0, 23.94],
    ):
        assert burst_fps([Path("a.mov"), Path("b.mov"), Path("c.mov")]) == 120.0


def test_an_unreadable_clip_does_not_drag_the_burst_down() -> None:
    # WHY: same boundary; None is what a failed probe returns.
    with patch(
        "immich_memories.processing.live_photo_merger.probe_clip_fps",
        side_effect=[None, 60.0],
    ):
        assert burst_fps([Path("a.mov"), Path("b.mov")]) == 60.0


def test_falls_back_when_nothing_can_be_probed() -> None:
    # WHY: same boundary; every probe failing is the degraded case.
    with patch("immich_memories.processing.live_photo_merger.probe_clip_fps", return_value=None):
        assert burst_fps([Path("a.mov")]) == 30.0


def test_the_merge_command_carries_the_measured_rate() -> None:
    # WHY: three ffprobe boundaries — fps, audio and HDR detection.
    with (
        patch("immich_memories.processing.live_photo_merger.probe_clip_fps", return_value=60.0),
        patch(
            "immich_memories.processing.live_photo_merger.probe_clip_has_audio",
            return_value=False,
        ),
        patch("immich_memories.processing.live_photo_merger._detect_clip_hdr", return_value=False),
    ):
        cmd = build_merge_command(
            [Path("a.mov"), Path("b.mov")],
            [(0.0, 1.0), (0.0, 1.0)],
            Path("out.mp4"),
        )

    filters = " ".join(cmd)
    assert "fps=60" in filters
    assert "fps=30" not in filters


def test_a_single_clip_is_not_resampled_at_all() -> None:
    """Nothing to concat, so nothing to normalise — leave the source alone."""
    # WHY: two ffprobe boundaries; the fps probe must not even be needed here.
    with (
        patch(
            "immich_memories.processing.live_photo_merger.probe_clip_has_audio",
            return_value=False,
        ),
        patch("immich_memories.processing.live_photo_merger._detect_clip_hdr", return_value=False),
    ):
        cmd = build_merge_command([Path("a.mov")], [(0.0, 1.0)], Path("out.mp4"))

    assert "fps=" not in " ".join(cmd)
