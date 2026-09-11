"""A declared endpoint may use only its real final frame, never missing material."""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from immich_memories.processing import editorial_live_render as renderer
from immich_memories.processing import live_photo_merger as merger
from immich_memories.processing.live_material import LiveRenderMaterial, LiveSourceEntry
from immich_memories.processing.probe_cache import ProbeCache, ProbeError


def source_probe(*, video=1778 / 600, container=2.966667, fps=1830 / 89, start=0.0):
    return SimpleNamespace(
        has_video=True,
        video_duration_seconds=video,
        duration_seconds=container,
        fps=fps,
        video_start_seconds=start,
    )


class SourceProbes:
    def __init__(self, probe, tail, head=None):
        self.probe, self.tail, self.head, self.packet_reads = probe, tail, head, 0

    def get(self, _path):
        return self.probe

    def first_video_frame(self, _path):
        self.packet_reads += 1
        return self.head

    def last_video_frame(self, _path):
        self.packet_reads += 1
        return self.tail


def test_millisecond_container_end_is_bound_to_actual_last_packet():
    entry = LiveSourceEntry("still", "video", 0.0, 0.0, 2.967)
    tail = {
        "start_seconds": 1778 / 600 - 27 / 600,
        "end_seconds": 1778 / 600,
        "frame_seconds": 27 / 600,
    }
    probes = SourceProbes(source_probe(), tail)
    evidence = renderer._source_timing(probes, Path("original.mov"), entry)
    assert evidence["source_tail_seconds"] == pytest.approx(0.003666666666666707)
    assert evidence["final_packet"] == tail
    assert probes.packet_reads == 1
    assert entry.end == 2.967


@pytest.mark.parametrize(
    "end,container,video", [(2.97, 2.966667, 1778 / 600), (3.2, 3.2, 1778 / 600)]
)
def test_tail_is_not_an_arbitrary_epsilon_or_substantial_padding(end, container, video):
    probes = SourceProbes(
        source_probe(video=video, container=container),
        {"start_seconds": video - 27 / 600, "end_seconds": video, "frame_seconds": 27 / 600},
    )
    with pytest.raises(ValueError, match="exceeds actual video source"):
        renderer._source_timing(
            probes, Path("source.mov"), LiveSourceEntry("s", "v", 0.0, 0.0, end)
        )


def test_interval_cannot_consist_only_of_unavailable_tail():
    probes = SourceProbes(
        source_probe(),
        {
            "start_seconds": 1778 / 600 - 27 / 600,
            "end_seconds": 1778 / 600,
            "frame_seconds": 27 / 600,
        },
    )
    with pytest.raises(ValueError, match="exceeds actual video source"):
        renderer._source_timing(
            probes, Path("source.mov"), LiveSourceEntry("s", "v", 0.0, 2.965, 2.967)
        )


def test_video_duration_is_not_mistaken_for_absolute_endpoint():
    probes = SourceProbes(source_probe(video=2.0, container=3.0, start=1.0), None)
    evidence = renderer._source_timing(
        probes, Path("source.mov"), LiveSourceEntry("s", "v", 0.0, 1.0, 3.0)
    )
    assert evidence["video_end_seconds"] == 3.0
    assert probes.packet_reads == 0


def test_source_within_actual_video_needs_no_packet_scan():
    probes = SourceProbes(source_probe(video=2.918333, container=2.918345), None)
    renderer._source_timing(probes, Path("source.mov"), LiveSourceEntry("s", "v", 0.0, 0, 2.918))
    assert probes.packet_reads == 0


def test_packet_tail_uses_presentation_order_and_exact_timebase(tmp_path, monkeypatch):
    path = tmp_path / "source.mov"
    path.write_bytes(b"source")
    cache = ProbeCache()
    monkeypatch.setattr(cache, "get", lambda _p: SimpleNamespace(video_stream_index=2))
    payload = {
        "streams": [{"time_base": "1/600"}],
        "packets": [{"pts": 1751, "duration": 27}, {"pts": 1696, "duration": 28}],
    }

    def probe(command, **_kwargs):
        assert command[command.index("-select_streams") + 1] == "2"
        assert "-show_packets" in command
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr("subprocess.run", probe)
    tail = cache.last_video_frame(path)
    assert tail == {
        "time_base": "1/600",
        "pts": 1751,
        "duration_ticks": 27,
        "start_seconds": 1751 / 600,
        "end_seconds": 1778 / 600,
        "frame_seconds": 27 / 600,
    }
    payload["packets"][0].pop("duration")
    cache.invalidate(path)
    with pytest.raises(ProbeError, match="verified final presentation frame"):
        cache.last_video_frame(path)


