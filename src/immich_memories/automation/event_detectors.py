"""Event-driven candidate detectors: TripDetector, ActivityBurstDetector, MultiPersonDetector."""

from __future__ import annotations

import itertools
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
        """Emit monthly_highlights candidates for burst months in the last 12 months."""
        threshold = burst_threshold or self._DEFAULT_BURST_THRESHOLD
        if len(assets_by_month) < 2:
            return []

        # WHY: only detect bursts in last 12 months — older content is for manual exploration
        cutoff = date(today.year - 1, today.month, 1)
        cutoff_key = f"{cutoff.year}-{cutoff.month:02d}"

        sorted_months = sorted(assets_by_month.keys())
        candidates: list[MemoryCandidate] = []

        for i, month_key in enumerate(sorted_months):
            if month_key < cutoff_key:
                continue

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


class MultiPersonDetector:
    """Detect pairs of people who appear together frequently."""

    BASE_SCORE = 0.55
    MIN_SHARED_ASSETS = 50
    TOP_PAIRS = 3

    def detect(
        self,
        assets_by_month: dict[str, int],
        people: list[Any],
        generated_keys: set[str],
        config: Config,
        today: date,
        person_asset_counts: dict[str, int] | None = None,
    ) -> list[MemoryCandidate]:
        """Propose multi_person memories for pairs who frequently appear together."""
        counts = person_asset_counts or {}
        if not counts:
            return []

        # Top 10 named people with thumbnails, sorted by asset count
        visible = [p for p in people if p.name and getattr(p, "thumbnail_path", None)]
        visible.sort(key=lambda p: counts.get(p.id, 0), reverse=True)
        top = visible[:10]

        if len(top) < 2:
            return []

        year = today.year - 1
        start = date(year, 1, 1)
        end = date(year, 12, 31)

        scored_pairs: list[tuple[float, Any, Any, int]] = []
        for person_a, person_b in itertools.combinations(top, 2):
            count_a = counts.get(person_a.id, 0)
            count_b = counts.get(person_b.id, 0)
            estimated_shared = int(min(count_a, count_b) * 0.3)

            if estimated_shared < self.MIN_SHARED_ASSETS:
                continue

            names_sorted = sorted([person_a.name.lower(), person_b.name.lower()])
            mem_key = make_memory_key("multi_person", start, end, names_sorted)

            if mem_key in generated_keys:
                continue

            pair_score = self.BASE_SCORE * min(1.0, estimated_shared / 500)
            scored_pairs.append((pair_score, person_a, person_b, estimated_shared))

        # Sort by score descending, take top pairs
        scored_pairs.sort(key=lambda x: x[0], reverse=True)

        candidates: list[MemoryCandidate] = []
        for pair_score, person_a, person_b, estimated_shared in scored_pairs[: self.TOP_PAIRS]:
            names_sorted = sorted([person_a.name.lower(), person_b.name.lower()])
            mem_key = make_memory_key("multi_person", start, end, names_sorted)

            # Display names in original case
            display_names = sorted([person_a.name, person_b.name])
            reason = f"{display_names[0]} & {display_names[1]} together, ~{estimated_shared} shared moments"

            candidates.append(
                MemoryCandidate(
                    memory_type="multi_person",
                    date_range_start=start,
                    date_range_end=end,
                    person_names=display_names,
                    memory_key=mem_key,
                    score=round(pair_score, 3),
                    reason=reason,
                    asset_count=estimated_shared,
                ),
            )

        return candidates
