"""Behavioral tests for per-run media probe reuse."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest


def _probe_payload() -> dict[str, object]:
    return {
        "streams": [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "hevc",
                "width": 1920,
                "height": 1080,
                "r_frame_rate": "60000/1001",
                "duration": "5.10",
                "color_space": "bt2020nc",
                "color_transfer": "arib-std-b67",
                "color_primaries": "bt2020",
                "bits_per_raw_sample": "10",
                "side_data_list": [{"rotation": -90}],
            },
            {
                "index": 1,
                "codec_type": "audio",
                "codec_name": "aac",
                "duration": "5.08",
                "bit_rate": "192000",
                "sample_rate": "48000",
                "channels": 2,
            },
        ],
        "format": {"duration": "5.12", "size": "4096", "bit_rate": "10000000"},
    }


def test_one_probe_supplies_all_source_metadata(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Callers can read every hot-path field without another ffprobe process."""
    from immich_memories.processing.probe_cache import ProbeCache

    source = tmp_path / "source.mov"
    source.write_bytes(b"media")
    commands: list[list[str]] = []

    def run_probe(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, json.dumps(_probe_payload()), "")

    monkeypatch.setattr("immich_memories.processing.probe_cache.subprocess.run", run_probe)
    cache = ProbeCache()

    probe = cache.get(source)
    assert cache.get(source) is probe
    assert (
        probe.duration_seconds,
        probe.resolution,
        probe.codec,
        probe.hdr_type,
        probe.has_audio,
        probe.rotation,
        probe.fps,
    ) == (5.12, (1080, 1920), "hevc", "hlg", True, 90, 60000 / 1001)
    assert probe.audio_duration_seconds == 5.08
    assert probe.video_duration_seconds == 5.10
    assert probe.audio_bitrate == 192000
    assert len(commands) == 1
    assert "json" in commands[0]


