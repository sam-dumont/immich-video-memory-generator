"""A declared source interval renders exactly the frames its packets can supply."""

from __future__ import annotations

import json
import subprocess
from fractions import Fraction

import pytest

from immich_memories.processing.probe_cache import ProbeCache, ProbeError

# Presentation timestamps of a real 30 fps recording thinned to an irregular
# grid (1/600 clock); the expected frame counts below are what ffmpeg's
# trim/setpts/fps=30:eof_action=pass graph emitted for each interval.
IRREGULAR = [
    (0, 60), (60, 60), (120, 60), (140, 60), (180, 20), (240, 60), (280, 40),
    (300, 40), (360, 60), (420, 20), (480, 60), (540, 20), (560, 60), (600, 40),
]  # fmt: skip


def _fake_probe(monkeypatch, packets, *, clock="1/600", container_start="0.000000"):
    metadata = {
        "streams": [
            {
                "index": 0,
                "codec_type": "video",
                "width": 320,
                "height": 240,
                "avg_frame_rate": "600/47",
                "r_frame_rate": "30/1",
                "duration": "3.066667",
                "start_time": container_start,
            }
        ],
        "format": {"duration": "3.066667", "size": "5", "start_time": container_start},
    }
    packet_data = {
        "streams": [{"time_base": clock}],
        "packets": [{"pts": pts, "duration": duration} for pts, duration in packets],
    }

    def run(command, **_kwargs):
        payload = packet_data if "-show_packets" in command else metadata
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr(subprocess, "run", run)


def _source(tmp_path):
    path = tmp_path / "source.mov"
    path.write_bytes(b"media")
    return path


@pytest.mark.parametrize(
    "start,end,frames",
    [
        (0.0, 0.15, 6),
        (0.0, 0.25, 9),
        (0.0, 0.2, 6),
        (0.05, 0.25, 6),
        (0.0, 0.7, 21),
        (0.0, 0.75, 24),
    ],
)
def test_segment_runs_to_the_first_frame_at_or_past_its_end(
    tmp_path, monkeypatch, start, end, frames
):
    _fake_probe(monkeypatch, IRREGULAR)
    segment = ProbeCache().quantized_segment(_source(tmp_path), start, end, Fraction(30))
    assert segment["frames"] == frames
    assert segment["seconds"] == pytest.approx(frames / 30)


def test_end_beyond_the_last_packet_stops_at_its_actual_end(tmp_path, monkeypatch):
    _fake_probe(monkeypatch, [(pts, 20) for pts in range(0, 1740, 20)] + [(1751, 27)])
    segment = ProbeCache().quantized_segment(_source(tmp_path), 0.0, 2.967, Fraction(30))
    assert segment["frames"] == 89
    assert segment["eof_pts"] == 1778


def test_declared_interval_is_read_in_ffmpeg_rebased_timeline(tmp_path, monkeypatch):
    # The container starts at 28 ms; ffmpeg subtracts that from every packet.
    _fake_probe(monkeypatch, [(pts, 20) for pts in range(30, 1800, 20)], container_start="0.028000")
    segment = ProbeCache().quantized_segment(_source(tmp_path), 0.0, 1.0, Fraction(30))
    assert segment["frames"] == 30
    assert segment["first_pts"] == 30


def test_interval_without_a_frame_is_refused(tmp_path, monkeypatch):
    _fake_probe(monkeypatch, IRREGULAR)
    with pytest.raises(ProbeError, match="no source frame"):
        ProbeCache().quantized_segment(_source(tmp_path), 0.21, 0.23, Fraction(30))


def test_negative_container_start_shifts_packets_forward(tmp_path, monkeypatch):
    # An edit list can place the container start before zero; ffmpeg adds 20 ms then.
    _fake_probe(monkeypatch, [(pts, 20) for pts in range(0, 1800, 20)], container_start="-0.020000")
    segment = ProbeCache().quantized_segment(_source(tmp_path), 0.0, 1.0, Fraction(30))
    assert segment["origin_pts"] == -12
    assert segment["first_pts"] == 0
    assert segment["frames"] == 30


def test_first_and_last_frames_share_the_packet_probe(tmp_path, monkeypatch):
    _fake_probe(monkeypatch, [(30, 20), (50, 20), (70, 27)])
    cache = ProbeCache()
    head, tail = (
        cache.first_video_frame(_source(tmp_path)),
        cache.last_video_frame(_source(tmp_path)),
    )
    assert head == {
        "time_base": "1/600",
        "pts": 30,
        "duration_ticks": 20,
        "start_seconds": 0.05,
        "end_seconds": pytest.approx(50 / 600),
        "frame_seconds": pytest.approx(20 / 600),
    }
    assert (tail["pts"], tail["duration_ticks"]) == (70, 27)
