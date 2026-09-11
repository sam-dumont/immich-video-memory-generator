"""CLI and UI entry points share one exact editorial runtime context."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from immich_memories.analysis.smart_pipeline import PipelineConfig
from immich_memories.api.models import Asset, AssetType
from immich_memories.config_loader import Config
from immich_memories.timeperiod import DateRange
from immich_memories.ui.state import AppState
from tests.conftest import make_clip


class _StopAtAnalysis(RuntimeError):
    """End a wiring test immediately after runtime composition."""


def test_cli_runtime_keeps_exact_windows_and_total_duration_for_story_selection(tmp_path) -> None:
    from immich_memories.cli._pipeline_runner import run_pipeline_and_generate

    clip = make_clip("exact-cli", duration=5.0)
    earlier = DateRange(
        datetime(2016, 8, 20, tzinfo=UTC),
        datetime(2016, 8, 20, 23, 59, 59, tzinfo=UTC),
    )
    later = DateRange(
        datetime(2026, 8, 20, tzinfo=UTC),
        datetime(2026, 8, 20, 23, 59, 59, tzinfo=UTC),
    )
    display_span = DateRange(earlier.start, later.end)
    config = Config(
        cache={
            "database": str(tmp_path / "analysis.db"),
            "directory": str(tmp_path / "cache"),
        }
    )
    pipeline = MagicMock()
    pipeline.run_editorial_source.side_effect = _StopAtAnalysis

    # WHY: stubs clip conversion, the pipeline class, and its builder for this wiring check.
    with (
        # WHY: avoids real ffprobe/file reads when turning the asset into a clip.
        patch("immich_memories.generate.assets_to_clips", return_value=[clip]),
        # WHY: replaces the real selection engine class so no analysis actually runs.
        patch("immich_memories.analysis.smart_pipeline.SmartPipeline", return_value=pipeline),
        # WHY: captures the editorial_context and dry_run kwargs asserted on below.
        patch(
            "immich_memories.analysis.editorial_runtime.build_smart_pipeline",
            return_value=pipeline,
            create=True,
        ) as build_pipeline,
        pytest.raises(_StopAtAnalysis),
    ):
        run_pipeline_and_generate(
            assets=[clip.asset],
            client=MagicMock(),
            config=config,
            progress=MagicMock(),
            duration=90.0,
            transition="cut",
            music=None,
            output_path=tmp_path / "on-this-day.mp4",
            memory_type="on_this_day",
            person_names=["Riley", "Bob"],
            date_range=display_span,
            date_ranges=(earlier, later),
            title_override="On this day",
            memory_preset_params={"person_match": "or"},
            upload_to_immich=False,
            album=None,
            source="auto",
            memory_key="candidate:on-this-day",
            no_render=True,
            accept_any_provenance=True,
        )

    context = build_pipeline.call_args.kwargs["editorial_context"]
    assert context.key == "candidate:on-this-day"
    assert context.label == "On this day"
    assert context.product == "on_this_day"
    assert context.date_ranges == (earlier, later)
    assert context.target_seconds == 90.0
    assert context.artifact_dir == tmp_path / "cache" / "editorial-runs" / context.key
    assert context.people == ("Riley", "Bob")
    assert context.person_match == "or"
    assert context.accept_any_provenance is True
    assert context.trip is False
    assert build_pipeline.call_args.kwargs["dry_run"] is False


def test_cli_runtime_defaults_to_primary_range_without_treating_no_render_as_dry_run(
    tmp_path,
) -> None:
    from immich_memories.cli._pipeline_runner import run_pipeline_and_generate

    clip = make_clip("no-render-cli", duration=5.0)
    window = DateRange(
        datetime(2026, 7, 1, tzinfo=UTC),
        datetime(2026, 7, 31, 23, 59, 59, tzinfo=UTC),
    )
    config = Config(
        cache={
            "database": str(tmp_path / "analysis.db"),
            "directory": str(tmp_path / "cache"),
        }
    )
    pipeline = MagicMock()
    pipeline.run_editorial_source.side_effect = _StopAtAnalysis

    # WHY: stubs clip conversion and the pipeline chain for this default-window wiring test.
    with (
        # WHY: skips real ffprobe/file reads for the single stub clip used here.
        patch("immich_memories.generate.assets_to_clips", return_value=[clip]),
        # WHY: replaces the selection engine class; construction must not run real analysis.
        patch("immich_memories.analysis.smart_pipeline.SmartPipeline", return_value=pipeline),
        # WHY: the call under inspection; its kwargs are asserted after the raise.
        patch(
            "immich_memories.analysis.editorial_runtime.build_smart_pipeline",
            return_value=pipeline,
            create=True,
        ) as build_pipeline,
        pytest.raises(_StopAtAnalysis),
    ):
        run_pipeline_and_generate(
            assets=[clip.asset],
            client=MagicMock(),
            config=config,
            progress=MagicMock(),
            duration=60.0,
            transition="cut",
            music=None,
            output_path=tmp_path / "july.mp4",
            memory_type="monthly_highlights",
            person_names=[],
            date_range=window,
            upload_to_immich=False,
            album=None,
            no_render=True,
        )

    context = build_pipeline.call_args.kwargs["editorial_context"]
    assert context.date_ranges == (window,)
    assert context.key == "july"
    assert context.label == window.description
    assert build_pipeline.call_args.kwargs["dry_run"] is False


def test_cli_album_runtime_uses_the_immutable_captured_corpus_not_its_display_span(
    tmp_path,
) -> None:
    from immich_memories.cli._pipeline_runner import run_pipeline_and_generate

    clip = make_clip(
        "album-video",
        duration=5.0,
        file_created_at=datetime(2026, 7, 1, tzinfo=UTC),
    )
    photo = Asset(
        id="album-photo",
        type=AssetType.IMAGE,
        fileCreatedAt=datetime(2026, 7, 9, tzinfo=UTC),
        fileModifiedAt=datetime(2026, 7, 9, tzinfo=UTC),
        updatedAt=datetime(2026, 7, 9, tzinfo=UTC),
    )
    display_span = DateRange(clip.asset.file_created_at, photo.file_created_at)
    config = Config(
        cache={
            "database": str(tmp_path / "analysis.db"),
            "directory": str(tmp_path / "cache"),
        }
    )
    pipeline = MagicMock()
    pipeline.run_editorial_source.side_effect = _StopAtAnalysis

    # WHY: stubs clip conversion and the pipeline chain around the album corpus wiring.
    with (
        # WHY: avoids real ffprobe/file reads for the stub video clip in this album fixture.
        patch("immich_memories.generate.assets_to_clips", return_value=[clip]),
        # WHY: replaces the selection engine class so construction never runs real analysis.
        patch("immich_memories.analysis.smart_pipeline.SmartPipeline", return_value=pipeline),
        # WHY: the collaborator whose editorial_context/album kwargs are asserted below.
        patch(
            "immich_memories.analysis.editorial_runtime.build_smart_pipeline",
            return_value=pipeline,
            create=True,
        ) as build_pipeline,
        pytest.raises(_StopAtAnalysis),
    ):
        run_pipeline_and_generate(
            assets=[clip.asset],
            photo_assets=[photo],
            include_photos=True,
            client=MagicMock(),
            config=config,
            progress=MagicMock(),
            duration=75.0,
            transition="cut",
            music=None,
            output_path=tmp_path / "album-holiday.mp4",
            memory_type="album",
            person_names=[],
            date_range=display_span,
            date_ranges=(),
            title_override="A different rendered title",
            memory_preset_params={"album_name": "Holiday", "album_id": "album-42"},
            upload_to_immich=False,
            album=None,
            no_render=True,
        )

    context = build_pipeline.call_args.kwargs["editorial_context"]
    assert context.key == "album-holiday"
    assert context.label == "Holiday"
    assert context.product == "album"
    assert context.date_ranges == ()
    assert context.album_ref == "album-42"
    assert context.album_sources == (clip.asset, photo)
    assert context.target_seconds == 75.0


def test_ui_runtime_uses_exact_state_windows_and_total_duration(tmp_path) -> None:
    from immich_memories.ui.pages.clip_pipeline import _run_pipeline_blocking

    earlier = DateRange(
        datetime(2016, 8, 20, tzinfo=UTC),
        datetime(2016, 8, 20, 23, 59, 59, tzinfo=UTC),
    )
    later = DateRange(
        datetime(2026, 8, 20, tzinfo=UTC),
        datetime(2026, 8, 20, 23, 59, 59, tzinfo=UTC),
    )
    clip = make_clip("ui-reviewed", duration=5.0)
    app_config = Config(
        cache={
            "database": str(tmp_path / "analysis.db"),
            "directory": str(tmp_path / "cache"),
        }
    )
    state = AppState(
        config=app_config,
        immich_url="http://immich.test",
        immich_api_key="test-key",
        memory_type="on_this_day",
        date_ranges=[earlier, later],
        clips=[clip],
        selected_clip_ids={clip.asset.id},
        target_duration=1.5,
        accept_any_provenance=True,
        thumbnail_cache=MagicMock(),
        analysis_cache=MagicMock(),
    )
    pipeline = MagicMock()
    pipeline.last_deep_analysis_count = 0
    pipeline.run_analysis.return_value = []
    result = MagicMock(
        selected_clips=[],
        editorial_selections=(),
        clip_segments={},
        errors=[],
        stats={},
    )
    pipeline.run_editorial_source.return_value = ([], result)
    progress_state = {"cancelled": False, "done": False, "error": None}

    # WHY: stubs the Immich client, pipeline class, config lookup, and pipeline builder.
    with (
        # WHY: replaces the Immich HTTP client so no real connection opens in this UI test.
        patch("immich_memories.ui.pages.clip_pipeline.SyncImmichClient") as client_type,
        # WHY: replaces the selection engine class so construction never runs real analysis.
        patch("immich_memories.analysis.smart_pipeline.SmartPipeline", return_value=pipeline),
        # WHY: replaces the process-wide config singleton with this test's Config instance.
        patch("immich_memories.config.get_config", return_value=app_config),
        # WHY: the collaborator under inspection; its call kwargs are asserted after the run.
        patch(
            "immich_memories.analysis.editorial_runtime.build_smart_pipeline",
            return_value=pipeline,
        ) as build_pipeline,
    ):
        client_type.return_value.__enter__.return_value = MagicMock()
        _run_pipeline_blocking(state, PipelineConfig(), [clip], [], progress_state)

    assert progress_state["error"] is None
    context = build_pipeline.call_args.kwargs["editorial_context"]
    assert context.product == "on_this_day"
    assert context.date_ranges == (earlier, later)
    assert context.label == f"{earlier.description}; {later.description}"
    assert context.target_seconds == 90.0
    assert context.artifact_dir.parent == tmp_path / "cache" / "editorial-runs"
    assert context.accept_any_provenance is True
    assert build_pipeline.call_args.kwargs["dry_run"] is False


def test_ui_album_runtime_keeps_full_corpus_and_owner_review_exclusions(tmp_path) -> None:
    from immich_memories.ui.pages.clip_pipeline import _run_pipeline_blocking

    kept_video = make_clip("kept-video", duration=5.0)
    dropped_video = make_clip("dropped-video", duration=5.0)
    kept_photo = Asset(
        id="kept-photo",
        type=AssetType.IMAGE,
        fileCreatedAt=datetime(2026, 7, 2, tzinfo=UTC),
        fileModifiedAt=datetime(2026, 7, 2, tzinfo=UTC),
        updatedAt=datetime(2026, 7, 2, tzinfo=UTC),
    )
    dropped_photo = Asset(
        id="dropped-photo",
        type=AssetType.IMAGE,
        fileCreatedAt=datetime(2026, 7, 3, tzinfo=UTC),
        fileModifiedAt=datetime(2026, 7, 3, tzinfo=UTC),
        updatedAt=datetime(2026, 7, 3, tzinfo=UTC),
    )
    app_config = Config(
        cache={
            "database": str(tmp_path / "analysis.db"),
            "directory": str(tmp_path / "cache"),
        }
    )
    state = AppState(
        config=app_config,
        immich_url="http://immich.test",
        immich_api_key="test-key",
        memory_type="album",
        album_id="album-42",
        album_name="Holiday",
        # Album loading keeps this for display/titles. It is not acquisition scope.
        date_ranges=[DateRange(kept_video.asset.file_created_at, dropped_photo.file_created_at)],
        clips=[kept_video, dropped_video],
        photo_assets=[kept_photo, dropped_photo],
        include_photos=True,
        selected_clip_ids={kept_video.asset.id},
        selected_photo_ids={kept_photo.id},
        target_duration=2.0,
        thumbnail_cache=MagicMock(),
        analysis_cache=MagicMock(),
    )
    pipeline = MagicMock()
    pipeline.last_deep_analysis_count = 0
    pipeline.run_analysis.return_value = []
    result = MagicMock(
        selected_clips=[],
        editorial_selections=(),
        clip_segments={},
        errors=[],
        stats={},
    )
    pipeline.run_editorial_source.return_value = ([], result)
    progress_state = {"cancelled": False, "done": False, "error": None}

    # WHY: stubs the same four boundaries for this album-sourced UI pipeline run.
    with (
        # WHY: replaces the Immich HTTP client so no real connection opens for this album run.
        patch("immich_memories.ui.pages.clip_pipeline.SyncImmichClient") as client_type,
        # WHY: replaces the selection engine class so construction never runs real analysis.
        patch("immich_memories.analysis.smart_pipeline.SmartPipeline", return_value=pipeline),
        # WHY: replaces the process-wide config singleton with this test's Config instance.
        patch("immich_memories.config.get_config", return_value=app_config),
        # WHY: the collaborator under inspection; its album/context kwargs are asserted below.
        patch(
            "immich_memories.analysis.editorial_runtime.build_smart_pipeline",
            return_value=pipeline,
        ) as build_pipeline,
    ):
        client_type.return_value.__enter__.return_value = MagicMock()
        _run_pipeline_blocking(
            state,
            PipelineConfig(),
            [kept_video],
            [kept_photo],
            progress_state,
        )

    assert progress_state["error"] is None
    context = build_pipeline.call_args.kwargs["editorial_context"]
    assert context.key.startswith("album-")
    assert context.label == "Holiday"
    assert context.date_ranges == ()
    assert context.album_ref == "album-42"
    assert context.album_sources == (
        kept_video,
        dropped_video,
        kept_photo,
        dropped_photo,
    )
    assert context.owner_excluded_asset_ids == ("dropped-video", "dropped-photo")
    assert context.target_seconds == 120.0