def test_only_certified_merge_opts_into_eof_frame_retention(monkeypatch):
    paths = [Path("one.mov"), Path("two.mov")]
    monkeypatch.setattr(merger, "_detect_clip_hdr", lambda _p: False)
    monkeypatch.setattr(merger, "probe_clip_has_audio", lambda _p: False)
    monkeypatch.setattr(merger, "burst_fps", lambda _p: 240.0)
    # WHY: burst_encoding_plan is typed to return an EncodingPlan and never returns
    # None. Stubbing None passed only while build_merge_command stopped at
    # _append_encoding_args; the hardware-encode pass reads plan.pixel_format, so a
    # None stub is a fiction that fails the moment that pass lands.
    software_plan = merger.burst_encoding_plan(is_hdr=False, hardware_enabled=False)
    monkeypatch.setattr(merger, "burst_encoding_plan", lambda **_kw: software_plan)
    monkeypatch.setattr(merger, "_append_encoding_args", lambda *_a: None)
    plain = merger.build_merge_command(paths, [(0, 1), (0, 1)], Path("out.mp4"))
    certified = merger.build_merge_command(
        paths, [(0, 1), (0, 1)], Path("out.mp4"), quantize_material=True, render_frame_rate="240"
    )
    assert "eof_action" not in " ".join(plain)
    assert "fps=240:eof_action=pass" in " ".join(certified)


def test_subframe_hold_preserves_encoded_frames_and_records_ceil_target(tmp_path, monkeypatch):
    path = tmp_path / "encoded.mp4"
    path.write_bytes(b"all-original-selected-frames")
    before = renderer._sha(path)
    commands = []
    # WHY: burst_encoding_plan is typed to return an EncodingPlan and never returns
    # None. Stubbing None passed only while build_merge_command stopped at
    # _append_encoding_args; the hardware-encode pass reads plan.pixel_format, so a
    # None stub is a fiction that fails the moment that pass lands.
    software_plan = merger.burst_encoding_plan(is_hdr=False, hardware_enabled=False)
    monkeypatch.setattr(merger, "burst_encoding_plan", lambda **_kw: software_plan)
    monkeypatch.setattr(
        merger, "_append_encoding_args", lambda cmd, _p, _a, out: cmd.append(str(out))
    )

    def encode(command, **_kw):
        commands.append(command)
        Path(command[-1]).write_bytes(b"same-frames-with-last-frame-held")
        return subprocess.CompletedProcess(command, 0, "", "full encoder evidence")

    monkeypatch.setattr("subprocess.run", encode)
    monkeypatch.setattr(renderer.ProbeCache, "get", lambda _s, _p: source_probe(video=3.0, fps=30))
    record = renderer._hold_last_frame(
        path, 2.967, source_probe(video=2.95, fps=30), hardware_enabled=False
    )
    assert record["input_sha256"] == before
    assert record["target_frames"] == 90
    assert record["after_seconds"] == 3.0
    assert record["nominal_seconds"] == 2.967
    assert "tpad=stop_mode=clone" in " ".join(commands[0])
    assert "trim=end_frame=90" in " ".join(commands[0])
    assert path.read_bytes() == b"same-frames-with-last-frame-held"


def test_substantial_encoded_shortfall_fails_before_encoder(tmp_path, monkeypatch):
    def forbidden(*_a, **_k):
        pytest.fail("an unbounded shortfall must not launch an encoder")

    monkeypatch.setattr("subprocess.run", forbidden)
    with pytest.raises(ValueError, match="exceeds its certified allowance"):
        renderer._hold_last_frame(
            tmp_path / "encoded.mp4", 3.0, source_probe(video=2.8, fps=30), hardware_enabled=False
        )


