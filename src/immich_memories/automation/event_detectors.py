"""Event-driven candidate detectors: TripDetector and ActivityBurstDetector."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from immich_memories.api.models import Asset
from immich_memories.automation.candidates import MemoryCandidate, make_memory_key
from immich_memories.config_loader import Config

logger = logging.getLogger(__name__)


class TripDetector:
    """Detect trip-based memory candidates from GPS-tagged assets."""

    # WHY: 7-day buffer avoids generating a memory for a trip still in progress
    _MIN_DAYS_SINCE_END = 7

    def detect(
        self,
        assets_by_month: dict[str, int],
        people: list[Any],
        generated_keys: set[str],
        config: Config,
        today: date,
        assets: list[Asset] | None = None,
    ) -> list[MemoryCandidate]:
        """Emit candidates for detected trips that haven't been generated yet."""
        if assets is None:
            return []

        trips_cfg = config.trips
        if trips_cfg.homebase_latitude == trips_cfg.homebase_longitude == 0.0:
            return []

        # Lazy import to avoid circular deps and keep module lightweight
        from immich_memories.analysis.trip_detection import detect_trips

        trips = detect_trips(
            assets=assets,
            home_lat=trips_cfg.homebase_latitude,
            home_lon=trips_cfg.homebase_longitude,
            min_distance_km=trips_cfg.min_distance_km,
            min_duration_days=trips_cfg.min_duration_days,
            max_gap_days=trips_cfg.max_gap_days,
        )

        candidates: list[MemoryCandidate] = []
        for trip in trips:
            days_since = (today - trip.end_date).days
            if days_since < self._MIN_DAYS_SINCE_END:
                continue

            key = make_memory_key("trip", trip.start_date, trip.end_date)
            if key in generated_keys:
                continue

            trip_days = (trip.end_date - trip.start_date).days + 1
            score = 0.75 * min(1.0, trip_days / 14) * min(1.0, trip.asset_count / 200)
            reason = f"{trip_days}-day trip to {trip.location_name}, {trip.asset_count} assets"

            candidates.append(
                MemoryCandidate(
                    memory_type="trip",
                    date_range_start=trip.start_date,
                    date_range_end=trip.end_date,
                    person_names=[],
                    memory_key=key,
                    score=score,
                    reason=reason,
                    asset_count=trip.asset_count,
                    extra_params={"location_name": trip.location_name},
                ),
            )

        return candidates


class ActivityBurstDetector:
    """Detect months with unusually high activity compared to rolling average."""

    _DEFAULT_BURST_THRESHOLD = 2.0

    def detect(
        self,
        assets_by_month: dict[str, int],
        people: list[Any],
        generated_keys: set[str],
        config: Config,
        today: date,
        burst_threshold: float | None = None,
    ) -> list[MemoryCandidate]:
        """Emit monthly_highlights candidates for burst months."""
        threshold = burst_threshold or self._DEFAULT_BURST_THRESHOLD
        if len(assets_by_month) < 2:
            return []

        sorted_months = sorted(assets_by_month.keys())
        candidates: list[MemoryCandidate] = []

        for i, month_key in enumerate(sorted_months):
            # Rolling average over the 12 months preceding this one
            window_start = max(0, i - 12)
            window = sorted_months[window_start:i]
            if not window:
                continue

            avg = sum(assets_by_month[m] for m in window) / len(window)
            if avg == 0:
                continue

            count = assets_by_month[month_key]
            ratio = count / avg
            if ratio <= threshold:
                continue

            year, month = int(month_key[:4]), int(month_key[5:7])
            start = date(year, month, 1)
            if month == 12:
                end = date(year, 12, 31)
            else:
                end = date(year, month + 1, 1) - timedelta(days=1)

            key = make_memory_key("monthly_highlights", start, end)
            if key in generated_keys:
                continue

            score = 0.7 * min(1.0, ratio / threshold)
            reason = f"{ratio:.1f}x average activity ({count} assets vs {avg:.0f} avg)"

            candidates.append(
                MemoryCandidate(
                    memory_type="monthly_highlights",
                    date_range_start=start,
                    date_range_end=end,
                    person_names=[],
                    memory_key=key,
                    score=score,
                    reason=reason,
                    asset_count=count,
                ),
            )

        return candidates
