"""Tests for TripDetector and ActivityBurstDetector."""

from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import patch

from immich_memories.analysis.trip_detection import DetectedTrip
from immich_memories.api.models import Asset, AssetType, ExifInfo
from immich_memories.automation.event_detectors import (
    ActivityBurstDetector,
    TripDetector,
)
from immich_memories.config_loader import Config


def _make_gps_asset(
    asset_id: str,
    lat: float,
    lon: float,
    created: datetime,
) -> Asset:
    return Asset(
        id=asset_id,
        type=AssetType.VIDEO,
        fileCreatedAt=created,
        fileModifiedAt=created,
        updatedAt=created,
        exifInfo=ExifInfo(latitude=lat, longitude=lon),
    )


class TestTripDetector:
    def test_produces_candidates_for_trips(self):
        detector = TripDetector()
        config = Config(trips={"homebase_latitude": 48.8, "homebase_longitude": 2.3})
        today = date(2026, 3, 1)

        trip = DetectedTrip(
            start_date=date(2026, 1, 5),
            end_date=date(2026, 1, 15),
            location_name="Barcelona, Spain",
            asset_count=80,
            centroid_lat=41.4,
            centroid_lon=2.2,
            asset_ids=[f"a{i}" for i in range(80)],
        )

        assets = [
            _make_gps_asset("a0", 41.4, 2.2, datetime(2026, 1, 5, tzinfo=UTC)),
        ]

        # WHY: mock detect_trips to avoid needing real GPS clustering
        with patch(
            "immich_memories.analysis.trip_detection.detect_trips",
            return_value=[trip],
        ):
            candidates = detector.detect(
                assets_by_month={},
                people=[],
                generated_keys=set(),
                config=config,
                today=today,
                assets=assets,
            )

        assert len(candidates) == 1
        c = candidates[0]
        assert c.memory_type == "trip"
        assert c.date_range_start == date(2026, 1, 5)
        assert c.date_range_end == date(2026, 1, 15)
        assert "Barcelona" in c.reason
        assert c.asset_count == 80
        assert c.score > 0

    def test_skips_recent_trips(self):
        """Trips that ended less than 7 days ago are not emitted."""
        detector = TripDetector()
        config = Config(trips={"homebase_latitude": 48.8, "homebase_longitude": 2.3})
        today = date(2026, 1, 20)

        trip = DetectedTrip(
            start_date=date(2026, 1, 10),
            end_date=date(2026, 1, 16),  # only 4 days ago
            location_name="Nice",
            asset_count=30,
            centroid_lat=43.7,
            centroid_lon=7.3,
        )

        with patch(
            "immich_memories.analysis.trip_detection.detect_trips",
            return_value=[trip],
        ):
            candidates = detector.detect(
                assets_by_month={},
                people=[],
                generated_keys=set(),
                config=config,
                today=today,
                assets=[_make_gps_asset("a0", 43.7, 7.3, datetime(2026, 1, 10, tzinfo=UTC))],
            )

        assert len(candidates) == 0

    def test_skips_already_generated(self):
        detector = TripDetector()
        config = Config(trips={"homebase_latitude": 48.8, "homebase_longitude": 2.3})
        today = date(2026, 3, 1)

        trip = DetectedTrip(
            start_date=date(2026, 1, 5),
            end_date=date(2026, 1, 15),
            location_name="Rome",
            asset_count=50,
            centroid_lat=41.9,
            centroid_lon=12.5,
        )

        already = {"trip:2026-01-05:2026-01-15:"}

        with patch(
            "immich_memories.analysis.trip_detection.detect_trips",
            return_value=[trip],
        ):
            candidates = detector.detect(
                assets_by_month={},
                people=[],
                generated_keys=already,
                config=config,
                today=today,
                assets=[_make_gps_asset("a0", 41.9, 12.5, datetime(2026, 1, 5, tzinfo=UTC))],
            )

        assert len(candidates) == 0

    def test_no_homebase_returns_empty(self):
        detector = TripDetector()
        config = Config()  # default 0.0, 0.0

        candidates = detector.detect(
            assets_by_month={},
            people=[],
            generated_keys=set(),
            config=config,
            today=date(2026, 3, 1),
            assets=[_make_gps_asset("a0", 41.9, 12.5, datetime(2026, 1, 5, tzinfo=UTC))],
        )

        assert candidates == []

    def test_no_assets_returns_empty(self):
        detector = TripDetector()
        config = Config(trips={"homebase_latitude": 48.8, "homebase_longitude": 2.3})

        candidates = detector.detect(
            assets_by_month={},
            people=[],
            generated_keys=set(),
            config=config,
            today=date(2026, 3, 1),
            assets=None,
        )

        assert candidates == []

    def test_score_formula(self):
        """Score = 0.75 * min(1, days/14) * min(1, assets/200)."""
        detector = TripDetector()
        config = Config(trips={"homebase_latitude": 48.8, "homebase_longitude": 2.3})
        today = date(2026, 6, 1)

        # 7-day trip (7/14 = 0.5), 100 assets (100/200 = 0.5)
        trip = DetectedTrip(
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 7),
            location_name="Alps",
            asset_count=100,
            centroid_lat=46.0,
            centroid_lon=7.0,
        )

        with patch(
            "immich_memories.analysis.trip_detection.detect_trips",
            return_value=[trip],
        ):
            candidates = detector.detect(
                assets_by_month={},
                people=[],
                generated_keys=set(),
                config=config,
                today=today,
                assets=[_make_gps_asset("a0", 46.0, 7.0, datetime(2026, 1, 1, tzinfo=UTC))],
            )

        assert len(candidates) == 1
        expected = 0.75 * (7 / 14) * (100 / 200)
        assert abs(candidates[0].score - expected) < 1e-9


