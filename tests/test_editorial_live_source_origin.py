"""A declared interval is read where ffmpeg reads it: re-based to the container start."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from immich_memories.processing import editorial_live_render as renderer
from immich_memories.processing.live_material import LiveSourceEntry
from tests.conftest import source_packet as packet


def probe(*, video_start=0.0, container_start=0.0, video=2.966667, container=2.966667, fps=30.0):
    return SimpleNamespace(
        has_video=True,
        video_duration_seconds=video,
        duration_seconds=container,
        fps=fps,
        video_start_seconds=video_start,
        container_start_seconds=container_start,
    )


class Probes:
    def __init__(self, probe, *, head=None, tail=None):
        self.probe, self.head, self.tail, self.packet_reads = probe, head, tail, 0

    def get(self, _path):
        return self.probe

    def first_video_frame(self, _path):
        self.packet_reads += 1
        return self.head

    def last_video_frame(self, _path):
        self.packet_reads += 1
        return self.tail


def test_video_starting_with_its_container_is_at_zero_for_ffmpeg():
    # A video-only source whose stream and container both start late renders from 0.
    probes = Probes(probe(video_start=0.05, container_start=0.05))
    evidence = renderer._source_timing(
        probes, Path("late.mov"), LiveSourceEntry("s", "v", 0.0, 0.0, 2.9)
    )
    assert evidence["video_start_seconds"] == pytest.approx(0.0)
    assert evidence["video_end_seconds"] == pytest.approx(2.966667)
    assert evidence["container_start_seconds"] == 0.05
    assert probes.packet_reads == 0


def test_video_lead_within_its_first_frame_is_bound_to_that_packet():
    # Audio starts 28 ms in, video 50 ms in: ffmpeg shows the first frame 22 ms late.
    head = packet(pts=30, ticks=20)
    probes = Probes(probe(video_start=0.05, container_start=0.028), head=head)
    evidence = renderer._source_timing(
        probes, Path("lead.mov"), LiveSourceEntry("s", "v", 0.0, 0.0, 2.9667)
    )
    assert evidence["source_lead_seconds"] == pytest.approx(0.022)
    assert evidence["initial_packet"] == head
    assert evidence["lead_boundary"] == "video-start-within-first-source-frame"
    assert probes.packet_reads == 1


def test_video_lead_of_exactly_one_source_frame_binds_to_that_packet():
    # The mirror of the container-end case: the container opens 1.7 ms before the first
    # video packet, which lands one whole frame after the declared start. In seconds
    # that lead reads 0.03333333333333334 against a 0.03333333333333333 frame.
    head = packet(pts=21, ticks=20)
    probes = Probes(probe(video_start=0.035, container_start=1 / 600), head=head)
    evidence = renderer._source_timing(
        probes, Path("lead.mov"), LiveSourceEntry("s", "v", 0.0, 0.0, 2.9)
    )
    assert evidence["lead_boundary"] == "video-start-within-first-source-frame"
    assert evidence["initial_packet"] == head


@pytest.mark.parametrize("first_pts", [38, 120])
def test_video_lead_beyond_one_source_frame_is_refused(first_pts):
    head = packet(pts=first_pts, ticks=20)
    probes = Probes(probe(video_start=first_pts / 600, container_start=0.028), head=head)
    with pytest.raises(ValueError, match="exceeds actual video source"):
        renderer._source_timing(
            probes, Path("gap.mov"), LiveSourceEntry("s", "v", 0.0, 0.0, 2.9667)
        )


def test_interval_cannot_consist_only_of_the_missing_lead():
    head = packet(pts=30, ticks=20)
    probes = Probes(probe(video_start=0.05, container_start=0.028), head=head)
    with pytest.raises(ValueError, match="exceeds actual video source"):
        renderer._source_timing(probes, Path("lead.mov"), LiveSourceEntry("s", "v", 0.0, 0.0, 0.02))


def test_rejection_carries_the_measured_numbers():
    probes = Probes(probe(video=2.0, container=2.0))
    with pytest.raises(ValueError, match=r'"video_end_seconds": 2\.0') as caught:
        renderer._source_timing(probes, Path("short.mov"), LiveSourceEntry("s", "v", 0.0, 0.0, 3.5))
    assert '"declared_end_seconds": 3.5' in str(caught.value)


@pytest.mark.parametrize("declared_end", [2.966, 2.967])
def test_container_end_is_accepted_at_either_millisecond_reading(declared_end):
    # Immich publishes whole milliseconds; a floored or a rounded reading of the
    # 2.966667 s container both bind to the final packet, 3.3 ms before it.
    tail = packet(pts=1751, ticks=27)
    probes = Probes(probe(video=1778 / 600, container=2.966667), tail=tail)
    evidence = renderer._source_timing(
        probes, Path("tail.mov"), LiveSourceEntry("s", "v", 0.0, 0.0, declared_end)
    )
    assert evidence["boundary"] == "millisecond-container-end-within-final-source-frame"
    assert evidence["source_tail_seconds"] == pytest.approx(declared_end - 1778 / 600)


@pytest.mark.parametrize("declared_end", [2.965, 2.9675, 2.968])
def test_container_end_more_than_a_millisecond_off_is_not_a_reading(declared_end):
    tail = packet(pts=1751, ticks=27)
    probes = Probes(probe(video=1778 / 600, container=2.966667), tail=tail)
    with pytest.raises(ValueError, match="exceeds actual video source"):
        renderer._source_timing(
            probes, Path("tail.mov"), LiveSourceEntry("s", "v", 0.0, 0.0, declared_end)
        )


def test_container_end_inside_a_final_packet_that_outlives_the_container_is_bound_to_it():
    # The stream reports its end at the final packet's start (1660 ticks), but that
    # packet runs 29 ticks further; the millisecond reading of the container end
    # therefore sits inside the final frame, 48 ms before it ends.
    tail = packet(pts=1660, ticks=29)
    probes = Probes(probe(video=2.766667, container=2.766667), tail=tail)
    evidence = renderer._source_timing(
        probes, Path("outlived.mov"), LiveSourceEntry("s", "v", 0.0, 1.0165, 2.767)
    )
    assert evidence["boundary"] == "millisecond-container-end-within-final-source-frame"
    assert evidence["source_tail_seconds"] == pytest.approx(2.767 - 1689 / 600)
    assert evidence["final_packet"] == tail


def test_container_over_declaring_a_whole_frame_binds_to_its_final_packet():
    # A companion carrying 2.000 s of audio over 59 video frames: the container says
    # 2.000 s, the last video packet ends at 1.966667 s. The declared end is exactly
    # one frame past that packet, which the final frame covers by being held.
    tail = packet(pts=1160, ticks=20)
    probes = Probes(probe(video=1.966667, container=2.0, fps=29.0), tail=tail)
    evidence = renderer._source_timing(
        probes, Path("companion.mov"), LiveSourceEntry("s", "v", 0.0, 0.0, 2.0)
    )
    assert evidence["boundary"] == "millisecond-container-end-within-final-source-frame"
    assert evidence["final_packet"] == tail


def test_container_over_declaring_more_than_its_final_frame_is_refused():
    tail = packet(pts=1160, ticks=20)
    probes = Probes(probe(video=1.966667, container=2.033333, fps=29.0), tail=tail)
    with pytest.raises(ValueError, match="exceeds actual video source"):
        renderer._source_timing(
            probes, Path("companion.mov"), LiveSourceEntry("s", "v", 0.0, 0.0, 2.033)
        )


def test_container_end_before_the_final_packet_starts_is_refused():
    tail = packet(pts=1680, ticks=30)
    probes = Probes(probe(video=2.766667, container=2.766667), tail=tail)
    with pytest.raises(ValueError, match="exceeds actual video source"):
        renderer._source_timing(
            probes, Path("gap.mov"), LiveSourceEntry("s", "v", 0.0, 1.0165, 2.767)
        )