def test_changed_file_identity_is_reprobed(tmp_path: Path, monkeypatch) -> None:
    """A staged segment rewritten at the same path cannot retain stale metadata."""
    from immich_memories.processing.probe_cache import ProbeCache

    source = tmp_path / "segment.mp4"
    source.write_bytes(b"first")
    calls = 0

    def run_probe(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        payload = _probe_payload()
        payload["format"] = {
            "duration": str(calls),
            "size": str(source.stat().st_size),
            "bit_rate": "10000000",
        }
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr("immich_memories.processing.probe_cache.subprocess.run", run_probe)
    cache = ProbeCache()

    assert cache.get(source).duration_seconds == 1.0
    source.write_bytes(b"second-version")
    assert cache.get(source).duration_seconds == 2.0
    assert calls == 2


def test_atomic_replacement_is_reprobed(tmp_path: Path, monkeypatch) -> None:
    """Publishing a replacement segment refreshes the final path's probe."""
    from immich_memories.processing.probe_cache import ProbeCache

    final = tmp_path / "segment.mp4"
    staged = tmp_path / "segment.staged.mp4"
    final.write_bytes(b"old")
    calls = 0

    def run_probe(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess(command, 0, json.dumps(_probe_payload()), "")

    monkeypatch.setattr("immich_memories.processing.probe_cache.subprocess.run", run_probe)
    cache = ProbeCache()
    cache.get(final)

    staged.write_bytes(b"replacement-media")
    os.replace(staged, final)
    cache.get(final)

    assert calls == 2


def test_explicit_invalidation_forces_a_fresh_probe(tmp_path: Path, monkeypatch) -> None:
    from immich_memories.processing.probe_cache import ProbeCache

    source = tmp_path / "source.mp4"
    source.write_bytes(b"media")
    calls = 0

    def run_probe(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess(command, 0, json.dumps(_probe_payload()), "")

    monkeypatch.setattr("immich_memories.processing.probe_cache.subprocess.run", run_probe)
    cache = ProbeCache()
    cache.get(source)
    cache.invalidate(source)
    cache.get(source)

    assert calls == 2


def test_probe_failure_is_not_cached(tmp_path: Path, monkeypatch) -> None:
    from immich_memories.processing.probe_cache import ProbeCache, ProbeError

    source = tmp_path / "source.mp4"
    source.write_bytes(b"media")
    calls = 0

    def run_probe(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return subprocess.CompletedProcess(command, 1, "", "broken")
        return subprocess.CompletedProcess(command, 0, json.dumps(_probe_payload()), "")

    monkeypatch.setattr("immich_memories.processing.probe_cache.subprocess.run", run_probe)
    cache = ProbeCache()

    with pytest.raises(ProbeError):
        cache.get(source)
    assert cache.get(source).codec == "hevc"
    assert calls == 2


def test_source_probe_helpers_share_one_caller_owned_cache(tmp_path: Path, monkeypatch) -> None:
    """Stable helper results are adapters over the same normalized metadata."""
    from immich_memories.processing.assembly_config import (
        AssemblyClip,
        AssemblySettings,
        standalone_assembly_encoding_plan,
    )
    from immich_memories.processing.clip_probing import (
        get_main_video_stream_map,
        get_video_duration,
        get_video_info,
    )
    from immich_memories.processing.encoding_plan import HdrTransfer
    from immich_memories.processing.ffmpeg_prober import FFmpegProber
    from immich_memories.processing.hdr_utilities import detect_dominant_hdr_transfer
    from immich_memories.processing.probe_cache import ProbeCache

    source = tmp_path / "source.mov"
    source.write_bytes(b"media")
    calls = 0

    def run_probe(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess(command, 0, json.dumps(_probe_payload()), "")

    monkeypatch.setattr("immich_memories.processing.probe_cache.subprocess.run", run_probe)
    cache = ProbeCache()
    prober = FFmpegProber(
        AssemblySettings(encoding_plan=standalone_assembly_encoding_plan()),
        probe_cache=cache,
    )
    clip = AssemblyClip(path=source, duration=5.0)

    assert get_video_duration(source, probe_cache=cache) == 5.12
    assert get_video_info(source, probe_cache=cache)["codec"] == "hevc"
    assert get_main_video_stream_map(source, probe_cache=cache) == "0:v:0"
    assert prober.get_video_resolution(source) == (1080, 1920)
    assert prober.probe_framerate(source) == pytest.approx(59.94, abs=0.01)
    assert prober.probe_duration(source, "audio") == 5.08
    assert prober.has_audio_stream(source) is True
    assert prober.has_video_stream(source) is True
    assert detect_dominant_hdr_transfer([clip], probe_cache=cache) is HdrTransfer.HLG
    assert calls == 1


def test_assembler_uses_the_caller_owned_run_cache() -> None:
    from immich_memories.config_loader import Config
    from immich_memories.generate_settings import _create_assembler
    from immich_memories.processing.assembly_config import (
        AssemblySettings,
        standalone_assembly_encoding_plan,
    )
    from immich_memories.processing.probe_cache import ProbeCache

    cache = ProbeCache()
    assembler = _create_assembler(
        AssemblySettings(encoding_plan=standalone_assembly_encoding_plan()),
        Config(),
        probe_cache=cache,
    )

    assert assembler.prober.probe_cache is cache


def test_extracted_segment_duration_uses_the_run_cache(tmp_path: Path, monkeypatch) -> None:
    from immich_memories.generate_clips import _probe_file_duration
    from immich_memories.processing.probe_cache import ProbeCache

    segment = tmp_path / "segment.mp4"
    segment.write_bytes(b"media")
    calls = 0

    def run_probe(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess(command, 0, json.dumps(_probe_payload()), "")

    monkeypatch.setattr("immich_memories.processing.probe_cache.subprocess.run", run_probe)
    cache = ProbeCache()

    assert _probe_file_duration(segment, probe_cache=cache) == 5.12
    assert cache.get(segment).codec == "hevc"
    assert calls == 1


def test_hot_assembly_metadata_consumers_share_one_probe(tmp_path: Path, monkeypatch) -> None:
    """Resolution, FPS, HDR, audio presence/bitrate reuse one source inspection."""
    from immich_memories.processing.assembly_config import (
        AssemblyClip,
        AssemblySettings,
        standalone_assembly_encoding_plan,
    )
    from immich_memories.processing.assembly_engine import create_assembly_context
    from immich_memories.processing.streaming_audio import _probe_max_audio_bitrate
    from immich_memories.processing.video_assembler import VideoAssembler

    source = tmp_path / "source.mov"
    source.write_bytes(b"media")
    calls = 0

    def run_probe(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess(command, 0, json.dumps(_probe_payload()), "")

    monkeypatch.setattr("immich_memories.processing.probe_cache.subprocess.run", run_probe)
    monkeypatch.setattr(
        "immich_memories.processing.hdr_utilities._check_zscale_available", lambda: True
    )
    settings = AssemblySettings(encoding_plan=standalone_assembly_encoding_plan())
    assembler = VideoAssembler(settings)
    clip = AssemblyClip(path=source, duration=5.0)

    context = create_assembly_context(settings, assembler.prober, [clip], 1920, 1080)
    assert context.target_fps == 60
    assert context.clip_hdr_types == ["hlg"]
    assert assembler.prober.get_video_resolution(source) == (1080, 1920)
    assert assembler.prober.has_audio_stream(source)
    assert assembler.encoder.resolve_encode_hdr(clip)[0] == "sdr"
    assert _probe_max_audio_bitrate([clip], probe_cache=assembler.prober.probe_cache) == "192k"
    assert calls == 1


@pytest.mark.parametrize(
    ("callable_factory", "expected_kwargs"),
    [
        (lambda seen: lambda *_args: seen.append({}), {}),
        (
            lambda seen: lambda *_args, probe_cache=None: seen.append({"probe_cache": probe_cache}),
            {"probe_cache": "cache"},
        ),
        (
            lambda seen: lambda *_args, **kwargs: seen.append(kwargs),
            {"probe_cache": "cache"},
        ),
    ],
    ids=["legacy", "explicit-keyword", "kwargs"],
)
def test_probe_cache_keyword_is_only_passed_to_compatible_callables(
    callable_factory, expected_kwargs
) -> None:
    from immich_memories.generate import _call_with_optional_probe_cache

    seen: list[dict[str, object]] = []
    callable_under_test = callable_factory(seen)

    _call_with_optional_probe_cache(callable_under_test, "arg", probe_cache="cache")

    assert seen == [expected_kwargs]


def test_optional_probe_cache_does_not_mask_internal_type_error() -> None:
    from immich_memories.generate import _call_with_optional_probe_cache

    def broken(*_args, probe_cache=None):
        assert probe_cache == "cache"
        raise TypeError("internal extension failure")

    with pytest.raises(TypeError, match="internal extension failure"):
        _call_with_optional_probe_cache(broken, "arg", probe_cache="cache")


def test_generation_probe_cache_seams_share_the_exact_cache(monkeypatch) -> None:
    from immich_memories import generate as generate_module
    from immich_memories.generate import (
        _build_settings_with_optional_probe_cache,
        _create_assembler_with_optional_probe_cache,
        _extract_clips_with_optional_prefetch,
    )
    from immich_memories.processing.probe_cache import ProbeCache

    cache = ProbeCache()
    extract = MagicMock(return_value=[])
    build_settings = MagicMock(return_value="settings")
    create_assembler = MagicMock(return_value="assembler")
    monkeypatch.setattr(generate_module, "_extract_clips", extract)
    monkeypatch.setattr(generate_module, "_build_assembly_settings", build_settings)
    monkeypatch.setattr(generate_module, "_create_assembler", create_assembler)
    monkeypatch.setattr(generate_module, "_build_download_coordinator", lambda *_args: None)

    _extract_clips_with_optional_prefetch(
        MagicMock(),
        None,
        Path("/tmp/run"),
        probe_cache=cache,
    )
    _build_settings_with_optional_probe_cache(
        MagicMock(),
        [],
        probe_cache=cache,
    )
    _create_assembler_with_optional_probe_cache(
        "settings",
        MagicMock(),
        probe_cache=cache,
    )

    assert extract.call_args.kwargs["probe_cache"] is cache
    assert build_settings.call_args.kwargs["probe_cache"] is cache
    assert create_assembler.call_args.kwargs["probe_cache"] is cache


def test_generation_probe_cache_seams_preserve_legacy_call_shapes(monkeypatch) -> None:
    from immich_memories import generate as generate_module
    from immich_memories.generate import (
        _build_settings_with_optional_probe_cache,
        _create_assembler_with_optional_probe_cache,
        _extract_clips_with_optional_prefetch,
    )
    from immich_memories.processing.probe_cache import ProbeCache

    calls: list[tuple[str, int]] = []

    def legacy_extract(_params, _cache_batch, _output_dir):
        calls.append(("extract", 3))
        return []

    def legacy_settings(_params, _clips):
        calls.append(("settings", 2))
        return "settings"

    def legacy_assembler(_settings, _config):
        calls.append(("assembler", 2))
        return "assembler"

    monkeypatch.setattr(
        generate_module,
        "_extract_clips",
        MagicMock(side_effect=legacy_extract),
    )
    monkeypatch.setattr(generate_module, "_build_assembly_settings", legacy_settings)
    monkeypatch.setattr(generate_module, "_create_assembler", legacy_assembler)
    monkeypatch.setattr(generate_module, "_build_download_coordinator", lambda *_args: None)
    cache = ProbeCache()

    _extract_clips_with_optional_prefetch(MagicMock(), None, Path("/tmp/run"), probe_cache=cache)
    _build_settings_with_optional_probe_cache(MagicMock(), [], probe_cache=cache)
    _create_assembler_with_optional_probe_cache("settings", MagicMock(), probe_cache=cache)

    assert calls == [("extract", 3), ("settings", 2), ("assembler", 2)]


def test_unusable_primary_frame_rate_falls_back_to_average(tmp_path: Path, monkeypatch) -> None:
    from immich_memories.processing.probe_cache import ProbeCache

    source = tmp_path / "source.mp4"
    source.write_bytes(b"media")
    payload = _probe_payload()
    streams = payload["streams"]
    assert isinstance(streams, list)
    video = streams[0]
    assert isinstance(video, dict)
    video["r_frame_rate"] = "0/0"
    video["avg_frame_rate"] = "30000/1001"

    def run_probe(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr("immich_memories.processing.probe_cache.subprocess.run", run_probe)

    assert ProbeCache().get(source).fps == pytest.approx(29.97, abs=0.01)
