"""Behavioral coverage for the run-scoped source-media probe cache."""

from __future__ import annotations

import json
import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock

import pytest


def _payload(*, duration: str = "5.12") -> dict[str, object]:
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
        "format": {"duration": duration, "size": "4096", "bit_rate": "10000000"},
    }


def _mock_ffprobe(monkeypatch: pytest.MonkeyPatch, payload: dict[str, object]) -> list[list[str]]:
    commands: list[list[str]] = []

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr("immich_memories.processing.probe_cache.subprocess.run", run)
    return commands


def test_one_cache_probe_supplies_hot_source_metadata_consumers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Duration, resolution, FPS, HDR and audio facts reuse one JSON probe."""
    from immich_memories.processing.assembly_config import (
        AssemblyClip,
        AssemblySettings,
        standalone_assembly_encoding_plan,
    )
    from immich_memories.processing.assembly_engine import create_assembly_context
    from immich_memories.processing.clip_probing import get_video_duration, get_video_info
    from immich_memories.processing.ffmpeg_prober import FFmpegProber
    from immich_memories.processing.hdr_utilities import detect_dominant_hdr_transfer
    from immich_memories.processing.probe_cache import ProbeCache
    from immich_memories.processing.streaming_audio import _probe_max_audio_bitrate

    source = tmp_path / "source.mov"
    source.write_bytes(b"media")
    commands = _mock_ffprobe(monkeypatch, _payload())
    cache = ProbeCache()
    settings = AssemblySettings(encoding_plan=standalone_assembly_encoding_plan())
    prober = FFmpegProber(settings, probe_cache=cache)
    clip = AssemblyClip(path=source, duration=5.0)

    assert get_video_duration(source, probe_cache=cache) == 5.12
    assert get_video_info(source, probe_cache=cache)["codec"] == "hevc"
    assert prober.get_video_resolution(source) == (1080, 1920)
    assert prober.probe_framerate(source) == pytest.approx(59.94, abs=0.01)
    assert prober.has_audio_stream(source) is True
    assert detect_dominant_hdr_transfer([clip], probe_cache=cache).value == "hlg"
    assert create_assembly_context(settings, prober, [clip], 1920, 1080).clip_primaries == [None]
    assert _probe_max_audio_bitrate([clip], probe_cache=cache) == "192k"
    assert len(commands) == 1
    assert "json" in commands[0]


def test_mutated_file_identity_reprobes_automatically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from immich_memories.processing.probe_cache import ProbeCache

    source = tmp_path / "segment.mp4"
    source.write_bytes(b"first")
    commands = _mock_ffprobe(monkeypatch, _payload())
    cache = ProbeCache()

    cache.get(source)
    source.write_bytes(b"second-version")
    cache.get(source)

    assert len(commands) == 2


def test_explicit_invalidation_reprobes_same_metadata_atomic_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A replace preserving size and mtime needs the explicit output-path invalidation."""
    from immich_memories.processing.probe_cache import ProbeCache

    final = tmp_path / "segment.mp4"
    staged = tmp_path / "segment.staged.mp4"
    final.write_bytes(b"old-media")
    commands = _mock_ffprobe(monkeypatch, _payload())
    cache = ProbeCache()
    cache.get(final)
    old_stat = final.stat()

    staged.write_bytes(b"new-media")
    os.utime(staged, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
    os.replace(staged, final)
    cache.invalidate(final)
    cache.get(final)

    assert len(commands) == 2


def test_invalidation_prevents_an_old_inflight_probe_from_repopulating_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An explicit invalidation creates a new generation even with identical stat data."""
    from immich_memories.processing.probe_cache import ProbeCache

    final = tmp_path / "segment.mp4"
    staged = tmp_path / "segment.staged.mp4"
    final.write_bytes(b"old-media")
    first_started = threading.Event()
    release_first = threading.Event()
    calls = 0

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        if calls == 1:
            first_started.set()
            assert release_first.wait(timeout=2)
            return subprocess.CompletedProcess(command, 0, json.dumps(_payload(duration="1")), "")
        return subprocess.CompletedProcess(command, 0, json.dumps(_payload(duration="2")), "")

    monkeypatch.setattr("immich_memories.processing.probe_cache.subprocess.run", run)
    cache = ProbeCache()
    with ThreadPoolExecutor(max_workers=2) as workers:
        old_probe = workers.submit(cache.get, final)
        assert first_started.wait(timeout=2)
        old_stat = final.stat()
        staged.write_bytes(b"new-media")
        os.utime(staged, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
        os.replace(staged, final)
        cache.invalidate(final)
        assert cache.get(final).duration_seconds == 2.0
        release_first.set()
        assert old_probe.result(timeout=2).duration_seconds == 1.0

    assert cache.get(final).duration_seconds == 2.0
    assert calls == 2


def test_symlink_aliases_share_the_canonical_probe_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Aliases resolve to the target identity, so a generation probes it once."""
    from immich_memories.processing.probe_cache import ProbeCache

    target = tmp_path / "target.mov"
    target.write_bytes(b"media")
    alias = tmp_path / "alias.mov"
    alias.symlink_to(target)
    commands = _mock_ffprobe(monkeypatch, _payload())
    cache = ProbeCache()

    assert cache.get(target) is cache.get(alias)
    assert len(commands) == 1


def test_same_key_concurrent_callers_share_one_probe_and_failure_wakes_waiters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Single-flight probing avoids a subprocess burst and never caches failures."""
    from immich_memories.processing.probe_cache import ProbeCache, ProbeProcessError

    source = tmp_path / "source.mp4"
    source.write_bytes(b"media")
    started = threading.Event()
    waiter_registered = threading.Event()
    release = threading.Event()
    calls = 0

    class NotifyingInflight(dict):
        def get(self, key, default=None):
            result = super().get(key, default)
            if result is not None:
                waiter_registered.set()
            return result

    def failing_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        started.set()
        assert waiter_registered.wait(timeout=2)
        assert release.wait(timeout=2)
        return subprocess.CompletedProcess(command, 1, "", "secret stderr must not leak")

    monkeypatch.setattr("immich_memories.processing.probe_cache.subprocess.run", failing_run)
    cache = ProbeCache()
    cache._inflight = NotifyingInflight()
    with ThreadPoolExecutor(max_workers=2) as workers:
        first = workers.submit(cache.get, source)
        assert started.wait(timeout=2)
        second = workers.submit(cache.get, source)
        assert waiter_registered.wait(timeout=2)
        release.set()
        with pytest.raises(ProbeProcessError, match="source.mp4"):
            first.result(timeout=2)
        with pytest.raises(ProbeProcessError, match="source.mp4"):
            second.result(timeout=2)

    assert calls == 1
    assert "secret" not in str(first.exception())

    commands = _mock_ffprobe(monkeypatch, _payload())
    assert cache.get(source).codec == "hevc"
    assert len(commands) == 1


@pytest.mark.parametrize(
    ("run_result", "error_type"),
    [
        (subprocess.CompletedProcess(["ffprobe"], 1, "", "secret stderr"), "ProbeProcessError"),
        (subprocess.CompletedProcess(["ffprobe"], 0, "not-json", ""), "ProbeMetadataError"),
    ],
)
def test_probe_errors_are_typed_and_do_not_leak_paths_or_stderr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    run_result: subprocess.CompletedProcess[str],
    error_type: str,
) -> None:
    from immich_memories.processing import probe_cache

    parent = tmp_path / "parent-secret"
    parent.mkdir()
    source = parent / "source.mp4"
    source.write_bytes(b"media")
    monkeypatch.setattr(probe_cache.subprocess, "run", lambda *_args, **_kwargs: run_result)

    with pytest.raises(getattr(probe_cache, error_type)) as exc_info:
        probe_cache.ProbeCache().get(source)

    message = str(exc_info.value)
    assert "source.mp4" in message
    assert "parent-secret" not in message
    assert "secret stderr" not in message


def test_missing_file_error_is_typed_and_basename_only(tmp_path: Path) -> None:
    from immich_memories.processing.probe_cache import ProbeCache, ProbeMissingFileError

    missing = tmp_path / "parent-secret" / "missing.mp4"
    with pytest.raises(ProbeMissingFileError) as exc_info:
        ProbeCache().get(missing)

    assert "missing.mp4" in str(exc_info.value)
    assert "parent-secret" not in str(exc_info.value)


@pytest.mark.parametrize(
    ("failure", "error_type"),
    [
        ("path", "ProbePathError"),
        ("missing", "ProbeMissingFileError"),
        ("stat", "ProbeStatError"),
        ("oserror", "ProbeProcessError"),
        ("timeout", "ProbeProcessError"),
    ],
)
def test_typed_probe_errors_have_no_raw_exception_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str, error_type: str
) -> None:
    """Source failures must not retain hidden full paths or subprocess details."""
    from immich_memories.processing import probe_cache

    parent = tmp_path / "parent-secret"
    parent.mkdir()
    source = parent / "source.mp4"
    source.write_bytes(b"media")
    cache = probe_cache.ProbeCache()
    target = source

    if failure == "path":
        target = parent / "source.txt"
        target.write_text("not media")
    elif failure == "missing":
        target.unlink()
    elif failure == "stat":

        class StatFailingPath:
            name = "source.mp4"

            def stat(self):
                raise OSError("raw stat")

        monkeypatch.setattr(
            probe_cache.ProbeCache,
            "_resolve",
            staticmethod(lambda _path: StatFailingPath()),
        )
    elif failure == "oserror":
        monkeypatch.setattr(
            probe_cache.subprocess,
            "run",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("raw subprocess")),
        )
    else:
        monkeypatch.setattr(
            probe_cache.subprocess,
            "run",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                subprocess.TimeoutExpired("ffprobe", 30)
            ),
        )

    with pytest.raises(getattr(probe_cache, error_type)) as exc_info:
        cache.get(target)

    assert "source" in str(exc_info.value)
    assert "parent-secret" not in str(exc_info.value)
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None


def test_probe_retains_every_normalized_video_and_audio_stream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Selecting convenient streams must not discard embedded/depth/audio records."""
    from immich_memories.processing.probe_cache import ProbeCache

    source = tmp_path / "multi.mov"
    source.write_bytes(b"media")
    payload = _payload()
    streams = payload["streams"]
    assert isinstance(streams, list)
    streams.extend(
        [
            {
                "index": 3,
                "codec_type": "video",
                "codec_name": "h264",
                "width": 3840,
                "height": 2160,
                "r_frame_rate": "30000/1001",
                "duration": "nan",
                "bit_rate": "40000000",
                "color_primaries": "bt709",
                "side_data_list": [{"rotation": "270"}],
            },
            {
                "index": 4,
                "codec_type": "audio",
                "codec_name": "ac3",
                "duration": "-1",
                "bit_rate": "384000",
                "sample_rate": "44100",
                "channels": "6",
            },
        ]
    )
    _mock_ffprobe(monkeypatch, payload)

    probe = ProbeCache().get(source)

    assert len(probe.video_streams) == 2
    assert len(probe.audio_streams) == 2
    assert probe.main_video.index == 3
    assert probe.main_video.rotation == 270
    assert probe.primary_audio.index == 1
    assert probe.max_audio_bitrate == 384000
    assert probe.video_duration_seconds == 0.0
    assert probe.audio_duration_seconds == 5.08


def test_malformed_metadata_error_has_no_raw_payload_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from immich_memories.processing.probe_cache import ProbeCache, ProbeMetadataError

    source = tmp_path / "source.mp4"
    source.write_bytes(b"media")
    monkeypatch.setattr(
        "immich_memories.processing.probe_cache.subprocess.run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            ["ffprobe"], 0, "{secret-payload}", ""
        ),
    )

    with pytest.raises(ProbeMetadataError) as exc_info:
        ProbeCache().get(source)

    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None
    assert "secret-payload" not in str(exc_info.value)


