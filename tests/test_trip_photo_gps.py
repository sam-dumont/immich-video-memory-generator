"""Tests for trip generation with photo-only GPS data (#228)."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch


class TestPhotoOnlyTripNotSkipped:
    """A trip with photos but no videos should not be skipped at the gate."""

    def test_pipeline_does_not_exit_when_photos_available(self):
        """run_pipeline_and_generate should not sys.exit when clips=[] but photos exist."""

        with (
            patch("immich_memories.cli._pipeline_runner.print_error") as mock_error,
            patch(
                "immich_memories.cli._pipeline_runner.sys.exit",
                side_effect=SystemExit(1),
            ),
            patch("immich_memories.generate.assets_to_clips", return_value=[]),
        ):
            from immich_memories.cli._pipeline_runner import run_pipeline_and_generate

            try:
                run_pipeline_and_generate(
                    assets=[],
                    photo_assets=[MagicMock()],
                    include_photos=True,
                    client=MagicMock(),
                    config=MagicMock(),
                    progress=MagicMock(),
                    duration=60.0,
                    transition="smart",
                    music=None,
                    output_path=MagicMock(),
                    memory_type="trip",
                    person_names=[],
                    date_range=MagicMock(),
                    upload_to_immich=False,
                    album=None,
                )
            except (SystemExit, Exception):
                pass

            # The "no usable content" gate should NOT fire
            no_content_calls = [
                c for c in mock_error.call_args_list if "No usable content" in str(c)
            ]
            assert not no_content_calls, (
                "Should not exit with 'No usable content' when photos are available"
            )

    def test_pipeline_exits_when_nothing_available(self):
        """run_pipeline_and_generate should sys.exit when no clips AND no photos."""
        with (
            patch("immich_memories.cli._pipeline_runner.print_error") as mock_error,
            patch(
                "immich_memories.cli._pipeline_runner.sys.exit",
                side_effect=SystemExit(1),
            ),
            patch("immich_memories.generate.assets_to_clips", return_value=[]),
        ):
            from immich_memories.cli._pipeline_runner import run_pipeline_and_generate

            try:
                run_pipeline_and_generate(
                    assets=[],
                    photo_assets=None,
                    include_photos=False,
                    client=MagicMock(),
                    config=MagicMock(),
                    progress=MagicMock(),
                    duration=60.0,
                    transition="smart",
                    music=None,
                    output_path=MagicMock(),
                    memory_type="trip",
                    person_names=[],
                    date_range=MagicMock(),
                    upload_to_immich=False,
                    album=None,
                )
            except SystemExit:
                pass

            no_content_calls = [
                c for c in mock_error.call_args_list if "No usable content" in str(c)
            ]
            assert no_content_calls, "Should exit with 'No usable content' when nothing available"


class TestTripGenerationPhotoFetch:
    """Trip generation should fetch photos before checking for empty content."""

    def test_photo_only_trip_not_skipped(self):
        """handle_trip_generation continues when trip has photos but no videos."""
        from immich_memories.analysis.trip_detection import DetectedTrip

        mock_trip = DetectedTrip(
            location_name="Aosta Valley",
            start_date=date(2021, 7, 10),
            end_date=date(2021, 7, 17),
            asset_count=108,
            centroid_lat=45.7,
            centroid_lon=7.3,
        )

        mock_client = MagicMock()
        # WHY: photo must have GPS far from home to survive the trip filter
        mock_photo = MagicMock()
        mock_photo.exif_info.latitude = 45.7
        mock_photo.exif_info.longitude = 7.3
        mock_client.get_photos_for_date_range.return_value = [mock_photo]

        with (
            patch(
                "immich_memories.cli._trip_generation.fetch_videos_and_live_photos",
                return_value=([], []),
            ),
            patch(
                "immich_memories.cli._trip_generation.run_pipeline_and_generate",
            ) as mock_generate,
            patch(
                "immich_memories.cli._trip_display.run_trip_detection",
                return_value=[mock_trip],
            ),
            patch(
                "immich_memories.cli._trip_display.format_trips_table",
                return_value="table",
            ),
            patch(
                "immich_memories.cli._trip_display.select_trips",
                return_value=[mock_trip],
            ),
        ):
            from immich_memories.cli._trip_generation import handle_trip_generation

            mock_generate.return_value = (MagicMock(), False, None)

            mock_config = MagicMock()
            mock_config.trips.homebase_latitude = 50.85
            mock_config.trips.homebase_longitude = 4.35
            mock_config.trips.min_distance_km = 50
            mock_config.defaults.transition = "smart"

            handle_trip_generation(
                client=mock_client,
                config=mock_config,
                progress=MagicMock(),
                year=2021,
                month=7,
                trip_index=None,
                all_trips=True,
                near_date=None,
                person_names=[],
                output_path=MagicMock(),
                use_live_photos=False,
                use_photos=True,
                effective_analysis_depth="fast",
                transition="smart",
                music=None,
                music_volume=0.5,
                no_music=True,
                resolution="auto",
                scale_mode=None,
                output_format=None,
                add_date=False,
                keep_intermediates=False,
                privacy_mode=False,
                title_override=None,
                subtitle_override=None,
                upload_to_immich=False,
                album=None,
            )

            # Photos fetched and pipeline called (not skipped)
            mock_client.get_photos_for_date_range.assert_called_once()
            mock_generate.assert_called_once()

    def test_empty_trip_skipped_gracefully(self):
        """Trip with no videos, no live photos, and no photos → skipped with error."""
        from immich_memories.analysis.trip_detection import DetectedTrip

        mock_trip = DetectedTrip(
            location_name="Ghost Town",
            start_date=date(2021, 7, 10),
            end_date=date(2021, 7, 17),
            asset_count=0,
            centroid_lat=0.0,
            centroid_lon=0.0,
        )

        mock_client = MagicMock()
        mock_client.get_photos_for_date_range.return_value = []

        with (
            patch(
                "immich_memories.cli._trip_generation.fetch_videos_and_live_photos",
                return_value=([], []),
            ),
            patch(
                "immich_memories.cli._trip_generation.run_pipeline_and_generate",
            ) as mock_generate,
            patch(
                "immich_memories.cli._trip_display.run_trip_detection",
                return_value=[mock_trip],
            ),
            patch(
                "immich_memories.cli._trip_display.format_trips_table",
                return_value="table",
            ),
            patch(
                "immich_memories.cli._trip_display.select_trips",
                return_value=[mock_trip],
            ),
            patch("immich_memories.cli._trip_generation.print_error") as mock_error,
        ):
            from immich_memories.cli._trip_generation import handle_trip_generation

            handle_trip_generation(
                client=mock_client,
                config=MagicMock(),
                progress=MagicMock(),
                year=2021,
                month=7,
                trip_index=None,
                all_trips=True,
                near_date=None,
                person_names=[],
                output_path=MagicMock(),
                use_live_photos=False,
                use_photos=True,
                effective_analysis_depth="fast",
                transition="smart",
                music=None,
                music_volume=0.5,
                no_music=True,
                resolution="auto",
                scale_mode=None,
                output_format=None,
                add_date=False,
                keep_intermediates=False,
                privacy_mode=False,
                title_override=None,
                subtitle_override=None,
                upload_to_immich=False,
                album=None,
            )

            # Pipeline should NOT be called — no content
            mock_generate.assert_not_called()
            # Error message should mention "No content"
            mock_error.assert_called_once()
            assert "No content" in str(mock_error.call_args)
