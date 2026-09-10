"""Use observed average cadence without discarding genuine high-frame-rate sources."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from immich_memories.processing import live_photo_merger, probe_cache

INVALID_RATES = (None, "", "0", "0/0", "-30/1", "nan", "inf", "1/0", "1/nan", "1/inf", "bad")


@pytest.mark.parametrize(
    ("average", "nominal", "expected"),
    [
        ("30000/1001", "240/1", 30000 / 1001),
        ("24000/1001", "120/1", 24000 / 1001),
        ("120/1", "120/1", 120.0),
        ("240/1", "240/1", 240.0),
        ("29.75", "240/1", 29.75),
    ],
)
def test_average_cadence_wins_without_a_high_rate_cap(average, nominal, expected):
    assert probe_cache._frame_rate(  # noqa: SLF001 — normalized metadata boundary
        {"avg_frame_rate": average, "r_frame_rate": nominal}
    ) == pytest.approx(expected)


@pytest.mark.parametrize("invalid", INVALID_RATES)
def test_unusable_average_falls_back_to_nominal(invalid):
    assert probe_cache._frame_rate(  # noqa: SLF001
        {"avg_frame_rate": invalid, "r_frame_rate": "60000/1001"}
    ) == pytest.approx(60000 / 1001)


@pytest.mark.parametrize("invalid", INVALID_RATES)
def test_no_usable_rate_is_explicitly_unknown(invalid):
    assert (
        probe_cache._frame_rate(  # noqa: SLF001
            {"avg_frame_rate": invalid, "r_frame_rate": invalid}
        )
        == 0.0
    )


def _response(average, nominal):
    return {
        "streams": [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1920,
                "height": 1080,
                "duration": "2.5",
                "avg_frame_rate": average,
                "r_frame_rate": nominal,
                "side_data_list": [
                    {"side_data_type": "Display Matrix", "rotation": 90, "description": "a,b,c"}
                ],
            }
        ],
        "format": {"duration": "2.5", "size": "4"},
    }


def test_comprehensive_probe_caches_the_average_and_preserves_other_metadata(tmp_path, monkeypatch):
    source = tmp_path / "source.mov"
    source.write_bytes(b"fake")
    calls = []

    def run(command, **_kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, json.dumps(_response("30/1", "240/1")), "")

    monkeypatch.setattr(subprocess, "run", run)
    cache = probe_cache.ProbeCache()

    first = cache.get(source)
    second = cache.get(source)

    assert first is second
    assert first.fps == 30.0
    assert first.video_duration_seconds == 2.5
    assert first.rotation == 90
    assert first.resolution == (1080, 1920)
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("average", "nominal", "expected"),
    [
        ("30000/1001", "240/1", 30000 / 1001),
        ("120/1", "120/1", 120.0),
        ("240/1", "240/1", 240.0),
        ("nan", "30/1", 30.0),
        ("inf", "30/1", 30.0),
        ("0", "30/1", 30.0),
        ("0/0", "30/1", 30.0),
        ("0/0", "inf", None),
    ],
)
def test_merger_uses_the_same_rates_from_json_with_side_data(
    tmp_path, monkeypatch, average, nominal, expected
):
    source = tmp_path / "source.mov"
    calls = []

    def run(command, **_kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, json.dumps(_response(average, nominal)), "")

    monkeypatch.setattr(subprocess, "run", run)

    actual = live_photo_merger.probe_clip_fps(source)

    assert actual is None if expected is None else actual == pytest.approx(expected)
    assert len(calls) == 1
    command = calls[0]
    assert command[command.index("-of") + 1] == "json"
    fields = command[command.index("-show_entries") + 1]
    assert "avg_frame_rate" in fields
    assert "r_frame_rate" in fields


def test_burst_retains_true_high_rate_instead_of_a_false_nominal_maximum(monkeypatch):
    # Rendering consumes verified packet cadence, separately from metadata averages.
    rates = {"variable.mov": 30.0, "fast.mov": 120.0}

    def cadence(_self, path):
        return {"fps": rates[path.name]}

    monkeypatch.setattr(probe_cache.ProbeCache, "render_frame_rate", cadence)

    assert live_photo_merger.burst_fps([Path(name) for name in rates]) == 120.0