def test_clip_probing_preserves_value_error_for_invalid_source_paths(tmp_path: Path) -> None:
    from immich_memories.processing.clip_probing import (
        get_main_video_stream_map,
        get_video_duration,
        get_video_info,
    )

    missing = tmp_path / "missing.mp4"
    for helper in (get_video_duration, get_video_info, get_main_video_stream_map):
        with pytest.raises(ValueError, match="missing.mp4"):
            helper(missing)

    unsupported = tmp_path / "not-video.txt"
    unsupported.write_text("not a video")
    with pytest.raises(ValueError, match="not-video.txt"):
        get_video_duration(unsupported)


def test_ffmpeg_prober_failure_logs_only_a_basename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from immich_memories.processing.assembly_config import AssemblySettings
    from immich_memories.processing.ffmpeg_prober import FFmpegProber
    from immich_memories.processing.probe_cache import ProbeCache

    parent = tmp_path / "parent-secret"
    parent.mkdir()
    source = parent / "source.mp4"
    source.write_bytes(b"media")
    monkeypatch.setattr(
        "immich_memories.processing.probe_cache.subprocess.run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(["ffprobe"], 1, "", "secret stderr"),
    )

    prober = FFmpegProber(AssemblySettings(), probe_cache=ProbeCache())
    assert prober.has_audio_stream(source) is False
    assert prober.probe_duration(source) == 0.0
    assert prober.probe_framerate(source) == 60.0

    assert "source.mp4" in caplog.text
    assert "parent-secret" not in caplog.text
    assert "secret stderr" not in caplog.text


