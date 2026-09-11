"""Exercise the opted-in product surfaces without legacy analysis or network work."""

from __future__ import annotations

import socket
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from immich_memories.analysis.editorial_planner import EditorialSelection
from immich_memories.analysis.smart_pipeline import PipelineConfig, PipelineResult
from immich_memories.api.models import Asset, AssetType, VideoClipInfo
from immich_memories.config_loader import Config
from immich_memories.timeperiod import DateRange
from immich_memories.ui.state import AppState
from tests.conftest import make_asset, make_clip

_WHEN = datetime(2026, 7, 10, tzinfo=UTC)
_WINDOW = DateRange(datetime(2026, 7, 1, tzinfo=UTC), datetime(2026, 7, 31, 23, 59, 59, tzinfo=UTC))


def _forbidden(*_args, **_kwargs):
    raise AssertionError("Source-first surface entered a legacy or network boundary")


@pytest.fixture(autouse=True)
def _offline_source_route(monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", _forbidden)
    monkeypatch.setattr(socket, "create_connection", _forbidden)


def _config(tmp_path) -> Config:
    return Config(
        editorial={
            "enabled": True,
            "annotation_database": str(tmp_path / "annotations.db"),
            "description_model": "fake-source-v1",
        },
        cache={"database": str(tmp_path / "analysis.db"), "directory": str(tmp_path / "cache")},
    )


def _photo(asset_id: str) -> Asset:
    return Asset(
        id=asset_id,
        type=AssetType.IMAGE,
        fileCreatedAt=_WHEN,
        fileModifiedAt=_WHEN,
        updatedAt=_WHEN,
    )


def _finished_selection() -> PipelineResult:
    motion = make_clip("selected-motion", duration=7.5, file_created_at=_WHEN)
    still = make_clip("selected-still-frame", duration=8.0, file_created_at=_WHEN)
    photo = VideoClipInfo(asset=_photo("selected-photo"), duration_seconds=3.25)
    # Deliberately nonchronological: the surface must preserve the editor's order,
    # fractional source intervals, and still extraction instruction without replanning.
    selected = [still, photo, motion]
    segments = {
        still.asset.id: (0.0, 4.25),
        photo.asset.id: (0.0, 3.25),
        motion.asset.id: (1.125, 5.375),
    }
    decisions = (
        EditorialSelection(
            asset_id=still.asset.id,
            start_time=0.0,
            end_time=4.25,
            render_mode="still",
            render_frame_seconds=2.375,
        ),
        EditorialSelection(
            asset_id=photo.asset.id, start_time=0.0, end_time=3.25, render_mode="still"
        ),
        EditorialSelection(
            asset_id=motion.asset.id, start_time=1.125, end_time=5.375, render_mode="motion"
        ),
    )
    return PipelineResult(
        selected_clips=selected,
        clip_segments=segments,
        editorial_selections=decisions,
        errors=[],
        stats={"selection_route": "editorial-source"},
    )


def _source_pipeline(result: PipelineResult) -> MagicMock:
    pipeline = MagicMock()
    pipeline.has_editorial_source_route = True
    pipeline.run_editorial_source.return_value = (result.selected_clips, result)
    return pipeline


@pytest.mark.parametrize("no_render", [False, True])
def test_cli_source_route_preserves_raw_album_demand_and_exact_render_handoff(tmp_path, no_render):
    from immich_memories.cli._pipeline_runner import run_pipeline_and_generate

    result = _finished_selection()
    short = make_asset("short-raw", duration=0.25, file_created_at=_WHEN)
    unknown = make_asset("unknown-raw", duration=None, file_created_at=_WHEN)
    videos = [result.selected_clips[2].asset, short, unknown, result.selected_clips[0].asset]
    photos = [result.selected_clips[1].asset, _photo("unselected-photo")]
    pipeline = _source_pipeline(result)
    output = tmp_path / "album.mp4"
    # WHY: stubs the pipeline builder, the renderer, and the no-render finish path together.
    with (
        # WHY: the collaborator under inspection; its context/dry_run kwargs are asserted.
        patch(
            "immich_memories.analysis.editorial_runtime.build_smart_pipeline", return_value=pipeline
        ) as build,
        patch("immich_memories.generate.generate_memory", return_value=output) as generate,
        patch(
            "immich_memories.cli._pipeline_runner._finish_without_rendering",
            return_value=(output, False, None),
        ) as finish,
    ):
        actual, _, _ = run_pipeline_and_generate(
            assets=videos,
            photo_assets=photos,
            include_photos=True,
            use_live_photos=False,
            client=MagicMock(),
            config=_config(tmp_path),
            progress=MagicMock(),
            duration=60.0,
            transition="cut",
            music=None,
            no_music=True,
            output_path=output,
            memory_type="album",
            person_names=[],
            date_range=_WINDOW,
            date_ranges=(),
            memory_preset_params={"album_name": "Source album", "album_id": "album-source"},
            upload_to_immich=False,
            album=None,
            no_render=no_render,
        )

    assert actual == output
    assert pipeline.run_editorial_source.call_args.args[0] == [*videos, *photos]
    assert pipeline.run_editorial_source.call_args.kwargs["include_live_photos"] is False
    context = build.call_args.kwargs["editorial_context"]
    assert context.album_sources == (*videos, *photos)
    assert context.album_ref == "album-source"
    assert context.date_ranges == ()
    assert build.call_args.kwargs["dry_run"] is False
    if no_render:
        generate.assert_not_called()
        assert finish.call_args.kwargs["pipeline_result"] is result
        assert (
            finish.call_args.kwargs["pipeline_result"].stats["selection_route"]
            == "editorial-source"
        )
    else:
        finish.assert_not_called()
        params = generate.call_args.args[0]
        assert params.clips is result.selected_clips
        assert params.clip_segments is result.clip_segments
        assert params.editorial_selections is result.editorial_selections
        assert params.include_photos is False
        assert params.photo_assets is None


@pytest.mark.parametrize("duration", [0.25, None])
def test_cli_raw_video_only_reaches_editor_before_legacy_minimum_duration_exit(tmp_path, duration):
    from immich_memories.cli._pipeline_runner import run_pipeline_and_generate
    from immich_memories.generate import assets_to_clips

    asset = make_asset("raw-only", duration=duration, file_created_at=_WHEN)
    assert assets_to_clips([asset]) == []
    pipeline = _source_pipeline(_finished_selection())
    # Stop at the actual seam: an unknown duration is evidence to resolve, not
    # permission for this wiring test to fabricate a playable source interval.
    pipeline.run_editorial_source.side_effect = RuntimeError("source route reached")
    # WHY: swaps in the pipeline stub so the run reaches the asserted RuntimeError path.
    with (
        # WHY: the collaborator under inspection; its call args are asserted after the raise.
        patch(
            "immich_memories.analysis.editorial_runtime.build_smart_pipeline", return_value=pipeline
        ),
        pytest.raises(RuntimeError, match="source route reached"),
    ):
        run_pipeline_and_generate(
            assets=[asset],
            client=MagicMock(),
            config=_config(tmp_path),
            progress=MagicMock(),
            duration=60.0,
            transition="cut",
            music=None,
            no_music=True,
            output_path=tmp_path / "raw.mp4",
            memory_type="monthly_highlights",
            person_names=[],
            date_range=_WINDOW,
            upload_to_immich=False,
            album=None,
            no_render=True,
        )
    assert pipeline.run_editorial_source.call_args.args[0] == [asset]


@pytest.mark.parametrize("include_photos", [False, True])
@pytest.mark.parametrize("include_live", [False, True])
def test_ui_source_route_retains_reviewed_demand_and_exact_selection(
    tmp_path, include_photos, include_live
):
    from immich_memories.ui.pages.clip_pipeline import _run_pipeline_blocking

    result = _finished_selection()
    reviewed_videos = [result.selected_clips[2], result.selected_clips[0]]
    reviewed_photo = result.selected_clips[1].asset
    excluded_video = make_clip("owner-excluded-video", file_created_at=_WHEN)
    excluded_photo = _photo("owner-excluded-photo")
    config = _config(tmp_path)
    state = AppState(
        config=config,
        immich_url="http://immich.test",
        immich_api_key="fake-test-key",
        memory_type="album",
        album_id="reviewed-album",
        album_name="Reviewed album",
        date_ranges=[_WINDOW],
        clips=[*reviewed_videos, excluded_video],
        photo_assets=[reviewed_photo, excluded_photo],
        include_photos=include_photos,
        include_live_photos=include_live,
        target_duration=1.0,
        thumbnail_cache=MagicMock(),
        pipeline_running=True,
    )
    if not include_photos:
        result.selected_clips.remove(result.selected_clips[1])
        result.clip_segments.pop(reviewed_photo.id)
        result.editorial_selections = tuple(
            d for d in result.editorial_selections if d.asset_id != reviewed_photo.id
        )
    pipeline = _source_pipeline(result)
    progress = {"cancelled": False, "done": False, "error": None}
    # WHY: stubs the Immich client, config lookup, and pipeline builder for this UI run.
    with (
        # WHY: replaces the Immich HTTP client so no real connection opens here.
        patch("immich_memories.ui.pages.clip_pipeline.SyncImmichClient") as client_type,
        # WHY: replaces the process-wide config singleton with this test's Config instance.
        patch("immich_memories.config.get_config", return_value=config),
        # WHY: the collaborator under inspection; its album/context kwargs are asserted below.
        patch(
            "immich_memories.analysis.editorial_runtime.build_smart_pipeline", return_value=pipeline
        ) as build,
    ):
        client_type.return_value.__enter__.return_value = MagicMock()
        _run_pipeline_blocking(state, PipelineConfig(), reviewed_videos, [reviewed_photo], progress)

    assert progress == {"cancelled": False, "done": True, "error": None}
    assert state.pipeline_running is False
    assert pipeline.run_editorial_source.call_args.args[0] == [
        *reviewed_videos,
        *([reviewed_photo] if include_photos else []),
    ]
    assert pipeline.run_editorial_source.call_args.kwargs["include_live_photos"] is include_live
    context = build.call_args.kwargs["editorial_context"]
    assert context.album_sources == (*state.clips, *state.photo_assets)
    assert context.owner_excluded_asset_ids == (excluded_video.asset.id, excluded_photo.id)
    assert context.date_ranges == ()
    assert context.target_seconds == 60.0
    assert state.pipeline_selected_clips is result.selected_clips
    assert state.clip_segments is result.clip_segments
    assert state.editorial_selections is result.editorial_selections
    assert state.pipeline_result["stats"]["selection_route"] == "editorial-source"
    if include_photos:
        assert state.selected_photo_ids == {reviewed_photo.id}


def test_ui_unavailable_source_reports_failure_without_legacy_reentry(tmp_path):
    from immich_memories.ui.pages.clip_pipeline import _run_pipeline_blocking

    clip = make_clip("unavailable-source", file_created_at=_WHEN)
    config = _config(tmp_path)
    state = AppState(
        config=config,
        immich_url="http://immich.test",
        immich_api_key="fake-test-key",
        memory_type="monthly_highlights",
        date_ranges=[_WINDOW],
        clips=[clip],
        target_duration=1.0,
        thumbnail_cache=MagicMock(),
        pipeline_running=True,
    )
    pipeline = _source_pipeline(_finished_selection())
    pipeline.run_editorial_source.side_effect = RuntimeError("selected source evidence unavailable")
    progress = {"cancelled": False, "done": False, "error": None}
    # WHY: stubs the Immich client, config lookup, and pipeline builder for this failure path.
    with (
        # WHY: replaces the Immich HTTP client; this run never needs a live connection.
        patch("immich_memories.ui.pages.clip_pipeline.SyncImmichClient"),
        # WHY: replaces the process-wide config singleton with this test's Config instance.
        patch("immich_memories.config.get_config", return_value=config),
        # WHY: the collaborator whose stubbed failure is expected to reach progress state.
        patch(
            "immich_memories.analysis.editorial_runtime.build_smart_pipeline", return_value=pipeline
        ),
    ):
        _run_pipeline_blocking(state, PipelineConfig(), [clip], [], progress)
    assert progress["error"] == "selected source evidence unavailable"
    assert progress["done"] is True
    assert state.pipeline_running is False
    assert state.pipeline_selected_clips == []
    pipeline.run_analysis.assert_not_called()
    pipeline.run_selection.assert_not_called()


def test_ui_loading_always_retains_raw_short_and_unknown_sources():
    from immich_memories.ui.pages.step2_loading import _build_clips

    assets = [
        make_asset("short", duration=0.25, file_created_at=_WHEN),
        make_asset("unknown", duration=None, file_created_at=_WHEN),
        make_asset("ordinary", duration=5.0, file_created_at=_WHEN),
    ]
    clips, skipped = _build_clips(assets)
    assert [clip.asset for clip in clips] == assets
    assert skipped == 0
    assert [clip.duration_seconds for clip in clips] == [0.25, 0.0, 5.0]


@pytest.mark.asyncio
async def test_ui_loading_fetches_only_uncached_thumbnails_and_never_probes(tmp_path):
    from immich_memories.ui.pages.step2_loading import _load_thumbnails_async

    config = _config(tmp_path)
    cached = make_clip("cached-thumbnail", file_created_at=_WHEN)
    missing = make_clip("missing-thumbnail", file_created_at=_WHEN)
    clips = [cached, missing]
    thumbnails = MagicMock()
    thumbnails.cached_ids.return_value = {cached.asset.id}
    state = AppState(config=config, thumbnail_cache=thumbnails)
    # WHY: stubs the page's app-state lookup and the batched thumbnail fetch together.
    with (
        # WHY: replaces the module-level state accessor with this test's mocked AppState.
        patch("immich_memories.ui.pages.step2_loading.get_app_state", return_value=state),
        # WHY: the collaborator under inspection; await args show only the uncached clip was sent.
        patch(
            "immich_memories.ui.pages.step2_loading._fetch_thumbnails_batched",
            new_callable=AsyncMock,
            return_value=1,
        ) as fetch_thumbnails,
    ):
        await _load_thumbnails_async(clips, MagicMock())

    # The loader has no probe path left: a clip keeps the duration and size Immich
    # declared, and only the thumbnail the cache lacks is fetched.
    assert (missing.width, missing.height, missing.duration_seconds) == (1920, 1080, 5.0)
    assert fetch_thumbnails.await_args.args[0] == [missing]