class TestActivityBurstDetector:
    def test_detects_burst_months(self):
        detector = ActivityBurstDetector()
        # 12 months of ~100, then a burst month of 500
        assets_by_month = {f"2025-{m:02d}": 100 for m in range(1, 13)}
        assets_by_month["2026-01"] = 500  # 5x average

        candidates = detector.detect(
            assets_by_month=assets_by_month,
            people=[],
            generated_keys=set(),
            config=Config(),
            today=date(2026, 3, 1),
        )

        assert len(candidates) == 1
        c = candidates[0]
        assert c.memory_type == "monthly_highlights"
        assert c.date_range_start == date(2026, 1, 1)
        assert c.date_range_end == date(2026, 1, 31)
        assert c.asset_count == 500
        assert "5.0x" in c.reason
        assert c.score > 0

    def test_uniform_activity_no_bursts(self):
        detector = ActivityBurstDetector()
        assets_by_month = {f"2025-{m:02d}": 100 for m in range(1, 13)}

        candidates = detector.detect(
            assets_by_month=assets_by_month,
            people=[],
            generated_keys=set(),
            config=Config(),
            today=date(2026, 1, 1),
        )

        assert candidates == []

    def test_custom_threshold(self):
        """Lower threshold detects more bursts."""
        detector = ActivityBurstDetector()
        assets_by_month = {f"2025-{m:02d}": 100 for m in range(1, 7)}
        assets_by_month["2025-07"] = 160  # 1.6x average — above 1.5 but below 2.0

        no_burst = detector.detect(
            assets_by_month=assets_by_month,
            people=[],
            generated_keys=set(),
            config=Config(),
            today=date(2025, 12, 1),
            burst_threshold=2.0,
        )
        assert no_burst == []

        has_burst = detector.detect(
            assets_by_month=assets_by_month,
            people=[],
            generated_keys=set(),
            config=Config(),
            today=date(2025, 12, 1),
            burst_threshold=1.5,
        )
        assert len(has_burst) == 1

    def test_skips_already_generated(self):
        detector = ActivityBurstDetector()
        assets_by_month = {f"2025-{m:02d}": 100 for m in range(1, 13)}
        assets_by_month["2026-01"] = 500

        already = {"monthly_highlights:2026-01-01:2026-01-31:"}

        candidates = detector.detect(
            assets_by_month=assets_by_month,
            people=[],
            generated_keys=already,
            config=Config(),
            today=date(2026, 3, 1),
        )

        assert candidates == []

    def test_score_formula(self):
        """Score = 0.7 * min(1.0, ratio / threshold)."""
        detector = ActivityBurstDetector()
        assets_by_month = {f"2025-{m:02d}": 100 for m in range(1, 13)}
        assets_by_month["2026-01"] = 300  # 3x average

        candidates = detector.detect(
            assets_by_month=assets_by_month,
            people=[],
            generated_keys=set(),
            config=Config(),
            today=date(2026, 3, 1),
        )

        assert len(candidates) == 1
        # ratio=3.0, threshold=2.0 -> 0.7 * min(1.0, 3.0/2.0) = 0.7 * 1.0 = 0.7
        assert abs(candidates[0].score - 0.7) < 1e-9

    def test_too_few_months(self):
        """With only 1 month, no rolling average is possible."""
        detector = ActivityBurstDetector()

        candidates = detector.detect(
            assets_by_month={"2025-01": 500},
            people=[],
            generated_keys=set(),
            config=Config(),
            today=date(2025, 6, 1),
        )

        assert candidates == []