def test_standalone_assembler_uses_a_fresh_probe_cache_for_each_public_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from immich_memories.processing.assembly_config import AssemblyClip, AssemblySettings
    from immich_memories.processing.video_assembler import VideoAssembler

    source = tmp_path / "source.mp4"
    replacement = tmp_path / "replacement.mp4"
    source.write_bytes(b"same-size")
    replacement.write_bytes(b"new--size")
    commands = _mock_ffprobe(monkeypatch, _payload())
    assembler = VideoAssembler(AssemblySettings())
    monkeypatch.setattr(
        assembler,
        "_process_single_clip",
        lambda clip, _output: assembler.prober.probe_cache.get(clip.path) and _output,
    )
    clip = AssemblyClip(path=source, duration=5.0)

    assembler.assemble([clip], tmp_path / "one.mp4")
    stat = source.stat()
    os.utime(replacement, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    os.replace(replacement, source)
    assembler.assemble([clip], tmp_path / "two.mp4")

    assert len(commands) == 2


def test_injected_assembler_cache_persists_across_public_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from immich_memories.processing.assembly_config import AssemblyClip, AssemblySettings
    from immich_memories.processing.probe_cache import ProbeCache
    from immich_memories.processing.video_assembler import VideoAssembler

    source = tmp_path / "source.mp4"
    source.write_bytes(b"media")
    commands = _mock_ffprobe(monkeypatch, _payload())
    cache = ProbeCache()
    assembler = VideoAssembler(AssemblySettings(), probe_cache=cache)
    monkeypatch.setattr(
        assembler,
        "_process_single_clip",
        lambda clip, _output: assembler.prober.probe_cache.get(clip.path) and _output,
    )
    clip = AssemblyClip(path=source, duration=5.0)

    assembler.assemble([clip], tmp_path / "one.mp4")
    assembler.assemble([clip], tmp_path / "two.mp4")

    assert assembler.prober.probe_cache is cache
    assert len(commands) == 1


def test_live_burst_helpers_and_smart_zoom_share_the_run_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from immich_memories.processing.live_photo_merger import (
        _detect_clip_hdr,
        _probe_clip_orientation,
        probe_clip_has_audio,
        probe_clip_has_video,
    )
    from immich_memories.processing.probe_cache import ProbeCache
    from immich_memories.processing.scaling_utilities import _get_video_duration

    source = tmp_path / "source.mov"
    source.write_bytes(b"media")
    commands = _mock_ffprobe(monkeypatch, _payload())
    cache = ProbeCache()

    assert probe_clip_has_video(source, probe_cache=cache)
    assert probe_clip_has_audio(source, probe_cache=cache)
    assert _probe_clip_orientation(source, probe_cache=cache) == "portrait"
    assert _detect_clip_hdr(source, probe_cache=cache)
    assert _get_video_duration(source, probe_cache=cache) == 5.12
    assert len(commands) == 1


def test_cached_live_burst_orientation_honors_tag_only_rotation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from immich_memories.processing.live_photo_merger import _probe_clip_orientation
    from immich_memories.processing.probe_cache import ProbeCache

    source = tmp_path / "source.mov"
    source.write_bytes(b"media")
    payload = _payload()
    streams = payload["streams"]
    assert isinstance(streams, list)
    video = streams[0]
    assert isinstance(video, dict)
    video.pop("side_data_list")
    video["tags"] = {"rotate": "90"}
    commands = _mock_ffprobe(monkeypatch, payload)

    assert _probe_clip_orientation(source, probe_cache=ProbeCache()) == "portrait"
    assert "stream_tags=rotate" in commands[0][commands[0].index("-show_entries") + 1]


def test_title_pre_render_and_assembler_share_the_injected_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from immich_memories.processing.assembly_config import AssemblyClip, AssemblySettings
    from immich_memories.processing.probe_cache import ProbeCache
    from immich_memories.processing.title_inserter import TitleInserter
    from immich_memories.processing.video_assembler import VideoAssembler

    source = tmp_path / "source.mov"
    source.write_bytes(b"media")
    commands = _mock_ffprobe(monkeypatch, _payload())
    cache = ProbeCache()
    assembler = VideoAssembler(AssemblySettings(), probe_cache=cache)
    clip = AssemblyClip(path=source, duration=5.0)
    title_inserter = TitleInserter(assembler.settings, assembler.prober)
    decoder = MagicMock(return_value=iter(()))
    monkeypatch.setattr("immich_memories.processing.streaming_assembler._make_decoder", decoder)
    process = MagicMock()
    process.stdin = MagicMock()
    process.returncode = 1
    monkeypatch.setattr(
        "immich_memories.processing.title_inserter.subprocess.Popen",
        lambda *_args, **_kwargs: process,
    )

    title_inserter._pre_render_first_clip([clip], tmp_path, 1920, 1080, 30, None)
    title_inserter._pre_render_last_clip([clip], tmp_path, 1920, 1080, 30, None)
    assert [call.kwargs["probe_cache"] for call in decoder.call_args_list] == [cache, cache]
    assert len(commands) == 1


def test_live_burst_orchestrator_forwards_the_run_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from immich_memories import generate_downloads
    from immich_memories.processing.probe_cache import ProbeCache

    source = tmp_path / "source.mov"
    source.write_bytes(b"media")
    second = tmp_path / "second.mov"
    second.write_bytes(b"media")
    output = tmp_path / "merged.mp4"
    cache = ProbeCache()
    seen: dict[str, object] = {}
    monkeypatch.setattr(
        "immich_memories.processing.live_photo_merger.filter_valid_clips",
        lambda paths, trims, *, probe_cache=None: (
            seen.setdefault("filter", probe_cache) and paths,
            trims,
        ),
    )
    monkeypatch.setattr(
        "immich_memories.processing.live_photo_merger.probe_clip_has_audio",
        lambda _path, *, probe_cache=None: False,  # noqa: ARG005
    )
    monkeypatch.setattr(
        "immich_memories.processing.live_photo_merger.build_merge_command",
        lambda *_args, **kwargs: seen.setdefault("build", kwargs.get("probe_cache")) and ["ffmpeg"],
    )
    monkeypatch.setattr(
        generate_downloads.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 1, "", "expected"),
    )

    generate_downloads._try_merge_burst([source], [(0.0, 1.0)], output, probe_cache=cache)

    assert seen == {"filter": cache, "build": cache}


