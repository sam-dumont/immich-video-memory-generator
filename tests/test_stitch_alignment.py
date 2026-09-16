"""Content-aligned Live stitch windows are measured from the files, not guessed.

The metadata plan puts each companion's in-file shutter at the file midpoint;
measured pre-shutter leads on real bursts differ by up to 0.8 s, and a join's
visible error is the difference of two files' errors (#1012). These contracts
cover the measurement and the re-placed windows.
"""

import subprocess

import numpy as np
import pytest

from immich_memories.processing.stitch_alignment import (
    aligned_trims,
    companion_frames,
    pairwise_clock_offset,
)


def _moving_frames(count: int, *, offset: int = 0, size: tuple[int, int] = (36, 64)) -> np.ndarray:
    """A textured frame translating one column per frame, like handheld footage.

    The whole scene moves, so every pixel disagrees at any misaligned offset —
    the far-offset errors that a featureless background would leave cheap.
    """
    rng = np.random.default_rng(7)
    base = (rng.random(size) * 200 + 20).astype(np.float32)
    return np.stack([np.roll(base, index + offset, axis=1) for index in range(count)])


def test_pairwise_clock_offset_recovers_how_much_later_a_file_started() -> None:
    early = _moving_frames(40)
    late = _moving_frames(30, offset=9)  # started 9 frames = 0.6 s later

    measured = pairwise_clock_offset(early, late)

    assert measured is not None
    assert measured.seconds == pytest.approx(0.6, abs=1 / 15)


def test_pairwise_clock_offset_refuses_an_ambiguous_match() -> None:
    flat = np.full((20, 36, 64), 128.0)  # featureless: every offset matches equally

    assert pairwise_clock_offset(flat, flat) is None


def test_pairwise_clock_offset_refuses_content_that_never_matches() -> None:
    noise_a = np.random.default_rng(1).integers(0, 255, (20, 36, 64)).astype(np.float32)
    noise_b = np.random.default_rng(2).integers(0, 255, (20, 36, 64)).astype(np.float32)

    assert pairwise_clock_offset(noise_a, noise_b) is None


def test_the_francorchamps_race_burst_aligns_to_continuous_content() -> None:
    """The measured burst: three files, 3.2 s of continuous content, plan claimed 4.42 s."""
    trims = [(0.0, 2.288), (1.3, 2.545), (0.8835, 1.767)]
    durations = [2.4, 2.6, 1.767]
    measured = [0.600, 0.833]  # frame-correlated from the real companions

    aligned = aligned_trims(trims, durations, measured)

    assert aligned is not None
    assert [round(b, 3) for b, _ in aligned] == [0.0, 1.058, 1.127]
    assert [round(e, 3) for _, e in aligned] == [1.658, 1.960, 1.767]
    # Every join is content-continuous: end_i = start_{i+1} + delta_i.
    for index, delta in enumerate(measured):
        assert aligned[index][1] == pytest.approx(aligned[index + 1][0] + delta, abs=1e-9)
    # Each still's measured in-file moment stays inside its window.
    for (start, end), anchor in zip(aligned, [0.867, 1.567, 1.700], strict=True):
        assert start <= anchor <= end


def test_alignment_keeps_planned_lengths_when_the_files_hold_them() -> None:
    trims = [(0.0, 1.0), (1.5, 2.5)]
    durations = [3.0, 3.0]

    aligned = aligned_trims(trims, durations, [0.5])

    assert aligned == [(0.0, 1.0), (0.5, 1.5)]


def test_alignment_stands_down_when_files_barely_overlap() -> None:
    """A measured delta past the previous window's end would need content before file start."""
    trims = [(0.0, 2.288), (0.0, 1.245)]
    durations = [2.4, 2.6]

    assert aligned_trims(trims, durations, [3.0]) is None


def test_alignment_requires_one_delta_per_join() -> None:
    with pytest.raises(ValueError, match="one measured delta per join"):
        aligned_trims([(0.0, 1.0)], [1.0], [0.5, 0.5])


def test_companion_frames_decodes_real_bytes(tmp_path) -> None:
    silent = tmp_path / "tiny.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=64x36:rate=15:duration=0.5",
            "-pix_fmt",
            "yuv420p",
            "-y",
            str(silent),
        ],
        check=True,
        capture_output=True,
    )
    frames = companion_frames(silent.read_bytes())

    assert frames.ndim == 3
    assert frames.shape[1:] == (36, 64)
    assert frames.shape[0] >= 5


def test_companion_frames_refuse_bytes_that_cannot_decode() -> None:
    from immich_memories.processing.stitch_alignment import CompanionUndecodable

    with pytest.raises(CompanionUndecodable):
        companion_frames(b"not a video")


def test_the_cluster_consumes_measured_deltas_and_falls_back_cleanly() -> None:
    from immich_memories.processing.live_photo_merger import LivePhotoCluster

    def still(key, seconds):
        from tests.conftest import make_asset
        from datetime import UTC, datetime, timedelta

        return make_asset(key, file_created_at=datetime(2024, 6, 4, 12, tzinfo=UTC) + timedelta(seconds=seconds))

    cluster = LivePhotoCluster(
        assets=[still("a", 0), still("b", 1.088), still("c", 2.333)],
        clip_durations={"a": 2.4, "b": 2.6, "c": 1.767},
    )
    metadata_plan = cluster.trim_points()
    assert len(metadata_plan) == 3

    aligned = cluster.trim_points(measured_deltas=[0.600, 0.833])
    assert aligned != metadata_plan
    assert aligned is not None and len(aligned) == 3
    # The joins are content-continuous under the measured clocks.
    assert aligned[0][1] == pytest.approx(aligned[1][0] + 0.600, abs=1e-9)
    assert aligned[1][1] == pytest.approx(aligned[2][0] + 0.833, abs=1e-9)
    # Unmeasurable pairs keep the metadata plan rather than guessing.
    assert cluster.trim_points(measured_deltas=[3.0, 0.833]) == metadata_plan


def test_metadata_clock_deltas_reproduce_the_midpoint_model() -> None:
    from immich_memories.processing.stitch_alignment import metadata_clock_deltas

    # Francorchamps: shutters 1.088 s and 1.245 s apart, files 2.4/2.6/1.767 s.
    deltas = metadata_clock_deltas([2.4, 2.6, 1.767], [0.0, 1.088, 2.333])

    assert deltas == pytest.approx([1.188, 0.8285])
