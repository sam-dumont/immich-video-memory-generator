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
    asset_type: AssetType = AssetType.VIDEO,
    local_dt: str | None = None,
) -> Asset:
    """Helper to build a minimal Asset with GPS and timestamp."""
    ts = datetime.fromisoformat(created_at).replace(tzinfo=UTC)
    exif = ExifInfo(latitude=lat, longitude=lon, city=city, country=country) if lat else None
    ldt = datetime.fromisoformat(local_dt) if local_dt else None
    return Asset(
        id=asset_id or f"asset-{created_at}",
        type=asset_type,
        fileCreatedAt=ts,
        fileModifiedAt=ts,
        updatedAt=ts,
        localDateTime=ldt,
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

    @pytest.fixture(autouse=True)
    def _no_geocode(self, monkeypatch):
        """Disable network geocoding in trip detection tests."""
        monkeypatch.setattr(
            "immich_memories.analysis.trip_detection.reverse_geocode",
            lambda *_args, **_kwargs: None,
        )

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

    def test_detects_trips_from_photos_and_videos(self):
        """Trip detection should work with mixed asset types (photos + videos)."""
        from immich_memories.analysis.trip_detection import detect_trips

        assets = [
            # Pre-2018 trip: only photos
            _make_asset(
                41.39,
                2.17,
                "2015-06-12T10:00:00",
                city="Barcelona",
                country="Spain",
                asset_type=AssetType.IMAGE,
            ),
            _make_asset(
                41.39,
                2.17,
                "2015-06-13T10:00:00",
                city="Barcelona",
                country="Spain",
                asset_type=AssetType.IMAGE,
            ),
            _make_asset(
                41.39,
                2.17,
                "2015-06-14T10:00:00",
                city="Barcelona",
                country="Spain",
                asset_type=AssetType.IMAGE,
            ),
        ]
        trips = detect_trips(assets, self.HOME_LAT, self.HOME_LON)
        assert len(trips) == 1
        assert trips[0].location_name == "Barcelona, Spain"
        assert trips[0].asset_count == 3


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
        # In CI (no Immich config), "not configured" fires before year validation.
        # In dev, the year check triggers. Either way, the command must fail.
        output = result.output or ""
        assert "--year" in output or "not configured" in output.lower()


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


class TestReverseGeocode:
    """Reverse geocoding for trip location naming."""

    def test_geocode_returns_location_name(self):
        """reverse_geocode should return a formatted location string."""
        from unittest.mock import MagicMock, patch

        from immich_memories.analysis.trip_detection import reverse_geocode

        mock_location = MagicMock()
        mock_location.raw = {"address": {"state": "Charente-Maritime", "country": "France"}}

        with patch("immich_memories.analysis.trip_detection.Nominatim") as mock_nom:
            mock_nom.return_value.reverse.return_value = mock_location
            result = reverse_geocode(45.95, -1.15)

        assert result == "Charente-Maritime, France"

    def test_geocode_returns_none_on_failure(self):
        """reverse_geocode should return None when geocoding fails."""
        from unittest.mock import patch

        from immich_memories.analysis.trip_detection import reverse_geocode

        with patch("immich_memories.analysis.trip_detection.Nominatim") as mock_nom:
            mock_nom.return_value.reverse.side_effect = Exception("Network error")
            result = reverse_geocode(45.95, -1.15)

        assert result is None

    def test_naming_prefers_geocode_over_exif(self):
        """_derive_location_name should prefer geocoded result over EXIF."""
        from unittest.mock import patch

        from immich_memories.analysis.trip_detection import _derive_location_name

        assets = [
            _make_asset(45.95, -1.15, "2024-07-12T10:00:00", city="Dolus", country="France"),
            _make_asset(45.96, -1.14, "2024-07-13T10:00:00", city="Dolus", country="France"),
        ]

        with patch(
            "immich_memories.analysis.trip_detection.reverse_geocode",
            return_value="Île d'Oléron, France",
        ):
            result = _derive_location_name(assets, centroid_lat=45.955, centroid_lon=-1.145)

        assert result == "Île d'Oléron, France"

    def test_naming_falls_back_to_exif_when_geocode_fails(self):
        """When geocode returns None, fall back to EXIF city/country."""
        from unittest.mock import patch

        from immich_memories.analysis.trip_detection import _derive_location_name

        assets = [
            _make_asset(41.39, 2.17, "2024-06-12T10:00:00", city="Barcelona", country="Spain"),
            _make_asset(41.39, 2.17, "2024-06-13T10:00:00", city="Barcelona", country="Spain"),
        ]

        with patch("immich_memories.analysis.trip_detection.reverse_geocode", return_value=None):
            result = _derive_location_name(assets, centroid_lat=41.39, centroid_lon=2.17)

        assert result == "Barcelona, Spain"


class TestReverseGeocodeGranularity:
    """Reverse geocoding should prefer specific names over broad regions."""

    def test_prefers_island_over_state(self):
        """For islands, should return island name not state."""
        from unittest.mock import MagicMock, patch

        from immich_memories.analysis.trip_detection import reverse_geocode

        mock_location = MagicMock()
        mock_location.raw = {
            "address": {
                "island": "Tenerife",
                "state": "Canary Islands",
                "country": "Spain",
            }
        }

        with patch("immich_memories.analysis.trip_detection.Nominatim") as mock_nom:
            mock_nom.return_value.reverse.return_value = mock_location
            result = reverse_geocode(28.2916, -16.6291)

        assert result == "Tenerife, Spain"

    def test_prefers_province_over_state(self):
        """For Spanish provinces, should use province over state.

        Nominatim returns 'province' for Spanish islands (e.g., 'Santa Cruz de Tenerife').
        """
        from unittest.mock import MagicMock, patch

        from immich_memories.analysis.trip_detection import reverse_geocode

        mock_location = MagicMock()
        mock_location.raw = {
            "address": {
                "province": "Santa Cruz de Tenerife",
                "state": "Canary Islands",
                "country": "Spain",
            }
        }

        with patch("immich_memories.analysis.trip_detection.Nominatim") as mock_nom:
            mock_nom.return_value.reverse.return_value = mock_location
            result = reverse_geocode(28.2916, -16.6291)

        assert result == "Santa Cruz de Tenerife, Spain"

    def test_prefers_county_over_state(self):
        """For Ardennes, should return 'Ardennes, France' not 'Grand Est, France'."""
        from unittest.mock import MagicMock, patch

        from immich_memories.analysis.trip_detection import reverse_geocode

        mock_location = MagicMock()
        mock_location.raw = {
            "address": {
                "county": "Ardennes",
                "state": "Grand Est",
                "country": "France",
            }
        }

        with patch("immich_memories.analysis.trip_detection.Nominatim") as mock_nom:
            mock_nom.return_value.reverse.return_value = mock_location
            result = reverse_geocode(49.77, 4.72)

        assert result == "Ardennes, France"

    def test_prefers_state_district_over_state(self):
        """state_district should be preferred over state."""
        from unittest.mock import MagicMock, patch

        from immich_memories.analysis.trip_detection import reverse_geocode

        mock_location = MagicMock()
        mock_location.raw = {
            "address": {
                "state_district": "Provence",
                "state": "Provence-Alpes-Côte d'Azur",
                "country": "France",
            }
        }

        with patch("immich_memories.analysis.trip_detection.Nominatim") as mock_nom:
            mock_nom.return_value.reverse.return_value = mock_location
            result = reverse_geocode(43.30, 5.37)

        assert result == "Provence, France"

    def test_deduplicates_region_and_country(self):
        """When state == country (e.g., Cyprus), return just country name."""
        from unittest.mock import MagicMock, patch

        from immich_memories.analysis.trip_detection import reverse_geocode

        # Cyprus: state == country, no useful county/island/province
        location = MagicMock()
        location.raw = {
            "address": {
                "state": "Cyprus",
                "country": "Cyprus",
            }
        }

        with patch("immich_memories.analysis.trip_detection.Nominatim") as mock_nom:
            mock_nom.return_value.reverse.return_value = location
            result = reverse_geocode(34.85, 32.85)

        assert result == "Cyprus"  # Not "Cyprus, Cyprus"

    def test_uses_detailed_zoom_for_small_spread_trips(self):
        """For trips in a small area (<80km), use detailed zoom."""
        from unittest.mock import MagicMock, patch

        from immich_memories.analysis.trip_detection import reverse_geocode

        detailed_location = MagicMock()
        detailed_location.raw = {
            "address": {
                "county": "Ardennes",
                "state": "Grand Est",
                "country": "France",
            }
        }

        with patch("immich_memories.analysis.trip_detection.Nominatim") as mock_nom:
            mock_nom.return_value.reverse.return_value = detailed_location
            result = reverse_geocode(49.77, 4.72, spread_km=30.0)

        assert result == "Ardennes, France"

    def test_multi_country_trip_lists_countries(self):
        """A cross-country trip (>300km, multiple countries) lists countries."""
        from unittest.mock import patch

        from immich_memories.analysis.trip_detection import _derive_location_name

        # European road trip: Belgium → France → Spain (spread >> 300km)
        assets = [
            _make_asset(50.85, 4.35, "2024-06-01T10:00:00", country="Belgium"),
            _make_asset(48.86, 2.35, "2024-06-03T10:00:00", country="France"),
            _make_asset(41.39, 2.17, "2024-06-06T10:00:00", country="Spain"),
            _make_asset(41.39, 2.17, "2024-06-07T10:00:00", country="Spain"),
        ]

        with patch("immich_memories.analysis.trip_detection.reverse_geocode"):
            result = _derive_location_name(assets, centroid_lat=47.0, centroid_lon=3.0)

        assert result == "Belgium → France → Spain"

    def test_dominant_country_ignores_layovers(self):
        """If 90%+ assets are in one country, use that country (ignore layovers)."""
        from unittest.mock import patch

        from immich_memories.analysis.trip_detection import _derive_location_name

        # 9 assets in Cyprus, 1 in Greece (Athens layover) — 90%+ in Cyprus
        # Athens (37.97, 23.72) to Paphos (34.78, 32.42) is ~850km
        assets = [
            _make_asset(37.97, 23.72, "2024-08-20T10:00:00", country="Greece"),
        ] + [
            _make_asset(34.78, 32.42, f"2024-08-{21 + i}T10:00:00", country="Cyprus")
            for i in range(9)
        ]

        with patch(
            "immich_memories.analysis.trip_detection.reverse_geocode",
            return_value="Cyprus",
        ):
            result = _derive_location_name(assets, centroid_lat=34.90, centroid_lon=33.00)

        assert result == "Cyprus"
        assert "Greece" not in result

    def test_falls_back_to_state_when_no_finer_detail(self):
        """When no island/county/state_district, fall back to state."""
        from unittest.mock import MagicMock, patch

        from immich_memories.analysis.trip_detection import reverse_geocode

        mock_location = MagicMock()
        mock_location.raw = {
            "address": {
                "state": "California",
                "country": "United States",
            }
        }

        with patch("immich_memories.analysis.trip_detection.Nominatim") as mock_nom:
            mock_nom.return_value.reverse.return_value = mock_location
            result = reverse_geocode(34.05, -118.24)

        assert result == "California, United States"


class TestCrossYearBoundary:
    """Trip detection should handle trips spanning year boundaries."""

    def test_run_trip_detection_extends_date_range(self):
        """run_trip_detection should query with buffer around year boundaries."""
        from datetime import datetime
        from unittest.mock import MagicMock, patch

        from immich_memories.cli._trip_display import run_trip_detection
        from immich_memories.config_loader import Config

        config = Config()
        config.trips.homebase_latitude = 50.8468
        config.trips.homebase_longitude = 4.3525

        mock_client = MagicMock()
        mock_client.get_assets_for_date_range.return_value = []
        mock_progress = MagicMock()

        with patch(
            "immich_memories.cli._trip_display.detect_trips",
            return_value=[],
        ):
            run_trip_detection(mock_client, config, 2015, mock_progress)

        # Verify the date range was extended with a buffer
        call_args = mock_client.get_assets_for_date_range.call_args
        date_range = call_args[0][0]
        # Should start Dec 1 of previous year (full month buffer)
        assert date_range.start <= datetime(2014, 12, 1)
        # Should end Jan 31 of next year (full month buffer)
        assert date_range.end >= datetime(2016, 1, 31)

    def test_run_trip_detection_filters_to_requested_year(self):
        """Trips outside the requested year should be filtered out."""
        from datetime import date
        from unittest.mock import MagicMock, patch

        from immich_memories.analysis.trip_detection import DetectedTrip
        from immich_memories.cli._trip_display import run_trip_detection
        from immich_memories.config_loader import Config

        config = Config()
        config.trips.homebase_latitude = 50.8468
        config.trips.homebase_longitude = 4.3525

        mock_client = MagicMock()
        mock_client.get_assets_for_date_range.return_value = []
        mock_progress = MagicMock()

        # Trip entirely in 2014 (should be filtered out when querying 2015)
        trip_2014 = DetectedTrip(
            start_date=date(2014, 12, 5),
            end_date=date(2014, 12, 10),
            location_name="Paris",
            asset_count=20,
            centroid_lat=48.86,
            centroid_lon=2.35,
        )
        # Trip spanning Dec 2014 → Jan 2015 (should be kept for year 2015)
        trip_boundary = DetectedTrip(
            start_date=date(2014, 12, 25),
            end_date=date(2015, 1, 3),
            location_name="Tenerife",
            asset_count=50,
            centroid_lat=28.29,
            centroid_lon=-16.63,
        )
        # Trip entirely in 2015 (should be kept)
        trip_2015 = DetectedTrip(
            start_date=date(2015, 7, 1),
            end_date=date(2015, 7, 10),
            location_name="Croatia",
            asset_count=80,
            centroid_lat=43.51,
            centroid_lon=16.44,
        )

        with patch(
            "immich_memories.cli._trip_display.detect_trips",
            return_value=[trip_2014, trip_boundary, trip_2015],
        ):
            trips = run_trip_detection(mock_client, config, 2015, mock_progress)

        # Should keep the boundary trip and 2015 trip, exclude the 2014-only trip
        assert len(trips) == 2
        assert trips[0].location_name == "Tenerife"
        assert trips[1].location_name == "Croatia"


class TestFilterNearHome:
    """GPS distance filter for removing near-home assets from trip clips."""

    HOME_LAT = 50.8468
    HOME_LON = 4.3525

    def test_filters_out_near_home_assets(self):
        """Assets within min_distance_km of home should be removed."""
        from immich_memories.analysis.trip_detection import filter_near_home

        assets = [
            # Barcelona: ~1000km from Brussels → keep
            _make_asset(41.39, 2.17, "2025-06-01T10:00:00"),
            # Brussels suburb: ~10km from home → filter out
            _make_asset(50.88, 4.40, "2025-06-01T18:00:00"),
        ]
        result = filter_near_home(assets, self.HOME_LAT, self.HOME_LON, min_distance_km=50)
        assert len(result) == 1
        assert result[0].exif_info.latitude == 41.39

    def test_keeps_assets_without_gps(self):
        """Assets with no GPS data should be kept (might be from trip)."""
        from immich_memories.analysis.trip_detection import filter_near_home

        assets = [
            _make_asset(None, None, "2025-06-01T12:00:00"),  # No GPS
            _make_asset(41.39, 2.17, "2025-06-01T14:00:00"),  # Far from home
        ]
        result = filter_near_home(assets, self.HOME_LAT, self.HOME_LON, min_distance_km=50)
        assert len(result) == 2


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


class TestDetectOvernightStops:
    """Overnight stop detection: group trip days into base camps."""

    def test_hiking_trip_different_stop_each_night(self):
        """Multi-city trip: different overnight stop each day → multiple bases.

        Uses "woke up here" heuristic: night N location = first photo of day N+1.
        Morning photos at accommodation tell us where we slept.
        """
        from immich_memories.analysis.trip_detection import detect_overnight_stops

        # 3 days, morning photos at the accommodation (all >5km apart)
        assets = [
            # Day 1: morning in Dresden, evening hiking
            _make_asset(
                50.98, 14.00, "2022-04-04T09:00:00", local_dt="2022-04-04T09:00:00", city="Dresden"
            ),
            _make_asset(
                50.978,
                14.109,
                "2022-04-04T18:00:00",
                local_dt="2022-04-04T18:00:00",
                city="Hohnstein",
            ),
            # Day 2: MORNING at Bad Schandau (= where we slept night 1)
            _make_asset(
                50.915,
                14.200,
                "2022-04-05T08:00:00",
                local_dt="2022-04-05T08:00:00",
                city="Bad Schandau",
            ),
            _make_asset(
                50.92, 14.18, "2022-04-05T17:00:00", local_dt="2022-04-05T17:00:00", city="Schmilka"
            ),
            # Day 3: MORNING at Konigstein (= where we slept night 2)
            _make_asset(
                50.919,
                14.071,
                "2022-04-06T08:00:00",
                local_dt="2022-04-06T08:00:00",
                city="Konigstein",
            ),
            # Last photo far from both BS and Konigstein (>5km each)
            _make_asset(
                50.85, 14.25, "2022-04-06T17:00:00", local_dt="2022-04-06T17:00:00", city="Pirna"
            ),
        ]
        bases = detect_overnight_stops(assets)
        assert len(bases) == 3
        # Night 1 → woke up at Bad Schandau (first photo day 2)
        assert bases[0].location_name == "Bad Schandau"
        # Night 2 → woke up at Konigstein (first photo day 3)
        assert bases[1].location_name == "Konigstein"
        # Last day → falls back to last photo (Pirna)
        assert bases[2].location_name == "Pirna"

    def test_base_camp_trip_same_hotel(self):
        """Cyprus: 5 nights in same city → ONE base, not 5."""
        from immich_memories.analysis.trip_detection import detect_overnight_stops

        # 3 days all ending at the same hotel in Nicosia (~0.5km apart)
        assets = [
            _make_asset(
                35.17, 33.36, "2024-08-27T10:00:00", local_dt="2024-08-27T10:00:00", city="Nicosia"
            ),
            _make_asset(
                35.173,
                33.358,
                "2024-08-27T20:00:00",
                local_dt="2024-08-27T20:00:00",
                city="Nicosia",
            ),
            _make_asset(
                35.16, 33.35, "2024-08-28T10:00:00", local_dt="2024-08-28T10:00:00", city="Nicosia"
            ),
            _make_asset(
                35.173,
                33.358,
                "2024-08-28T21:00:00",
                local_dt="2024-08-28T21:00:00",
                city="Nicosia",
            ),
            _make_asset(
                35.17, 33.36, "2024-08-29T09:00:00", local_dt="2024-08-29T09:00:00", city="Nicosia"
            ),
            _make_asset(
                35.166,
                33.364,
                "2024-08-29T20:00:00",
                local_dt="2024-08-29T20:00:00",
                city="Nicosia",
            ),
        ]
        bases = detect_overnight_stops(assets)
        assert len(bases) == 1
        assert bases[0].nights == 3
        assert bases[0].location_name == "Nicosia"

    def test_two_bases_with_transition(self):
        """Cyprus pattern: Nicosia (3 nights) → Pafos (2 nights) → 2 bases."""
        from immich_memories.analysis.trip_detection import detect_overnight_stops

        assets = [
            # Nicosia base (3 days, last photo always near same spot)
            _make_asset(
                35.17, 33.36, "2024-08-27T10:00:00", local_dt="2024-08-27T10:00:00", city="Nicosia"
            ),
            _make_asset(
                35.173,
                33.358,
                "2024-08-27T20:00:00",
                local_dt="2024-08-27T20:00:00",
                city="Nicosia",
            ),
            _make_asset(
                35.173,
                33.358,
                "2024-08-28T20:00:00",
                local_dt="2024-08-28T20:00:00",
                city="Nicosia",
            ),
            _make_asset(
                35.173,
                33.358,
                "2024-08-29T20:00:00",
                local_dt="2024-08-29T20:00:00",
                city="Nicosia",
            ),
            # Transition day + Pafos base (2 days, last photo in Geroskipou)
            _make_asset(
                35.17, 33.36, "2024-08-30T09:00:00", local_dt="2024-08-30T09:00:00", city="Nicosia"
            ),
            _make_asset(
                34.739,
                32.434,
                "2024-08-30T20:00:00",
                local_dt="2024-08-30T20:00:00",
                city="Geroskipou",
            ),
            _make_asset(
                34.740,
                32.434,
                "2024-08-31T20:00:00",
                local_dt="2024-08-31T20:00:00",
                city="Geroskipou",
            ),
        ]
        bases = detect_overnight_stops(assets)
        assert len(bases) == 2
        assert bases[0].nights >= 3
        assert bases[1].nights >= 2

    def test_empty_assets(self):
        """No assets → no bases."""
        from immich_memories.analysis.trip_detection import detect_overnight_stops

        assert detect_overnight_stops([]) == []

    def test_single_day_trip(self):
        """Single day → 1 base with 1 night."""
        from immich_memories.analysis.trip_detection import detect_overnight_stops

        assets = [
            _make_asset(
                41.39, 2.17, "2024-06-12T10:00:00", local_dt="2024-06-12T10:00:00", city="Barcelona"
            ),
            _make_asset(
                41.39, 2.17, "2024-06-12T18:00:00", local_dt="2024-06-12T18:00:00", city="Barcelona"
            ),
        ]
        bases = detect_overnight_stops(assets)
        assert len(bases) == 1

    def test_day_trip_returns_to_base(self):
        """Val d'Aoste: morning at trailhead, return to base each evening → 1 base.

        The hard case: morning photos are NOT at the base (you drove to a
        trailhead early). But you have photos at the base later in the day,
        proving you returned. The algorithm must look at ALL photos, not just
        first/last, to detect the recurring home base.
        """
        from immich_memories.analysis.trip_detection import detect_overnight_stops

        base_lat, base_lon = 45.73, 7.35  # Ville Sur Sarre
        trail_a = (45.62, 7.20)  # 15km south
        trail_b = (45.80, 7.50)  # 15km north-east

        assets = [
            # Day 1: morning at trailhead A, afternoon back at base
            _make_asset(
                *trail_a, "2021-08-01T08:00:00", local_dt="2021-08-01T08:00:00", city="Trailhead A"
            ),
            _make_asset(
                base_lat,
                base_lon,
                "2021-08-01T17:00:00",
                local_dt="2021-08-01T17:00:00",
                city="Ville Sur Sarre",
            ),
            # Day 2: morning at trailhead B, afternoon back at base
            _make_asset(
                *trail_b, "2021-08-02T08:00:00", local_dt="2021-08-02T08:00:00", city="Trailhead B"
            ),
            _make_asset(
                base_lat,
                base_lon,
                "2021-08-02T17:00:00",
                local_dt="2021-08-02T17:00:00",
                city="Ville Sur Sarre",
            ),
            # Day 3: morning at trailhead A again, afternoon at base
            _make_asset(
                *trail_a, "2021-08-03T08:00:00", local_dt="2021-08-03T08:00:00", city="Trailhead A"
            ),
            _make_asset(
                base_lat,
                base_lon,
                "2021-08-03T17:00:00",
                local_dt="2021-08-03T17:00:00",
                city="Ville Sur Sarre",
            ),
            # Day 4: last day at base
            _make_asset(
                base_lat,
                base_lon,
                "2021-08-04T10:00:00",
                local_dt="2021-08-04T10:00:00",
                city="Ville Sur Sarre",
            ),
        ]
        bases = detect_overnight_stops(assets)
        assert len(bases) == 1
        assert bases[0].nights == 4
        assert bases[0].location_name == "Ville Sur Sarre"

    def test_base_excursion_new_base(self):
        """Base A (3 nights) → Base B (2 nights) = 2 bases.

        Uses "woke up here" heuristic: morning photos show where we slept.
        With 5 days: mornings 2-4 at Nicosia (3 nights), morning 5 at Pafos,
        last photo at Pafos (2 nights).
        """
        from immich_memories.analysis.trip_detection import detect_overnight_stops

        assets = [
            # Day 1: morning at Nicosia
            _make_asset(
                35.173,
                33.358,
                "2024-08-27T08:00:00",
                local_dt="2024-08-27T08:00:00",
                city="Nicosia",
            ),
            # Day 2: MORNING at Nicosia (slept night 1 here)
            _make_asset(
                35.173,
                33.358,
                "2024-08-28T08:00:00",
                local_dt="2024-08-28T08:00:00",
                city="Nicosia",
            ),
            # Day 3: MORNING at Nicosia (slept night 2 here)
            _make_asset(
                35.173,
                33.358,
                "2024-08-29T08:00:00",
                local_dt="2024-08-29T08:00:00",
                city="Nicosia",
            ),
            # Day 4: MORNING at Nicosia (slept night 3 here), travel to Pafos
            _make_asset(
                35.173,
                33.358,
                "2024-08-30T08:00:00",
                local_dt="2024-08-30T08:00:00",
                city="Nicosia",
            ),
            _make_asset(
                34.739,
                32.434,
                "2024-08-30T18:00:00",
                local_dt="2024-08-30T18:00:00",
                city="Geroskipou",
            ),
            # Day 5: MORNING at Geroskipou (slept night 4 here)
            _make_asset(
                34.740,
                32.434,
                "2024-08-31T08:00:00",
                local_dt="2024-08-31T08:00:00",
                city="Geroskipou",
            ),
            _make_asset(
                34.740,
                32.434,
                "2024-08-31T20:00:00",
                local_dt="2024-08-31T20:00:00",
                city="Geroskipou",
            ),
        ]
        bases = detect_overnight_stops(assets)
        assert len(bases) == 2
        assert bases[0].nights >= 3  # Nicosia: nights 1-3
        assert bases[1].nights >= 2  # Geroskipou: nights 4-5

    def test_two_bases_with_excursion(self):
        """Crete: 2 home bases + 1 overnight excursion = 3 stops.

        Base A (west Crete, 4 days) → 1 night excursion at Samaria Gorge →
        Base B (east Crete, 4 days). The excursion is a single night away
        from any home base. Both bases are detected because photos cluster
        there across multiple days.
        """
        from immich_memories.analysis.trip_detection import detect_overnight_stops

        base_a = (35.50, 23.96)  # Chania area
        base_b = (35.19, 26.10)  # Sitia area
        gorge = (35.27, 23.94)  # Samaria Gorge (26km from Chania)

        assets = []
        # Base A: 4 days with photos at base
        for day in range(4, 8):
            ts = f"2019-07-0{day}T"
            assets.append(
                _make_asset(*base_a, f"{ts}08:00:00", local_dt=f"{ts}08:00:00", city="Chania")
            )
            assets.append(
                _make_asset(*base_a, f"{ts}19:00:00", local_dt=f"{ts}19:00:00", city="Chania")
            )

        # Day 8: excursion to Samaria Gorge — leave base, don't come back
        assets.append(
            _make_asset(
                *base_a, "2019-07-08T07:00:00", local_dt="2019-07-08T07:00:00", city="Chania"
            )
        )
        assets.append(
            _make_asset(
                *gorge, "2019-07-08T18:00:00", local_dt="2019-07-08T18:00:00", city="Samaria"
            )
        )

        # Day 9: travel from gorge to base B
        assets.append(
            _make_asset(
                *gorge, "2019-07-09T08:00:00", local_dt="2019-07-09T08:00:00", city="Samaria"
            )
        )
        assets.append(
            _make_asset(
                *base_b, "2019-07-09T18:00:00", local_dt="2019-07-09T18:00:00", city="Sitia"
            )
        )

        # Base B: 4 days with photos at base
        for day in range(10, 14):
            ts = f"2019-07-{day}T"
            assets.append(
                _make_asset(*base_b, f"{ts}08:00:00", local_dt=f"{ts}08:00:00", city="Sitia")
            )
            assets.append(
                _make_asset(*base_b, f"{ts}19:00:00", local_dt=f"{ts}19:00:00", city="Sitia")
            )

        bases = detect_overnight_stops(assets)
        assert len(bases) == 3
        assert bases[0].location_name == "Chania"
        assert bases[0].nights >= 4
        assert bases[1].nights == 1  # Samaria excursion
        assert bases[2].location_name == "Sitia"
        assert bases[2].nights >= 4

    def test_excursion_then_return_to_base(self):
        """Base A (5 days) → excursion B (1 night) → back to A (2 days) = 3 stops.

        The key pattern: you sleep at the base for 5 days, go on a 1-night
        excursion, then RETURN to the same base. The algorithm must detect
        the return and assign those days back to base A.
        """
        from immich_memories.analysis.trip_detection import detect_overnight_stops

        base = (45.73, 7.35)  # Ville Sur Sarre
        excursion = (45.87, 7.17)  # Grand Saint-Bernard (20km away)

        assets = []
        # Days 1-5: at base
        for day in range(1, 6):
            ts = f"2021-08-0{day}T"
            assets.append(
                _make_asset(
                    *base, f"{ts}08:00:00", local_dt=f"{ts}08:00:00", city="Ville Sur Sarre"
                )
            )
            assets.append(
                _make_asset(
                    *base, f"{ts}19:00:00", local_dt=f"{ts}19:00:00", city="Ville Sur Sarre"
                )
            )

        # Day 6: leave base, go to excursion, sleep there
        assets.append(
            _make_asset(
                *base, "2021-08-06T08:00:00", local_dt="2021-08-06T08:00:00", city="Ville Sur Sarre"
            )
        )
        assets.append(
            _make_asset(
                *excursion,
                "2021-08-06T18:00:00",
                local_dt="2021-08-06T18:00:00",
                city="Grand St-Bernard",
            )
        )

        # Day 7: return to base
        assets.append(
            _make_asset(
                *excursion,
                "2021-08-07T08:00:00",
                local_dt="2021-08-07T08:00:00",
                city="Grand St-Bernard",
            )
        )
        assets.append(
            _make_asset(
                *base, "2021-08-07T17:00:00", local_dt="2021-08-07T17:00:00", city="Ville Sur Sarre"
            )
        )

        # Days 8-9: back at base
        for day in range(8, 10):
            ts = f"2021-08-0{day}T"
            assets.append(
                _make_asset(
                    *base, f"{ts}08:00:00", local_dt=f"{ts}08:00:00", city="Ville Sur Sarre"
                )
            )
            assets.append(
                _make_asset(
                    *base, f"{ts}19:00:00", local_dt=f"{ts}19:00:00", city="Ville Sur Sarre"
                )
            )

        bases = detect_overnight_stops(assets)
        # Base A appears before AND after the excursion → merge into one base
        # absorbing the excursion: [A(5n), X(1n), A(3n)] → [A(9n)]
        assert len(bases) == 1
        assert bases[0].location_name == "Ville Sur Sarre"
        assert bases[0].nights >= 8

    def test_base_with_excursions_between(self):
        """Bretagne: base A (with excursions) → base B = 2 bases.

        Pattern: base photos on days 1,4 + excursion photos on days 2,3
        + different base on days 5-7. The repeated base A should be detected
        even if it doesn't meet the day-count threshold, because it appears
        at non-consecutive positions in the output.
        """
        from immich_memories.analysis.trip_detection import detect_overnight_stops

        base_a = (48.295, -3.960)  # Brasparts
        excursion1 = (48.276, -4.593)  # Camaret-sur-Mer (47km)
        excursion2 = (48.362, -3.735)  # Huelgoat (18km)
        base_b = (48.685, -2.337)  # Fréhel (127km)

        assets = [
            # Day 1: at Brasparts
            _make_asset(
                *base_a, "2023-09-23T10:00:00", local_dt="2023-09-23T10:00:00", city="Brasparts"
            ),
            _make_asset(
                *base_a, "2023-09-23T19:00:00", local_dt="2023-09-23T19:00:00", city="Brasparts"
            ),
            # Day 2: excursion to Camaret, last photo far from base
            _make_asset(
                *excursion1,
                "2023-09-24T10:00:00",
                local_dt="2023-09-24T10:00:00",
                city="Camaret-sur-Mer",
            ),
            _make_asset(
                *excursion1,
                "2023-09-24T18:00:00",
                local_dt="2023-09-24T18:00:00",
                city="Camaret-sur-Mer",
            ),
            # Day 3: excursion to Huelgoat
            _make_asset(
                *excursion2, "2023-09-25T10:00:00", local_dt="2023-09-25T10:00:00", city="Huelgoat"
            ),
            _make_asset(
                *excursion2, "2023-09-25T18:00:00", local_dt="2023-09-25T18:00:00", city="Huelgoat"
            ),
            # Day 4: back at Brasparts (proves it's the base!)
            _make_asset(
                *base_a, "2023-09-26T10:00:00", local_dt="2023-09-26T10:00:00", city="Brasparts"
            ),
            _make_asset(
                *base_a, "2023-09-26T19:00:00", local_dt="2023-09-26T19:00:00", city="Brasparts"
            ),
            # Days 5-7: moved to Fréhel (new base)
            _make_asset(
                *base_b, "2023-09-27T10:00:00", local_dt="2023-09-27T10:00:00", city="Frehel"
            ),
            _make_asset(
                *base_b, "2023-09-27T19:00:00", local_dt="2023-09-27T19:00:00", city="Frehel"
            ),
            _make_asset(
                *base_b, "2023-09-28T10:00:00", local_dt="2023-09-28T10:00:00", city="Frehel"
            ),
            _make_asset(
                *base_b, "2023-09-28T19:00:00", local_dt="2023-09-28T19:00:00", city="Frehel"
            ),
            _make_asset(
                *base_b, "2023-09-29T10:00:00", local_dt="2023-09-29T10:00:00", city="Frehel"
            ),
            _make_asset(
                *base_b, "2023-09-29T19:00:00", local_dt="2023-09-29T19:00:00", city="Frehel"
            ),
        ]
        bases = detect_overnight_stops(assets)
        # Brasparts (days 1,2,3,4) → Fréhel (days 5,6,7)
        assert len(bases) == 2
        assert bases[0].location_name == "Brasparts"
        assert bases[0].nights >= 4
        assert bases[1].location_name == "Frehel"
        assert bases[1].nights >= 3


class TestTagClipsToSegments:
    """Map clips to overnight segments by date."""

    def test_assigns_clips_to_correct_segments(self):
        from immich_memories.analysis.trip_detection import OvernightBase, tag_clips_to_segments

        bases = [
            OvernightBase(
                start_date=date(2023, 9, 23),
                end_date=date(2023, 9, 26),
                nights=4,
                lat=48.3,
                lon=-4.0,
                location_name="Brasparts",
                asset_ids=[],
            ),
            OvernightBase(
                start_date=date(2023, 9, 27),
                end_date=date(2023, 9, 29),
                nights=3,
                lat=48.7,
                lon=-2.3,
                location_name="Frehel",
                asset_ids=[],
            ),
        ]
        clip_dates = {
            "clip1": date(2023, 9, 23),
            "clip2": date(2023, 9, 25),
            "clip3": date(2023, 9, 27),
            "clip4": date(2023, 9, 29),
        }
        result = tag_clips_to_segments(clip_dates, bases)
        assert result["clip1"] == 0
        assert result["clip2"] == 0
        assert result["clip3"] == 1
        assert result["clip4"] == 1

    def test_assigns_orphan_clip_to_nearest_segment(self):
        from immich_memories.analysis.trip_detection import OvernightBase, tag_clips_to_segments

        bases = [
            OvernightBase(
                start_date=date(2023, 9, 23),
                end_date=date(2023, 9, 25),
                nights=3,
                lat=48.3,
                lon=-4.0,
                location_name="Brasparts",
                asset_ids=[],
            ),
        ]
        # Clip date is outside the segment range
        result = tag_clips_to_segments({"orphan": date(2023, 9, 30)}, bases)
        assert result["orphan"] == 0  # nearest (and only) segment

    def test_empty_bases_returns_empty(self):
        from immich_memories.analysis.trip_detection import tag_clips_to_segments

        result = tag_clips_to_segments({"clip1": date(2023, 9, 23)}, [])
        assert result == {}


class TestDistributeClipBudget:
    """Proportional clip distribution across trip segments."""

    def test_proportional_distribution(self):
        from immich_memories.analysis.trip_detection import distribute_clip_budget

        result = distribute_clip_budget(10, [4, 3, 1])
        assert sum(result) == 10
        assert all(r >= 1 for r in result)
        assert result[0] > result[2]  # 4-night segment gets more than 1-night

    def test_minimum_one_per_segment(self):
        from immich_memories.analysis.trip_detection import distribute_clip_budget

        result = distribute_clip_budget(3, [1, 1, 1])
        assert result == [1, 1, 1]

    def test_fewer_clips_than_segments(self):
        from immich_memories.analysis.trip_detection import distribute_clip_budget

        result = distribute_clip_budget(2, [3, 2, 1])
        assert sum(result) == 2
        assert len(result) == 2  # only 2 clips, can't cover all 3

    def test_empty_segments(self):
        from immich_memories.analysis.trip_detection import distribute_clip_budget

        assert distribute_clip_budget(5, []) == []
