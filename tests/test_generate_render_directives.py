"""Editorial still-or-motion decisions at the generation boundary."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from immich_memories.analysis.editorial_planner import EditorialSelection
from immich_memories.api.models import AssetType
from immich_memories.config_loader import Config
from immich_memories.generate import GenerationParams
from immich_memories.processing.assembly_config import AssemblyClip
from immich_memories.processing.download_coordinator import DownloadResult
from tests.conftest import make_clip


@pytest.mark.parametrize("live", [False, True], ids=["photograph", "live-as-still"])
def test_selected_photos_render_each_planned_duration_without_changing_defaults(
    tmp_path, monkeypatch, live
):
    from immich_memories.generate_clips import _extract_clips

    clips = [make_clip("first", duration=3.5), make_clip("second", duration=2.25)]
    for clip in clips:
        clip.asset.type = AssetType.IMAGE
        if live:
            clip.asset.live_photo_video_id = clip.asset.id + "-companion"
            clip.live_burst_video_ids = [clip.asset.live_photo_video_id]
            clip.live_burst_trim_points = [(0.0, 1.0)]
    config = Config()
    default_duration = config.photos.duration
    params = GenerationParams(
        clips=clips,
        output_path=tmp_path / "memory.mp4",
        config=config,
        client=MagicMock(),
        clip_segments={clip.asset.id: (0.0, clip.duration_seconds) for clip in clips},
        editorial_selections=tuple(
            EditorialSelection(clip.asset.id, 0.0, clip.duration_seconds, "still") for clip in clips
        ),
    )
    observed = []

    def render(**kwargs):
        duration = kwargs["config"].duration
        observed.append((kwargs["asset"].id, duration))
        return AssemblyClip(
            path=tmp_path / (kwargs["asset"].id + ".mp4"),
            duration=duration,
            asset_id=kwargs["asset"].id,
            is_photo=True,
        )

    monkeypatch.setattr("immich_memories.photos.photo_pipeline._render_single_photo", render)
    monkeypatch.setattr(
        "immich_memories.generate_photos._detect_photo_resolution", lambda *_: (1920, 1080)
    )
    download = MagicMock(side_effect=AssertionError("A still must not download companion video"))
    monkeypatch.setattr("immich_memories.generate_clips._download_video_path", download)
    result = _extract_clips(params, None, tmp_path)
    assert observed == [("first", 3.5), ("second", 2.25)]
    assert [(clip.asset_id, clip.duration) for clip in result] == observed
    assert config.photos.duration == default_duration
    download.assert_not_called()


def test_direct_photo_render_without_editorial_selection_keeps_configured_duration(
    tmp_path, monkeypatch
):
    from immich_memories.generate_clips import _extract_clips

    clip = make_clip("direct-photo", duration=2.0)
    clip.asset.type = AssetType.IMAGE
    params = GenerationParams(
        clips=[clip],
        output_path=tmp_path / "memory.mp4",
        config=Config(),
        client=MagicMock(),
        clip_segments={clip.asset.id: (0.0, 2.0)},
    )
    render = MagicMock(return_value=None)
    monkeypatch.setattr("immich_memories.photos.photo_pipeline._render_single_photo", render)
    monkeypatch.setattr(
        "immich_memories.generate_photos._detect_photo_resolution", lambda *_: (1920, 1080)
    )
    _extract_clips(params, None, tmp_path)
    assert render.call_args.kwargs["config"].duration == params.config.photos.duration


def test_live_photo_directed_to_still_skips_burst_download_and_uses_photo_render(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from immich_memories.generate_clips import _extract_clips

    clip = make_clip("live-photo-still", duration=4.0)
    clip.asset.type = AssetType.IMAGE
    clip.asset.live_photo_video_id = "component-video"
    burst_ids = ["burst-a", "burst-b"]
    trim_points = [(0.0, 1.5), (1.0, 2.5)]
    shutters = [100.0, 101.0]
    still_ids = ["live-photo-still", "burst-neighbour"]
    clip.live_burst_video_ids = burst_ids
    clip.live_burst_trim_points = trim_points
    clip.live_burst_shutter_timestamps = shutters
    clip.live_burst_still_ids = still_ids
    params = GenerationParams(
        clips=[clip],
        output_path=tmp_path / "memory.mp4",
        config=Config(),
        client=MagicMock(),
        editorial_selections=(EditorialSelection(asset_id=clip.asset.id, render_mode="still"),),
    )
    rendered = AssemblyClip(
        path=tmp_path / "still.mp4",
        duration=4.0,
        asset_id=clip.asset.id,
        is_photo=True,
    )
    photo_render = MagicMock(return_value=rendered)
    coordinator = MagicMock()
    coordinator.sources_for.return_value = {}
    video_download = MagicMock(side_effect=AssertionError("motion must not be downloaded"))
    monkeypatch.setattr(
        "immich_memories.generate_photos._render_photo_as_clip",
        photo_render,
    )
    monkeypatch.setattr("immich_memories.generate_downloads.download_clip", video_download)

    result = _extract_clips(
        params,
        MagicMock(),
        tmp_path,
        download_coordinator=coordinator,
    )

    coordinator.prefetch.assert_not_called()
    video_download.assert_not_called()
    photo_render.assert_called_once()
    assert photo_render.call_args.args[0] is clip
    assert result == [rendered]
    assert clip.live_burst_video_ids is burst_ids
    assert clip.live_burst_trim_points is trim_points
    assert clip.live_burst_shutter_timestamps is shutters
    assert clip.live_burst_still_ids is still_ids


def test_video_directed_to_still_samples_exact_frame_and_uses_photo_render(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from immich_memories.generate_clips import _extract_clips

    clip = make_clip("video-still", duration=10.0)
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"video")
    clip.local_path = video_path
    frame_path = tmp_path / "exact-frame.jpg"
    frame_path.write_bytes(b"frame")
    rendered = AssemblyClip(
        path=tmp_path / "still.mp4",
        duration=4.0,
        asset_id=clip.asset.id,
        is_photo=True,
    )
    params = GenerationParams(
        clips=[clip],
        output_path=tmp_path / "memory.mp4",
        config=Config(),
        client=MagicMock(),
        clip_segments={clip.asset.id: (2.0, 7.5)},
        editorial_selections=(
            EditorialSelection(
                asset_id=clip.asset.id,
                render_mode="still",
                render_frame_seconds=4.25,
            ),
        ),
    )
    coordinator = MagicMock()
    coordinator.sources_for.return_value = {
        clip.asset.id: DownloadResult(
            asset_id=clip.asset.id,
            download_id=clip.asset.id,
            path=video_path,
        )
    }
    exact_frame = MagicMock(return_value=frame_path)
    photo_render = MagicMock(return_value=rendered)
    video_extract = MagicMock(side_effect=AssertionError("video segment must not render"))
    monkeypatch.setattr(
        "immich_memories.processing.frame_sampling.extract_frame_at",
        exact_frame,
        raising=False,
    )
    monkeypatch.setattr(
        "immich_memories.photos.photo_pipeline._render_single_photo",
        photo_render,
    )
    monkeypatch.setattr("immich_memories.processing.clips.extract_clip", video_extract)

    result = _extract_clips(
        params,
        MagicMock(),
        tmp_path,
        download_coordinator=coordinator,
    )

    coordinator.prefetch.assert_not_called()
    exact_frame.assert_called_once()
    assert exact_frame.call_args.args[0] == video_path
    assert exact_frame.call_args.kwargs["timestamp"] == 4.25
    photo_render.assert_called_once()
    assert photo_render.call_args.kwargs["asset"] is clip.asset
    assert photo_render.call_args.kwargs["source_path"] == frame_path
    assert photo_render.call_args.kwargs["config"].duration == 5.5
    assert params.config.photos.duration != 5.5
    video_extract.assert_not_called()
    assert result == [rendered]
    assert clip.asset.type == AssetType.VIDEO


def test_duplicate_render_directives_fail_before_prefetch(
    tmp_path: Path,
) -> None:
    from immich_memories.generate_clips import _extract_clips

    clip = make_clip("duplicate-directive", duration=5.0)
    params = GenerationParams(
        clips=[clip],
        output_path=tmp_path / "memory.mp4",
        config=Config(),
        client=MagicMock(),
        editorial_selections=(
            EditorialSelection(asset_id=clip.asset.id, render_mode="motion"),
            EditorialSelection(asset_id=clip.asset.id, render_mode="still", render_frame_seconds=2),
        ),
    )
    coordinator = MagicMock()

    with pytest.raises(ValueError, match="duplicate render directives.*duplicate-directive"):
        _extract_clips(
            params,
            MagicMock(),
            tmp_path,
            download_coordinator=coordinator,
        )

    coordinator.prefetch.assert_not_called()


def test_unknown_render_directive_fails_before_prefetch(
    tmp_path: Path,
) -> None:
    from immich_memories.generate_clips import _extract_clips

    clip = make_clip("known", duration=5.0)
    params = GenerationParams(
        clips=[clip],
        output_path=tmp_path / "memory.mp4",
        config=Config(),
        client=MagicMock(),
        editorial_selections=(
            EditorialSelection(asset_id="outside-generation", render_mode="motion"),
        ),
    )
    coordinator = MagicMock()

    with pytest.raises(ValueError, match="unknown render directive.*outside-generation"):
        _extract_clips(
            params,
            MagicMock(),
            tmp_path,
            download_coordinator=coordinator,
        )

    coordinator.prefetch.assert_not_called()


def test_image_motion_directive_without_live_photo_family_fails_before_prefetch(
    tmp_path: Path,
) -> None:
    from immich_memories.generate_clips import _extract_clips

    clip = make_clip("static-image", duration=4.0)
    clip.asset.type = AssetType.IMAGE
    params = GenerationParams(
        clips=[clip],
        output_path=tmp_path / "memory.mp4",
        config=Config(),
        client=MagicMock(),
        editorial_selections=(EditorialSelection(asset_id=clip.asset.id, render_mode="motion"),),
    )
    coordinator = MagicMock()

    with pytest.raises(ValueError, match="IMAGE motion.*Live Photo family.*static-image"):
        _extract_clips(
            params,
            MagicMock(),
            tmp_path,
            download_coordinator=coordinator,
        )

    coordinator.prefetch.assert_not_called()


def test_image_motion_directive_with_unpaired_live_photo_family_fails_before_prefetch(
    tmp_path: Path,
) -> None:
    from immich_memories.generate_clips import _extract_clips

    clip = make_clip("unpaired-live-photo", duration=4.0)
    clip.asset.type = AssetType.IMAGE
    clip.live_burst_video_ids = ["burst-a", "burst-b"]
    clip.live_burst_trim_points = [(0.0, 1.0)]
    params = GenerationParams(
        clips=[clip],
        output_path=tmp_path / "memory.mp4",
        config=Config(),
        client=MagicMock(),
        editorial_selections=(EditorialSelection(asset_id=clip.asset.id, render_mode="motion"),),
    )
    coordinator = MagicMock()

    with pytest.raises(ValueError, match="IMAGE motion.*Live Photo family.*unpaired-live-photo"):
        _extract_clips(
            params,
            MagicMock(),
            tmp_path,
            download_coordinator=coordinator,
        )

    coordinator.prefetch.assert_not_called()


def test_video_still_without_frame_timestamp_fails_before_prefetch(
    tmp_path: Path,
) -> None:
    from immich_memories.generate_clips import _extract_clips

    clip = make_clip("video-missing-frame", duration=5.0)
    params = GenerationParams(
        clips=[clip],
        output_path=tmp_path / "memory.mp4",
        config=Config(),
        client=MagicMock(),
        editorial_selections=(EditorialSelection(asset_id=clip.asset.id, render_mode="still"),),
    )
    coordinator = MagicMock()

    with pytest.raises(ValueError, match="exact frame timestamp.*video-missing-frame"):
        _extract_clips(
            params,
            MagicMock(),
            tmp_path,
            download_coordinator=coordinator,
        )

    coordinator.prefetch.assert_not_called()


def test_video_still_frame_past_source_fails_before_prefetch(
    tmp_path: Path,
) -> None:
    from immich_memories.generate_clips import _extract_clips

    clip = make_clip("video-past-frame", duration=5.0)
    params = GenerationParams(
        clips=[clip],
        output_path=tmp_path / "memory.mp4",
        config=Config(),
        client=MagicMock(),
        editorial_selections=(
            EditorialSelection(
                asset_id=clip.asset.id,
                render_mode="still",
                render_frame_seconds=5.01,
            ),
        ),
    )
    coordinator = MagicMock()

    with pytest.raises(ValueError, match="frame timestamp outside source.*video-past-frame"):
        _extract_clips(
            params,
            MagicMock(),
            tmp_path,
            download_coordinator=coordinator,
        )

    coordinator.prefetch.assert_not_called()


def test_live_photo_directed_to_motion_preserves_legacy_burst_rendering(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from immich_memories.generate_clips import _extract_clips

    clip = make_clip("live-photo-motion", duration=4.0)
    clip.asset.type = AssetType.IMAGE
    burst_ids = ["burst-a", "burst-b"]
    trim_points = [(0.0, 1.5), (1.0, 2.5)]
    shutters = [100.0, 101.0]
    still_ids = ["live-photo-motion", "burst-neighbour"]
    clip.live_burst_video_ids = burst_ids
    clip.live_burst_trim_points = trim_points
    clip.live_burst_shutter_timestamps = shutters
    clip.live_burst_still_ids = still_ids
    params = GenerationParams(
        clips=[clip],
        output_path=tmp_path / "memory.mp4",
        config=Config(),
        client=MagicMock(),
        editorial_selections=(EditorialSelection(asset_id=clip.asset.id, render_mode="motion"),),
    )
    merged = tmp_path / "merged.mp4"
    merged.write_bytes(b"merged")
    segment = tmp_path / "segment.mp4"
    segment.write_bytes(b"segment")
    coordinator = MagicMock()
    coordinator.sources_for.return_value = {}
    video_download = MagicMock(return_value=merged)
    video_extract = MagicMock(return_value=segment)
    photo_render = MagicMock(side_effect=AssertionError("motion must not render as a photo"))
    monkeypatch.setattr("immich_memories.generate_downloads.download_clip", video_download)
    monkeypatch.setattr("immich_memories.processing.clips.extract_clip", video_extract)
    monkeypatch.setattr("immich_memories.generate_photos._render_photo_as_clip", photo_render)
    monkeypatch.setattr(
        "immich_memories.generate_clips._probe_file_duration", lambda _path, **_kwargs: 4.0
    )

    result = _extract_clips(
        params,
        MagicMock(),
        tmp_path,
        download_coordinator=coordinator,
    )

    coordinator.prefetch.assert_not_called()
    assert video_download.call_args.args[2] is clip
    video_extract.assert_called_once()
    photo_render.assert_not_called()
    assert len(result) == 1
    assert result[0].asset_id == clip.asset.id
    assert not result[0].is_photo
    assert params.clips[0] is clip
    assert clip.live_burst_video_ids is burst_ids
    assert clip.live_burst_trim_points is trim_points
    assert clip.live_burst_shutter_timestamps is shutters
    assert clip.live_burst_still_ids is still_ids


def test_no_render_directive_preserves_legacy_video_extraction(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from immich_memories.generate_clips import _extract_clips

    clip = make_clip("legacy-video", duration=5.0)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    clip.local_path = source
    segment = tmp_path / "segment.mp4"
    segment.write_bytes(b"segment")
    params = GenerationParams(
        clips=[clip],
        output_path=tmp_path / "memory.mp4",
        config=Config(),
        client=MagicMock(),
        clip_segments={clip.asset.id: (1.25, 3.75)},
    )
    coordinator = MagicMock()
    coordinator.sources_for.return_value = {
        clip.asset.id: DownloadResult(
            asset_id=clip.asset.id,
            download_id=clip.asset.id,
            path=source,
        )
    }
    video_extract = MagicMock(return_value=segment)
    monkeypatch.setattr("immich_memories.processing.clips.extract_clip", video_extract)
    monkeypatch.setattr(
        "immich_memories.generate_clips._probe_file_duration", lambda _path, **_kwargs: 2.5
    )

    result = _extract_clips(
        params,
        MagicMock(),
        tmp_path,
        download_coordinator=coordinator,
    )

    assert params.editorial_selections == ()
    coordinator.prefetch.assert_not_called()
    video_extract.assert_called_once_with(
        source,
        start_time=1.25,
        end_time=3.75,
        config=params.config,
    )
    assert len(result) == 1
    assert result[0].asset_id == clip.asset.id
    assert result[0].duration == 2.5
    assert not result[0].is_photo
    assert params.clips[0] is clip