def test_live_burst_alignment_falls_back_when_cached_probe_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from immich_memories import generate_downloads
    from immich_memories.processing.probe_cache import ProbeCache, ProbeProcessError

    source = tmp_path / "source.mov"
    source.write_bytes(b"media")
    second = tmp_path / "second.mov"
    second.write_bytes(b"media")
    output = tmp_path / "merged.mp4"
    cache = ProbeCache()
    monkeypatch.setattr(
        "immich_memories.processing.live_photo_merger.filter_valid_clips",
        lambda paths, trims, *, probe_cache=None: (paths, trims),  # noqa: ARG005
    )
    monkeypatch.setattr(
        "immich_memories.processing.live_photo_merger.probe_clip_has_audio",
        lambda _path, *, probe_cache=None: True,  # noqa: ARG005
    )
    monkeypatch.setattr(
        "immich_memories.processing.live_photo_merger.build_merge_command",
        lambda *_args, **_kwargs: ["ffmpeg"],
    )
    monkeypatch.setattr(
        "immich_memories.processing.live_photo_merger.align_clips_spectrogram",
        lambda *_args: pytest.fail("alignment should not run after probe failure"),
    )
    monkeypatch.setattr(
        cache, "get", lambda _path: (_ for _ in ()).throw(ProbeProcessError("safe"))
    )
    monkeypatch.setattr(
        generate_downloads.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 1, "", "expected"),
    )

    assert (
        generate_downloads._try_merge_burst(
            [source, second],
            [(0.0, 1.0), (0.0, 1.0)],
            output,
            shutter_timestamps=[0.0, 1.0],
            probe_cache=cache,
        )
        is None
    )
