"""Certified Live rendering must use its complete, unchanged playable material."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from immich_memories import generate_clips
from immich_memories import generate_downloads as downloads
from immich_memories.analysis.editorial_planner import EditorialSelection
from immich_memories.api.models import AssetType, VideoClipInfo
from immich_memories.config_models import HardwareAccelConfig
from immich_memories.config_models_render import OutputConfig
from immich_memories.generate import GenerationParams
from immich_memories.processing import editorial_live_render as certified
from immich_memories.processing import live_photo_merger
from immich_memories.processing.live_material import LiveRenderMaterial, LiveSourceEntry
from tests.conftest import make_asset


@pytest.fixture(autouse=True)
def no_external_work(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("these renderer contract tests must not run services or media tools")

    monkeypatch.setattr("socket.socket.connect", forbidden)
    monkeypatch.setattr("subprocess.run", forbidden)


@pytest.fixture
def source(tmp_path):
    material = LiveRenderMaterial(
        (
            LiveSourceEntry("still-a", "video-a", 0.0, 0.0, 1.0),
            LiveSourceEntry("still-b", "video-b", 1.0, 0.5, 0.5),
            LiveSourceEntry("still-c", "video-b", 1.0, 0.5, 1.5),
        )
    )
    asset = make_asset("still-a", duration=None)
    asset.type = AssetType.IMAGE
    asset.live_photo_video_id = "video-a"
    clip = VideoClipInfo(
        asset=asset,
        duration_seconds=material.duration_seconds,
        live_burst_still_ids=list(material.still_ids),
        live_burst_video_ids=list(material.video_ids),
        live_burst_trim_points=list(material.trim_points),
        live_burst_shutter_timestamps=list(material.shutter_timestamps),
        live_burst_material=material.as_dict(),
        editorial_live_manifest={
            "version": certified.RENDER_VERSION,
            "material": material.as_dict(),
            "selected_interval": [0.25, 1.75],
        },
    )
    paths = [tmp_path / f"{video_id}.mov" for video_id in material.video_ids]
    for path in paths:
        path.write_bytes(path.stem.encode())
    return clip, paths, material


@pytest.fixture
def probes(monkeypatch):
    state = SimpleNamespace(calls=[], source_duration=2.0, output_duration=2.0, fps=25.0)

    class FakeProbeCache:
        def render_frame_rate(self, _path):
            return {"basis": "matching-stream-rates", "rate": str(int(state.fps)), "fps": state.fps}

        def get(self, path):
            state.calls.append(path)
            duration = (
                state.output_duration
                if path.parent.name == ".live_merges"
                else state.source_duration
            )
            return SimpleNamespace(
                has_video=True,
                video_duration_seconds=duration,
                duration_seconds=duration,
                fps=state.fps,
            )

    monkeypatch.setattr(certified, "ProbeCache", FakeProbeCache)
    return state


def merging(calls):
    def merge(paths, trims, target, **kwargs):
        calls.append((paths, trims, target, kwargs))
        target.write_bytes(b"encoded-positive-material")
        return target

    return merge


def test_positive_segments_keep_alias_lineage_and_exact_warm_skips_probe_and_merge(
    source, probes, tmp_path
):
    clip, paths, material = source
    calls = []
    merge = merging(calls)
    first = certified.render_certified_live(clip, paths, tmp_path, merge=merge)
    assert len(probes.calls) == 3  # Two actual sources and the resulting merge.
    second = certified.render_certified_live(clip, paths, tmp_path, merge=merge)
    assert first == second
    assert len(probes.calls) == 3
    assert len(calls) == 1
    assert calls[0][0:2] == (paths, [(0.0, 1.0), (0.5, 1.5)])
    assert calls[0][3] == {
        "shutter_timestamps": [0.0, 1.0],
        "hardware_enabled": True,
        "strict_material": True,
        "render_frame_rate": "25",
    }
    assert material.still_ids == ("still-a", "still-b", "still-c")
    record = json.loads(first.with_suffix(".json").read_text())
    assert record["declared_duration_seconds"] == record["encoded_duration_seconds"] == 2.0
    assert record["frame_rounding_bound_seconds"] == pytest.approx(2 / 25)
    assert record["identity"]["certificate"] == clip.editorial_live_manifest


@pytest.mark.parametrize("change", ["source_bytes", "selected_interval", "hardware"])
def test_changed_render_identity_cannot_reuse_an_old_merge(source, probes, tmp_path, change):
    clip, paths, _ = source
    calls = []
    merge = merging(calls)
    first = certified.render_certified_live(clip, paths, tmp_path, merge=merge)
    if change == "source_bytes":
        paths[0].write_bytes(b"new-source-bytes")
    if change == "selected_interval":
        clip.editorial_live_manifest["selected_interval"] = [0.0, 1.5]
    second = certified.render_certified_live(
        clip,
        paths,
        tmp_path,
        merge=merge,
        hardware_enabled=change != "hardware",
    )
    assert second != first
    assert first.is_file() and second.is_file()
    assert len(calls) == 2
    assert len(probes.calls) == 6


def test_tampered_cached_merge_fails_without_reencoding(source, probes, tmp_path):
    clip, paths, _ = source
    calls = []
    merge = merging(calls)
    output = certified.render_certified_live(clip, paths, tmp_path, merge=merge)
    output.write_bytes(b"unrelated-cached-video")
    with pytest.raises(ValueError, match="Cached editorial Live merge changed"):
        certified.render_certified_live(clip, paths, tmp_path, merge=merge)
    assert len(calls) == 1 and len(probes.calls) == 3


def test_strict_download_ignores_unchecked_local_path_but_legacy_keeps_it(
    source, probes, tmp_path, monkeypatch
):
    clip, paths, material = source
    stale = tmp_path / "stale-local.mp4"
    stale.write_bytes(b"unverified")
    clip.local_path = str(stale)
    calls = []
    monkeypatch.setattr(downloads, "_try_merge_burst", merging(calls))
    prefetched = {
        key: SimpleNamespace(path=path) for key, path in zip(material.video_ids, paths, strict=True)
    }
    output = downloads.download_clip(
        None,
        None,
        clip,
        tmp_path,
        prefetched_burst_results=prefetched,
    )
    assert output != stale and output.is_file()
    assert len(calls) == 1
    legacy = clip.model_copy(update={"editorial_live_manifest": None})
    assert downloads.download_clip(None, None, legacy, tmp_path) == stale
    assert len(calls) == 1 and len(probes.calls) == 3


@pytest.mark.parametrize("missing", ["absent", "none", "wrong_id"])
def test_missing_declared_companion_cannot_become_a_subset_or_still_fallback(
    source, probes, tmp_path, monkeypatch, missing
):
    clip, paths, _ = source
    results = {"video-a": SimpleNamespace(path=paths[0])}
    if missing == "none":
        results["video-b"] = SimpleNamespace(path=None)
    if missing == "wrong_id":
        results["unrelated"] = SimpleNamespace(path=paths[1])
    fallback = []
    monkeypatch.setattr(downloads, "_download_fallback", lambda *_args: fallback.append(True))
    with pytest.raises(ValueError, match="missing a declared companion"):
        downloads.download_clip(None, None, clip, tmp_path, prefetched_burst_results=results)
    assert fallback == [] and probes.calls == []


def test_stale_local_path_cannot_bypass_missing_certified_sources(source, tmp_path):
    clip, _, _ = source
    stale = tmp_path / "existing.mp4"
    stale.write_bytes(b"unverified")
    clip.local_path = str(stale)
    with pytest.raises(ValueError, match="needs its declared sources"):
        downloads.download_clip(None, None, clip, tmp_path)


@pytest.mark.parametrize("bad_source", ["missing", "empty", "partial"])
def test_incomplete_source_file_is_rejected_before_probe(source, probes, tmp_path, bad_source):
    clip, paths, _ = source
    if bad_source == "missing":
        paths[1].unlink()
    elif bad_source == "empty":
        paths[1].write_bytes(b"")
    else:
        partial = paths[1].with_suffix(".part")
        paths[1].rename(partial)
        paths[1] = partial
    with pytest.raises(ValueError, match="requires every declared source"):
        certified.render_certified_live(clip, paths, tmp_path, merge=merging([]))
    assert probes.calls == []


@pytest.mark.parametrize("mismatch", ["trims", "interval", "lineage"])
def test_certificate_disagreement_fails_before_media_work(source, probes, tmp_path, mismatch):
    clip, paths, _ = source
    if mismatch == "trims":
        clip.live_burst_trim_points[0] = (0.1, 1.0)
    elif mismatch == "interval":
        clip.editorial_live_manifest["selected_interval"] = [0.0, 2.1]
    else:
        clip.live_burst_still_ids.pop()
    with pytest.raises(ValueError):
        certified.render_certified_live(clip, paths, tmp_path, merge=merging([]))
    assert probes.calls == []


@pytest.mark.parametrize("invalid", ["short_source", "unknown_fps"])
def test_source_bounds_and_frame_rate_must_be_proven(source, probes, tmp_path, invalid):
    clip, paths, _ = source
    if invalid == "short_source":
        probes.source_duration = 1.0
    else:
        probes.fps = 0.0
    calls = []
    with pytest.raises(ValueError):
        certified.render_certified_live(clip, paths, tmp_path, merge=merging(calls))
    assert calls == []


@pytest.mark.parametrize("actual", [1.8, 2.2])
def test_encoded_duration_drift_cannot_be_certified(source, probes, tmp_path, actual):
    clip, paths, _ = source
    probes.output_duration = actual
    with pytest.raises(ValueError, match="changed its certified duration"):
        certified.render_certified_live(clip, paths, tmp_path, merge=merging([]))
    assert not list((tmp_path / ".live_merges").glob("*.json"))


def test_failed_merge_cannot_create_a_certificate_or_fallback(source, probes, tmp_path):
    clip, paths, _ = source
    with pytest.raises(ValueError, match="material fallback is forbidden"):
        certified.render_certified_live(clip, paths, tmp_path, merge=lambda *_a, **_k: None)
    assert not list((tmp_path / ".live_merges").glob("*.json"))


def test_strict_merge_never_filters_sources_or_rewrites_video_trims(source, tmp_path, monkeypatch):
    _, paths, material = source
    target = tmp_path / "merge.mp4"
    seen = []

    def forbidden(*_args, **_kwargs):
        pytest.fail("strict material must not enter filtering or spectrogram alignment")

    def command(actual_paths, trims, output, **kwargs):
        seen.append((actual_paths, trims, output, kwargs))
        return ["fake-ffmpeg"]

    def fake_run(cmd, **_kwargs):
        assert cmd == ["fake-ffmpeg"]
        target.write_bytes(b"encoded")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(live_photo_merger, "filter_valid_clips", forbidden)
    monkeypatch.setattr(live_photo_merger, "align_clips_spectrogram", forbidden)
    monkeypatch.setattr(live_photo_merger, "probe_clip_has_audio", lambda _path: True)
    monkeypatch.setattr(live_photo_merger, "build_merge_command", command)
    monkeypatch.setattr("subprocess.run", fake_run)
    assert (
        downloads._try_merge_burst(
            paths,
            list(material.trim_points),
            target,
            shutter_timestamps=list(material.shutter_timestamps),
            strict_material=True,
        )
        == target
    )
    assert seen == [
        (
            paths,
            [(0.0, 1.0), (0.5, 1.5)],
            target,
            {
                "audio_trim_points": None,
                "hardware_enabled": True,
                "quantize_material": True,
                "render_frame_rate": None,
            },
        )
    ]


@pytest.fixture
def extraction_config():
    return SimpleNamespace(hardware=HardwareAccelConfig(enabled=False), output=OutputConfig())


@pytest.fixture
def segment_probe(monkeypatch):
    state = SimpleNamespace(calls=[], duration=1.52, fps=25.0)

    class FakeProbeCache:
        def get(self, path):
            state.calls.append(path)
            return SimpleNamespace(
                has_video=True,
                video_duration_seconds=state.duration,
                duration_seconds=state.duration,
                fps=state.fps,
            )

    monkeypatch.setattr(certified, "ProbeCache", FakeProbeCache)
    return state


def extracting(calls):
    def extract(path, **kwargs):
        calls.append((path, kwargs))
        target = kwargs["output_path"]
        target.write_bytes(b"accurately-extracted-segment")
        return target

    return extract


def test_exact_extraction_interval_has_no_buffers_and_warm_skips_probe_and_extract(
    source, segment_probe, extraction_config, tmp_path
):
    clip, paths, _ = source
    calls = []
    extract = extracting(calls)
    first, duration = certified.extract_certified_live(
        clip, paths[0], tmp_path, extract=extract, config=extraction_config
    )
    assert duration == 1.5
    assert calls == [
        (
            paths[0],
            {
                "start_time": 0.25,
                "end_time": 1.75,
                "output_path": first,
                "reencode": True,
                "buffer_start": False,
                "buffer_end": False,
                "config": extraction_config,
            },
        )
    ]
    assert certified.extract_certified_live(
        clip, paths[0], tmp_path, extract=extract, config=extraction_config
    ) == (first, 1.5)
    assert len(calls) == len(segment_probe.calls) == 1
    record = json.loads(first.with_suffix(".json").read_text())
    assert record["selected_duration_seconds"] == 1.5
    assert record["encoded_duration_seconds"] == 1.52
    assert record["frame_rounding_bound_seconds"] == 0.04


@pytest.mark.parametrize("changed", ["bytes", "interval", "config"])
def test_extraction_cannot_reuse_segment_after_material_or_settings_change(
    source, segment_probe, extraction_config, tmp_path, changed
):
    clip, paths, _ = source
    calls = []
    extract = extracting(calls)
    first, _ = certified.extract_certified_live(
        clip, paths[0], tmp_path, extract=extract, config=extraction_config
    )
    if changed == "bytes":
        paths[0].write_bytes(b"different-merged-video")
    elif changed == "interval":
        clip.editorial_live_manifest["selected_interval"] = [0.0, 1.5]
    else:
        extraction_config.hardware.enabled = True
    second, _ = certified.extract_certified_live(
        clip, paths[0], tmp_path, extract=extract, config=extraction_config
    )
    assert first != second and first.exists() and second.exists()
    assert len(calls) == len(segment_probe.calls) == 2


@pytest.mark.parametrize("invalid", ["short", "long", "unknown_fps"])
def test_certified_extraction_cannot_silently_shorten_or_extend_the_selection(
    source, segment_probe, extraction_config, tmp_path, invalid
):
    clip, paths, _ = source
    if invalid == "short":
        segment_probe.duration = 1.40
    elif invalid == "long":
        segment_probe.duration = 1.55
    else:
        segment_probe.fps = 0.0
    with pytest.raises(ValueError, match="cannot fulfill its certified interval|shortfall exceeds"):
        certified.extract_certified_live(
            clip, paths[0], tmp_path, extract=extracting([]), config=extraction_config
        )
    assert not list((tmp_path / ".live_segments").glob("*.json"))


@pytest.mark.parametrize("tamper", ["output", "certificate"])
def test_tampered_segment_cache_is_a_failure_without_reencoding(
    source, segment_probe, extraction_config, tmp_path, tamper
):
    clip, paths, _ = source
    calls = []
    extract = extracting(calls)
    target, _ = certified.extract_certified_live(
        clip, paths[0], tmp_path, extract=extract, config=extraction_config
    )
    if tamper == "output":
        target.write_bytes(b"unverified-video")
    else:
        target.with_suffix(".json").unlink()
    with pytest.raises(ValueError, match="lacks exact conserved provenance"):
        certified.extract_certified_live(
            clip, paths[0], tmp_path, extract=extract, config=extraction_config
        )
    assert len(calls) == len(segment_probe.calls) == 1


def generation_params(clip, config, tmp_path):
    return GenerationParams(
        clips=[clip],
        output_path=tmp_path / "film.mp4",
        config=config,
        editorial_selections=(EditorialSelection(clip.asset.id, 0.25, 1.75, "motion"),),
    )


def test_actual_extraction_uses_certificate_when_optional_segment_map_is_empty(
    source, segment_probe, extraction_config, tmp_path, monkeypatch
):
    clip, paths, _ = source
    params = generation_params(clip, extraction_config, tmp_path)
    calls = []
    monkeypatch.setattr(generate_clips, "_download_video_path", lambda *_args: paths[0])
    monkeypatch.setattr("immich_memories.processing.clips.extract_clip", extracting(calls))

    def forbidden(*_args, **_kwargs):
        pytest.fail("a certified extraction cannot use legacy duration shortening")

    monkeypatch.setattr(generate_clips, "_probe_file_duration", forbidden)
    result = generate_clips._extract_clips(params, None, tmp_path)
    assert len(result) == 1
    assert result[0].asset_id == clip.asset.id
    assert result[0].duration == 1.5
    assert (calls[0][1]["start_time"], calls[0][1]["end_time"]) == (0.25, 1.75)
    assert params.clip_segments == {}


@pytest.mark.parametrize("strict", [True, False])
def test_actual_extraction_missing_source_propagates_only_for_certified_live(
    source, extraction_config, tmp_path, monkeypatch, strict
):
    clip, _, _ = source
    if not strict:
        clip.editorial_live_manifest = None
    params = generation_params(clip, extraction_config, tmp_path)
    monkeypatch.setattr(generate_clips, "_download_video_path", lambda *_args: None)
    if strict:
        with pytest.raises(ValueError, match="source is unavailable"):
            generate_clips._extract_clips(params, None, tmp_path)
    else:
        assert generate_clips._extract_clips(params, None, tmp_path) == []


def test_actual_prefetch_ignores_stale_local_path_for_certified_companions(source, tmp_path):
    clip, _, material = source
    stale = tmp_path / "unchecked-local.mp4"
    stale.write_bytes(b"unverified-local-video")
    clip.local_path = str(stale)
    targets = generate_clips._prefetch_assets([clip])
    assert tuple(target.id for target in targets) == material.video_ids
    legacy = clip.model_copy(update={"editorial_live_manifest": None})
    assert generate_clips._prefetch_assets([legacy]) == []


@pytest.mark.parametrize("mismatch", ["missing", "still", "interval", "override", "companion"])
def test_actual_extraction_rejects_mismatched_directives_before_source_work(
    source, extraction_config, tmp_path, monkeypatch, mismatch
):
    clip, _, _ = source
    params = generation_params(clip, extraction_config, tmp_path)
    if mismatch == "missing":
        params.editorial_selections = ()
    elif mismatch == "still":
        params.editorial_selections = (EditorialSelection(clip.asset.id, 0.25, 1.75, "still"),)
    elif mismatch == "interval":
        params.editorial_selections = (EditorialSelection(clip.asset.id, 0.0, 1.5, "motion"),)
    elif mismatch == "override":
        params.clip_segments = {clip.asset.id: (0.0, 1.5)}
    else:
        clip.asset.live_photo_video_id = "unrelated-source"

    def forbidden(*_args, **_kwargs):
        pytest.fail("invalid certificates must fail before source acquisition")

    monkeypatch.setattr(generate_clips, "_download_video_path", forbidden)
    with pytest.raises(ValueError, match="Editorial Live"):
        generate_clips._extract_clips(params, None, tmp_path)
