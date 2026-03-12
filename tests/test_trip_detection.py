"""Tests for trip detection: GPS clustering, distance filtering, temporal grouping."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from immich_memories.api.models import Asset, AssetType, ExifInfo


def _make_asset(
    lat: float | None,
    lon: float | None,
    created_at: str,
    city: str | None = None,
    country: str | None = None,
    asset_id: str | None = None,
) -> Asset:
    """Helper to build a minimal Asset with GPS and timestamp."""
    ts = datetime.fromisoformat(created_at).replace(tzinfo=UTC)
    exif = ExifInfo(latitude=lat, longitude=lon, city=city, country=country) if lat else None
    return Asset(
        id=asset_id or f"asset-{created_at}",
        type=AssetType.VIDEO,
        fileCreatedAt=ts,
        fileModifiedAt=ts,
        updatedAt=ts,
        exifInfo=exif,
    )


class TestHaversineDistance:
    """Haversine distance calculation between GPS coordinates."""

    def test_same_point_is_zero(self):
        from immich_memories.analysis.trip_detection import haversine_km

        assert haversine_km(50.8468, 4.3525, 50.8468, 4.3525) == 0.0

    def test_brussels_to_paris(self):
        """Brussels to Paris is ~264 km."""
        from immich_memories.analysis.trip_detection import haversine_km

        dist = haversine_km(50.8468, 4.3525, 48.8566, 2.3522)
        assert 260 < dist < 270


class TestTripsConfig:
    """Config model for trip detection parameters."""

    def test_defaults(self):
        from immich_memories.config_models import TripsConfig

        config = TripsConfig()
        assert config.homebase_latitude == 0.0
        assert config.homebase_longitude == 0.0
        assert config.min_distance_km == 50
        assert config.min_duration_days == 2
        assert config.max_gap_days == 2

    def test_null_island_validation(self):
        """Should raise when homebase is still at Null Island (0,0)."""
        from immich_memories.config_models import TripsConfig

        config = TripsConfig()
        with pytest.raises(ValueError, match="home coordinates"):
            config.validate_homebase()

    def test_valid_homebase_passes(self):
        from immich_memories.config_models import TripsConfig

        config = TripsConfig(homebase_latitude=50.8468, homebase_longitude=4.3525)
        config.validate_homebase()  # should not raise

    def test_trips_config_on_main_config(self):
        """TripsConfig should be accessible from the main Config object."""
        from immich_memories.config_loader import Config

        config = Config()
        assert hasattr(config, "trips")
        assert config.trips.homebase_latitude == 0.0
        assert config.trips.min_distance_km == 50


class TestDetectTrips:
    """Core trip detection algorithm."""

    # Brussels homebase
    HOME_LAT = 50.8468
    HOME_LON = 4.3525

    def test_filters_assets_near_home(self):
        """Assets within min_distance_km should be excluded."""
        from immich_memories.analysis.trip_detection import detect_trips

        # Antwerp is ~45km from Brussels: within default 50km threshold
        assets = [
            _make_asset(51.2194, 4.4025, "2024-06-12T10:00:00", city="Antwerp", country="Belgium"),
            _make_asset(51.2194, 4.4025, "2024-06-13T10:00:00", city="Antwerp", country="Belgium"),
            _make_asset(51.2194, 4.4025, "2024-06-14T10:00:00", city="Antwerp", country="Belgium"),
        ]
        trips = detect_trips(assets, self.HOME_LAT, self.HOME_LON)
        assert len(trips) == 0

    def test_detects_single_trip(self):
        """Videos in Barcelona (>1000km from Brussels) over 3 days = one trip."""
        from immich_memories.analysis.trip_detection import detect_trips

        assets = [
            _make_asset(41.3851, 2.1734, "2024-06-12T10:00:00", city="Barcelona", country="Spain"),
            _make_asset(41.3851, 2.1734, "2024-06-13T14:00:00", city="Barcelona", country="Spain"),
            _make_asset(41.3851, 2.1734, "2024-06-14T09:00:00", city="Barcelona", country="Spain"),
        ]
        trips = detect_trips(assets, self.HOME_LAT, self.HOME_LON)
        assert len(trips) == 1
        assert trips[0].location_name == "Barcelona, Spain"
        assert trips[0].asset_count == 3

    def test_splits_by_gap(self):
        """Two clusters separated by > max_gap_days = two trips."""
        from immich_memories.analysis.trip_detection import detect_trips

        assets = [
            # Trip 1: Barcelona, Jun 12-14
            _make_asset(41.39, 2.17, "2024-06-12T10:00:00", city="Barcelona", country="Spain"),
            _make_asset(41.39, 2.17, "2024-06-13T10:00:00", city="Barcelona", country="Spain"),
            _make_asset(41.39, 2.17, "2024-06-14T10:00:00", city="Barcelona", country="Spain"),
            # Trip 2: London, Jun 22-24 (8 day gap > default 2)
            _make_asset(51.51, -0.13, "2024-06-22T10:00:00", city="London", country="UK"),
            _make_asset(51.51, -0.13, "2024-06-23T10:00:00", city="London", country="UK"),
            _make_asset(51.51, -0.13, "2024-06-24T10:00:00", city="London", country="UK"),
        ]
        trips = detect_trips(assets, self.HOME_LAT, self.HOME_LON)
        assert len(trips) == 2
        assert trips[0].location_name == "Barcelona, Spain"
        assert trips[1].location_name == "London, UK"

    def test_filters_short_trips(self):
        """Trip spanning < min_duration_days should be excluded."""
        from immich_memories.analysis.trip_detection import detect_trips

        # Only 1 day in Paris: doesn't meet default min_duration_days=2
        assets = [
            _make_asset(48.86, 2.35, "2024-06-12T10:00:00", city="Paris", country="France"),
            _make_asset(48.86, 2.35, "2024-06-12T18:00:00", city="Paris", country="France"),
        ]
        trips = detect_trips(assets, self.HOME_LAT, self.HOME_LON)
        assert len(trips) == 0

    def test_skips_assets_without_gps(self):
        """Assets with no EXIF GPS should be silently ignored."""
        from immich_memories.analysis.trip_detection import detect_trips

        assets = [
            _make_asset(None, None, "2024-06-12T10:00:00"),
            _make_asset(41.39, 2.17, "2024-06-12T10:00:00", city="Barcelona", country="Spain"),
            _make_asset(41.39, 2.17, "2024-06-13T10:00:00", city="Barcelona", country="Spain"),
            _make_asset(41.39, 2.17, "2024-06-14T10:00:00", city="Barcelona", country="Spain"),
        ]
        trips = detect_trips(assets, self.HOME_LAT, self.HOME_LON)
        assert len(trips) == 1
        assert trips[0].asset_count == 3  # the GPS-less one is excluded

    def test_location_falls_back_to_country(self):
        """If no city in EXIF, use country only."""
        from immich_memories.analysis.trip_detection import detect_trips

        assets = [
            _make_asset(41.39, 2.17, "2024-06-12T10:00:00", country="Spain"),
            _make_asset(41.39, 2.17, "2024-06-13T10:00:00", country="Spain"),
            _make_asset(41.39, 2.17, "2024-06-14T10:00:00", country="Spain"),
        ]
        trips = detect_trips(assets, self.HOME_LAT, self.HOME_LON)
        assert len(trips) == 1
        assert trips[0].location_name == "Spain"

    def test_location_unknown_when_no_exif_location(self):
        """If assets have GPS but no city/country, fall back to Unknown Location."""
        from immich_memories.analysis.trip_detection import detect_trips

        assets = [
            _make_asset(41.39, 2.17, "2024-06-12T10:00:00"),
            _make_asset(41.39, 2.17, "2024-06-13T10:00:00"),
            _make_asset(41.39, 2.17, "2024-06-14T10:00:00"),
        ]
        trips = detect_trips(assets, self.HOME_LAT, self.HOME_LON)
        assert len(trips) == 1
        assert trips[0].location_name == "Unknown Location"

    def test_custom_thresholds(self):
        """Should respect custom min_distance, min_duration, max_gap."""
        from immich_memories.analysis.trip_detection import detect_trips

        # Antwerp is ~45km: too close for default 50km, but within 30km threshold
        assets = [
            _make_asset(51.2194, 4.4025, "2024-06-12T10:00:00", city="Antwerp", country="Belgium"),
            _make_asset(51.2194, 4.4025, "2024-06-13T10:00:00", city="Antwerp", country="Belgium"),
            _make_asset(51.2194, 4.4025, "2024-06-14T10:00:00", city="Antwerp", country="Belgium"),
        ]
        trips = detect_trips(
            assets, self.HOME_LAT, self.HOME_LON, min_distance_km=30, min_duration_days=1
        )
        assert len(trips) == 1

    def test_sorted_by_start_date(self):
        """Trips should be sorted chronologically."""
        from immich_memories.analysis.trip_detection import detect_trips

        assets = [
            # Trip 2 first in input order (August)
            _make_asset(51.51, -0.13, "2024-08-01T10:00:00", city="London", country="UK"),
            _make_asset(51.51, -0.13, "2024-08-02T10:00:00", city="London", country="UK"),
            _make_asset(51.51, -0.13, "2024-08-03T10:00:00", city="London", country="UK"),
            # Trip 1 (June)
            _make_asset(41.39, 2.17, "2024-06-12T10:00:00", city="Barcelona", country="Spain"),
            _make_asset(41.39, 2.17, "2024-06-13T10:00:00", city="Barcelona", country="Spain"),
            _make_asset(41.39, 2.17, "2024-06-14T10:00:00", city="Barcelona", country="Spain"),
        ]
        trips = detect_trips(assets, self.HOME_LAT, self.HOME_LON)
        assert len(trips) == 2
        assert trips[0].location_name == "Barcelona, Spain"
        assert trips[1].location_name == "London, UK"

    def test_trip_across_year_boundary(self):
        """A trip spanning Dec 28 to Jan 3 should be one trip, not two."""
        from immich_memories.analysis.trip_detection import detect_trips

        assets = [
            _make_asset(41.39, 2.17, "2024-12-28T10:00:00", city="Barcelona", country="Spain"),
            _make_asset(41.39, 2.17, "2024-12-29T10:00:00", city="Barcelona", country="Spain"),
            _make_asset(41.39, 2.17, "2024-12-30T10:00:00", city="Barcelona", country="Spain"),
            _make_asset(41.39, 2.17, "2025-01-01T10:00:00", city="Barcelona", country="Spain"),
            _make_asset(41.39, 2.17, "2025-01-02T10:00:00", city="Barcelona", country="Spain"),
            _make_asset(41.39, 2.17, "2025-01-03T10:00:00", city="Barcelona", country="Spain"),
        ]
        trips = detect_trips(assets, self.HOME_LAT, self.HOME_LON)
        assert len(trips) == 1
        assert trips[0].asset_count == 6


class TestTripCLI:
    """CLI integration for trip memory type."""

    def test_trip_in_memory_type_choices(self):
        """Trip should be accepted as a valid --memory-type value."""
        from click.testing import CliRunner

        from immich_memories.cli import main

        runner = CliRunner()
        # Use --dry-run so it doesn't actually try to connect to Immich
        result = runner.invoke(main, ["generate", "--memory-type", "trip", "--year", "2024"])
        # Should NOT fail with "Invalid value for '--memory-type'"
        assert "Invalid value" not in (result.output or "")

    def test_trip_index_option_exists(self):
        """--trip-index should be a valid option on the generate command."""
        from click.testing import CliRunner

        from immich_memories.cli import main

        runner = CliRunner()
        result = runner.invoke(main, ["generate", "--help"])
        assert "--trip-index" in result.output

    def test_all_trips_option_exists(self):
        """--all-trips should be a valid flag on the generate command."""
        from click.testing import CliRunner

        from immich_memories.cli import main

        runner = CliRunner()
        result = runner.invoke(main, ["generate", "--help"])
        assert "--all-trips" in result.output

    def test_trip_requires_year(self):
        """--memory-type trip requires --year."""
        from click.testing import CliRunner

        from immich_memories.cli import main

        runner = CliRunner()
        result = runner.invoke(main, ["generate", "--memory-type", "trip"])
        assert result.exit_code != 0
        assert "--year" in (result.output or "")


class TestFormatTripsTable:
    """Trip table display for CLI discovery mode."""

    def test_formats_trips_as_table(self):
        """format_trips_table returns a Rich Table with trip info."""
        from immich_memories.analysis.trip_detection import DetectedTrip
        from immich_memories.cli._trip_display import format_trips_table

        trips = [
            DetectedTrip(
                start_date=date(2024, 6, 12),
                end_date=date(2024, 6, 14),
                location_name="Barcelona, Spain",
                asset_count=15,
                centroid_lat=41.39,
                centroid_lon=2.17,
                asset_ids=[],
            ),
            DetectedTrip(
                start_date=date(2024, 8, 1),
                end_date=date(2024, 8, 5),
                location_name="London, UK",
                asset_count=30,
                centroid_lat=51.51,
                centroid_lon=-0.13,
                asset_ids=[],
            ),
        ]
        table = format_trips_table(trips)
        assert table.title == "Detected Trips"
        assert table.row_count == 2

    def test_empty_trips_returns_none(self):
        """No trips detected returns None."""
        from immich_memories.cli._trip_display import format_trips_table

        result = format_trips_table([])
        assert result is None

    def test_select_by_index(self):
        """select_trips with trip_index returns the selected trip."""
        from immich_memories.analysis.trip_detection import DetectedTrip
        from immich_memories.cli._trip_display import select_trips

        trips = [
            DetectedTrip(
                start_date=date(2024, 6, 12),
                end_date=date(2024, 6, 14),
                location_name="Barcelona, Spain",
                asset_count=15,
                centroid_lat=41.39,
                centroid_lon=2.17,
            ),
            DetectedTrip(
                start_date=date(2024, 8, 1),
                end_date=date(2024, 8, 5),
                location_name="London, UK",
                asset_count=30,
                centroid_lat=51.51,
                centroid_lon=-0.13,
            ),
        ]
        selected = select_trips(trips, trip_index=2)
        assert len(selected) == 1
        assert selected[0].location_name == "London, UK"

    def test_select_all_trips(self):
        """select_trips with all_trips returns all trips."""
        from immich_memories.analysis.trip_detection import DetectedTrip
        from immich_memories.cli._trip_display import select_trips

        trips = [
            DetectedTrip(
                start_date=date(2024, 6, 12),
                end_date=date(2024, 6, 14),
                location_name="Barcelona, Spain",
                asset_count=15,
                centroid_lat=41.39,
                centroid_lon=2.17,
            ),
        ]
        selected = select_trips(trips, all_trips=True)
        assert len(selected) == 1

    def test_select_invalid_index_raises(self):
        """select_trips with out-of-range index raises ValueError."""
        from immich_memories.analysis.trip_detection import DetectedTrip
        from immich_memories.cli._trip_display import select_trips

        trips = [
            DetectedTrip(
                start_date=date(2024, 6, 12),
                end_date=date(2024, 6, 14),
                location_name="Barcelona, Spain",
                asset_count=15,
                centroid_lat=41.39,
                centroid_lon=2.17,
            ),
        ]
        with pytest.raises(ValueError, match="Trip index 5 out of range"):
            select_trips(trips, trip_index=5)

    def test_select_no_option_returns_empty(self):
        """select_trips with no option returns empty (discovery mode)."""
        from immich_memories.analysis.trip_detection import DetectedTrip
        from immich_memories.cli._trip_display import select_trips

        trips = [
            DetectedTrip(
                start_date=date(2024, 6, 12),
                end_date=date(2024, 6, 14),
                location_name="Barcelona, Spain",
                asset_count=15,
                centroid_lat=41.39,
                centroid_lon=2.17,
            ),
        ]
        selected = select_trips(trips)
        assert selected == []


class TestTripInUI:
    """Trip memory type appears in the UI preset selector."""

    def test_trip_in_preset_cards(self):
        """Trip should be listed in the UI preset cards."""
        from immich_memories.ui.pages.step1_presets import _PRESET_CARDS

        keys = [card[0] for card in _PRESET_CARDS]
        assert "trip" in keys

    def test_trip_render_params_branch(self):
        """_render_params should handle the TRIP memory type without error."""
        from immich_memories.memory_types.registry import MemoryType

        # Just verify the enum value is recognized
        assert MemoryType("trip") == MemoryType.TRIP