def test_source_interval_before_nonzero_video_start_is_rejected():
    head = {"start_seconds": 1.0, "end_seconds": 1.0 + 1 / 30, "frame_seconds": 1 / 30}
    probes = SourceProbes(source_probe(video=2.0, container=3.0, start=1.0), None, head)
    with pytest.raises(ValueError, match="exceeds actual video source"):
        renderer._source_timing(
            probes, Path("source.mov"), LiveSourceEntry("s", "v", 0.0, 0.0, 0.5)
        )


def test_output_start_cannot_masquerade_as_playable_duration():
    with pytest.raises(ValueError, match="nonzero video start"):
        renderer._output_duration(source_probe(video=2.0, container=3.0, start=1.0))


def test_output_uses_stream_duration_not_absolute_endpoint():
    probe = source_probe(video=2.0, container=3.0, start=1 / 90000)
    probe.video_time_base = "1/90000"
    assert renderer._output_duration(probe) == 2.0


def test_certified_command_does_not_use_legacy_unknown_cadence_fallback(monkeypatch):
    monkeypatch.setattr(
        ProbeCache, "render_frame_rate", lambda *_a: (_ for _ in ()).throw(ProbeError("unknown"))
    )
    monkeypatch.setattr(merger, "burst_fps", lambda *_a: pytest.fail("legacy fallback used"))
    with pytest.raises(ProbeError, match="unknown"):
        merger.build_merge_command(
            [Path("missing.mov")], [(0, 1)], Path("out.mp4"), quantize_material=True
        )


class EncodeProbes:
    def __init__(self, probe):
        self.probe, self.invalidated = probe, []

    def get(self, _path):
        return self.probe

    def invalidate(self, path):
        self.invalidated.append(path)


TWO_CUTS = LiveRenderMaterial(
    (LiveSourceEntry("a", "va", 0.0, 0.0, 2.94), LiveSourceEntry("b", "vb", 1.0, 0.4855, 2.94))
)


def test_concat_of_two_quantized_segments_certifies_at_its_predicted_length(tmp_path):
    # Each cut rounds its own segment up: 89 + 74 frames at 30 fps, 38.8 ms past the material.
    probes = EncodeProbes(source_probe(video=163 / 30, container=163 / 30, fps=30))
    duration, hold, fps = renderer._quantized_encode(
        probes, tmp_path / "merge.mp4", TWO_CUTS, 163 / 30, hardware_enabled=False
    )
    assert (duration, hold, fps) == (163 / 30, None, 30)
    assert duration - TWO_CUTS.duration_seconds > 1 / 30


def test_encode_that_departs_from_its_packet_prediction_is_refused(tmp_path):
    probes = EncodeProbes(source_probe(video=165 / 30, container=165 / 30, fps=30))
    with pytest.raises(ValueError, match="changed its certified duration"):
        renderer._quantized_encode(
            probes, tmp_path / "merge.mp4", TWO_CUTS, 163 / 30, hardware_enabled=False
        )


def test_source_frame_shortfall_is_held_within_its_certified_allowance(tmp_path, monkeypatch):
    # A start cut inside an irregular source loses one source interval (60 ms), more than
    # one output frame; the hold receives exactly that certified shortfall plus one frame.
    predicted = TWO_CUTS.duration_seconds - 0.06
    held = []

    def hold(path, nominal, probe, *, hardware_enabled, allowance):
        held.append((nominal, allowance))
        probes.probe = source_probe(video=nominal, container=nominal, fps=30)
        return {"held": True}

    monkeypatch.setattr(renderer, "_hold_last_frame", hold)
    probes = EncodeProbes(source_probe(video=predicted, container=predicted, fps=30))
    duration, evidence, _ = renderer._quantized_encode(
        probes, tmp_path / "merge.mp4", TWO_CUTS, predicted, hardware_enabled=False
    )
    assert evidence == {"held": True}
    assert duration == TWO_CUTS.duration_seconds
    assert held == [(TWO_CUTS.duration_seconds, pytest.approx(0.06 + 1 / 30))]
    assert probes.invalidated == [tmp_path / "merge.mp4"]
