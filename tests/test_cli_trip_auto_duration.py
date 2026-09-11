"""Trip Auto must survive Click and discovery before media sets the duration."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from immich_memories.analysis.trip_detection import DetectedTrip
from immich_memories.api.models import Asset
from immich_memories.cli import main
from immich_memories.cli._pipeline_runner import (
    _resolve_requested_duration,
    run_pipeline_and_generate,
)
from immich_memories.cli._trip_generation import _print_trip_result
from immich_memories.config_loader import Config


class _ResolvedBeforePlanning(BaseException):
    """Stop after the real resolver, before any planner/cache/render work."""


@pytest.fixture
def cli_config(monkeypatch):
    config = Config()
    config.immich.url = "http://test.invalid"
    config.immich.api_key = "test-key"
    config.trips.homebase_latitude = 0.0
    config.trips.homebase_longitude = 0.0
    monkeypatch.setattr("immich_memories.cli.init_config_dir", lambda: None)
    monkeypatch.setattr("immich_memories.cli.get_config", lambda: config)

    def forbid_network(*args, **kwargs):
        pytest.fail("duration resolution must not contact a service")

    monkeypatch.setattr("socket.socket.connect", forbid_network)
    return config


@pytest.mark.parametrize(
    ("date_args", "duration_args", "photos_per_day", "incoming", "expected", "no_render"),
    [
        ([], [], 4, None, 150.0, True),
        (["--start", "2031-04-08", "--end", "2031-04-19"], [], 4, None, 150.0, True),
        ([], [], 1, None, 55.0, True),
        ([], ["--duration", "420"], 1, 420.0, 420.0, True),
        ([], ["--duration", "420"], 1, 420.0, 420.0, False),
    ],
)
def test_trip_click_reaches_media_resolver_after_normal_discovery(
    cli_config, monkeypatch, date_args, duration_args, photos_per_day, incoming, expected, no_render
):
    """Use the real trip handler and pipeline entry; replace only external inputs."""
    start = datetime(2031, 4, 8, 12, tzinfo=UTC)
    photos = [
        Asset(
            id=f"photo-{day}-{index}",
            type="IMAGE",
            fileCreatedAt=start + timedelta(days=day),
            fileModifiedAt=start + timedelta(days=day),
            updatedAt=start + timedelta(days=day),
            exifInfo={"latitude": 45.0, "longitude": 8.0},
        )
        for day in range(12)
        for index in range(photos_per_day)
    ]
    trip = DetectedTrip(
        start_date=date(2031, 4, 8),
        end_date=date(2031, 4, 19),
        location_name="Test destination",
        asset_count=len(photos),
        centroid_lat=45.0,
        centroid_lon=8.0,
    )
    client = MagicMock()
    client.__enter__.return_value = client
    client.get_videos_for_date_range.return_value = []
    client.get_photos_for_date_range.return_value = photos
    observed = {}

    def record_pipeline_entry(**kwargs):
        observed["no_render"] = kwargs.get("no_render", False)
        return run_pipeline_and_generate(**kwargs)

    def resolve_then_stop(requested_duration, **kwargs):
        observed.update(
            incoming=requested_duration,
            product=kwargs["memory_type"],
            photos=[photo.id for photo in kwargs["photos"]],
            total=_resolve_requested_duration(requested_duration, **kwargs),
        )
        raise _ResolvedBeforePlanning()

    monkeypatch.setattr(
        "immich_memories.cli._pipeline_runner._resolve_requested_duration", resolve_then_stop
    )
    monkeypatch.setattr(
        "immich_memories.cli._trip_generation.run_pipeline_and_generate", record_pipeline_entry
    )
    # WHY: replaces the Immich client and trip detector the CLI opens before resolution.
    with (
        # WHY: SyncImmichClient is the Immich HTTP boundary; this returns the seeded client.
        patch("immich_memories.api.immich.SyncImmichClient", return_value=client),
        # WHY: trip detection isn't under test; this returns one fixed window to resolve.
        patch("immich_memories.cli._trip_display.run_trip_detection", return_value=[trip]),
        pytest.raises(_ResolvedBeforePlanning),
    ):
        CliRunner().invoke(
            main,
            [
                "generate",
                "--memory-type",
                "trip",
                "--year",
                "2031",
                "--near-date",
                "2031-04-14",
                "--include-photos",
                "--no-music",
                "--quiet",
                *(["--no-render"] if no_render else []),
                *date_args,
                *duration_args,
            ],
            catch_exceptions=False,
        )

    assert observed == {
        "incoming": incoming,
        "product": "trip",
        "photos": [photo.id for photo in photos],
        "total": expected,
        "no_render": no_render,
    }
    queried_window = client.get_photos_for_date_range.call_args.args[0]
    assert queried_window.start.date() == trip.start_date
    assert queried_window.end.date() == trip.end_date


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["--memory-type", "monthly_highlights", "--year", "2031", "--month", "4"], 60.0),
        (["--memory-type", "year_in_review", "--year", "2031"], 600.0),
        (["--start", "2031-04-08", "--end", "2031-04-19"], 30.0),
        (["--from-album", "test-album"], None),
        (["--from-album", "test-album", "--duration", "90"], 90.0),
    ],
)
def test_other_click_duration_defaults_are_preserved(cli_config, monkeypatch, args, expected):
    """The deferred trip duration must not change other product defaults."""
    observed = {}

    def before_client(**kwargs):
        observed["duration"] = kwargs["duration"]
        raise _ResolvedBeforePlanning()

    monkeypatch.setattr("immich_memories.cli.generate._build_params_table", before_client)
    with pytest.raises(_ResolvedBeforePlanning):
        CliRunner().invoke(main, ["generate", *args, "--no-render"], catch_exceptions=False)
    assert observed["duration"] == expected


def test_album_click_preserves_no_render_at_its_separate_dispatch(cli_config, monkeypatch):
    observed = {}

    def capture(**kwargs):
        observed.update(kwargs)
        raise _ResolvedBeforePlanning()

    monkeypatch.setattr("immich_memories.cli._album_generation.handle_album_generation", capture)
    client = MagicMock()
    client.__enter__.return_value = client
    # WHY: replaces the Immich HTTP boundary the album CLI path opens before dispatch.
    with (
        # WHY: SyncImmichClient is the Immich HTTP boundary; the test only checks handoff kwargs.
        patch("immich_memories.api.immich.SyncImmichClient", return_value=client),
        pytest.raises(_ResolvedBeforePlanning),
    ):
        CliRunner().invoke(
            main,
            [
                "generate",
                "--from-album",
                "test-album",
                "--duration",
                "45",
                "--no-render",
                "--no-music",
                "--quiet",
            ],
            catch_exceptions=False,
        )
    assert observed["no_render"] is True
    assert observed["dry_run"] is False


@pytest.mark.parametrize("dry_run,no_render", [(False, True), (True, False), (False, False)])
def test_trip_plan_only_result_does_not_claim_a_video_or_upload(
    monkeypatch, tmp_path, dry_run, no_render
):
    messages = []
    monkeypatch.setattr("immich_memories.cli._trip_generation.print_success", messages.append)
    output = tmp_path / "unrendered.mp4"
    _print_trip_result(
        dry_run=dry_run,
        no_render=no_render,
        location_name="Test destination",
        result_path=output,
        should_upload=True,
        album_name="Test album",
    )
    if dry_run or no_render:
        assert messages == ["Trip plan complete: Test destination"]
    else:
        assert messages == [f"Trip video: {output}", "Uploaded to Immich (album: Test album)"]
