"""Selected render cadence follows packet spacing rather than a VFR average."""

from __future__ import annotations

import json
import subprocess
from fractions import Fraction

import pytest

from immich_memories.processing.probe_cache import ProbeCache, ProbeError


def _fake_probe(monkeypatch, *, average="1830/89", nominal="24/1", packets=None, clock="1/24000"):
    commands = []
    metadata = {
        "streams": [
            {
                "index": 2,
                "codec_type": "video",
                "width": 1920,
                "height": 1080,
                "avg_frame_rate": average,
                "r_frame_rate": nominal,
                "duration": "2.966667",
            }
        ],
        "format": {"duration": "2.966667", "size": "5"},
    }
    packet_data = {"streams": [{"time_base": clock}], "packets": packets}

    def run(command, **_kwargs):
        commands.append(command)
        payload = packet_data if "-show_packets" in command else metadata
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr(subprocess, "run", run)
    return commands, metadata, packet_data


def _source(tmp_path):
    path = tmp_path / "source.mov"
    path.write_bytes(b"media")
    return path


def test_dense_vfr_sections_survive_despite_lower_average(tmp_path, monkeypatch):
    source = _source(tmp_path)
    # Presentation intervals vary; the densest interval is 1/24 second.
    packets = [{"pts": p, "duration": 1000} for p in [0, 2000, 3000, 4500]]
    commands, _, _ = _fake_probe(monkeypatch, packets=packets)
    cache = ProbeCache()

    assert cache.get(source).fps == pytest.approx(1830 / 89)
    cadence = cache.render_frame_rate(source)

    assert cadence == {
        "basis": "presentation-packet-spacing",
        "rate": "24",
        "fps": 24.0,
        "packet_count": 4,
        "time_base": "1/24000",
        "min_spacing_ticks": 1000,
    }
    assert cadence["fps"] > cache.get(source).fps
    assert len(commands) == 2
    packet_command = commands[1]
    assert packet_command[packet_command.index("-select_streams") + 1] == "2"
    assert packet_command[packet_command.index("-of") + 1] == "json"
    assert "-show_frames" not in packet_command


def test_unordered_pts_use_presentation_order_and_preserve_exact_fraction(tmp_path, monkeypatch):
    source = _source(tmp_path)
    packets = [{"pts": p, "duration": 1001} for p in [3003, -1001, 0, 1001]]
    _fake_probe(monkeypatch, average="25/1", nominal="60000/1001", packets=packets, clock="1/30000")

    cadence = ProbeCache().render_frame_rate(source)

    assert cadence["rate"] == "30000/1001"
    assert cadence["fps"] == pytest.approx(30000 / 1001)
    assert cadence["min_spacing_ticks"] == 1001


@pytest.mark.parametrize(
    "average,nominal", [("120/1", "120/1"), ("240/1", "240/1"), ("30000/1001", "60000/2002")]
)
def test_matching_cfr_rates_do_not_read_packets(tmp_path, monkeypatch, average, nominal):
    source = _source(tmp_path)
    commands, _, _ = _fake_probe(monkeypatch, average=average, nominal=nominal)
    cache = ProbeCache()

    cadence = cache.render_frame_rate(source)

    assert cadence == {
        "basis": "matching-stream-rates",
        "rate": str(Fraction(average)),
        "fps": float(Fraction(average)),
    }
    assert cache.get(source).average_frame_rate == average
    assert cache.get(source).nominal_frame_rate == nominal
    assert len(commands) == 1
    assert "-show_packets" not in commands[0]


@pytest.mark.parametrize(
    "packets",
    [
        None,
        [],
        [{"pts": 0}],
        [{"pts": 0}, {}],
        [{"pts": 0}, {"pts": 0}],
        [{"pts": 0}, {"pts": 1.0}],
        [{"pts": 0}, {"pts": "1000"}],
        [{"pts": 0}, {"pts": True}],
    ],
)
def test_unknown_or_ambiguous_pts_fail_instead_of_using_average(tmp_path, monkeypatch, packets):
    source = _source(tmp_path)
    _fake_probe(monkeypatch, packets=packets)

    with pytest.raises(ProbeError, match="presentation"):
        ProbeCache().render_frame_rate(source)


@pytest.mark.parametrize("clock", [None, "0/1", "-1/24000", "0/0", "nan", "inf"])
def test_packet_clock_must_be_positive_and_exact(tmp_path, monkeypatch, clock):
    source = _source(tmp_path)
    _fake_probe(monkeypatch, packets=[{"pts": 0}, {"pts": 1000}], clock=clock)

    with pytest.raises(ProbeError, match="presentation"):
        ProbeCache().render_frame_rate(source)


def test_unknown_stream_rates_require_real_packet_evidence(tmp_path, monkeypatch):
    source = _source(tmp_path)
    commands, _, _ = _fake_probe(
        monkeypatch, average="0/0", nominal="nan", packets=[{"pts": 0}, {"pts": 200}]
    )

    cadence = ProbeCache().render_frame_rate(source)

    assert cadence["basis"] == "presentation-packet-spacing"
    assert cadence["rate"] == "120"
    assert len(commands) == 2


def test_cadence_and_tail_share_exact_source_packet_probe(tmp_path, monkeypatch):
    source = _source(tmp_path)
    commands, _, packet_data = _fake_probe(
        monkeypatch, packets=[{"pts": 0, "duration": 1000}, {"pts": 1000, "duration": 1000}]
    )
    cache = ProbeCache()

    assert cache.render_frame_rate(source)["rate"] == "24"
    assert cache.last_video_frame(source)["end_seconds"] == pytest.approx(1 / 12)
    assert cache.render_frame_rate(source)["rate"] == "24"
    assert len(commands) == 2

    source.write_bytes(b"replaced media identity")
    packet_data["packets"] = [{"pts": 0, "duration": 500}, {"pts": 500, "duration": 500}]
    assert cache.render_frame_rate(source)["rate"] == "48"
    assert len(commands) == 4
