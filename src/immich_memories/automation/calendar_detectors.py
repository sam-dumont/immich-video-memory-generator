"""Calendar-driven candidate detectors for smart automation.

Three detectors that propose memories based on calendar patterns:
monthly highlights, yearly reviews, and person spotlights.
"""

from __future__ import annotations

import calendar
from datetime import date

from immich_memories.automation.candidates import MemoryCandidate, make_memory_key
from immich_memories.config_loader import Config


class MonthlyDetector:
    """Proposes monthly highlight memories for recent un-generated months."""

    LOOKBACK_MONTHS = 6
    BASE_SCORE = 0.7

    def detect(
        self,
        assets_by_month: dict[str, int],
        people: list,
        generated_keys: set[str],
        config: Config,
        today: date,
    ) -> list[MemoryCandidate]:
        months = _last_n_completed_months(today, self.LOOKBACK_MONTHS)
        candidates = []

        for i, (year, month) in enumerate(months):
            key_str = f"{year}-{month:02d}"
            count = assets_by_month.get(key_str, 0)
            if count == 0:
                continue

            last_day = calendar.monthrange(year, month)[1]
            start = date(year, month, 1)
            end = date(year, month, last_day)
            mem_key = make_memory_key("monthly_highlights", start, end)

            if mem_key in generated_keys:
                continue

            # Most recent completed month gets full score, older ones decay
            recency = 1.0 - (i * 0.1)
            score = self.BASE_SCORE * max(recency, 0.3)

            reason = (
                f"{count} assets, most recent month"
                if i == 0
                else f"{count} assets, never generated"
            )

            candidates.append(
                MemoryCandidate(
                    memory_type="monthly_highlights",
                    date_range_start=start,
                    date_range_end=end,
                    person_names=[],
                    memory_key=mem_key,
                    score=round(score, 3),
                    reason=reason,
                    asset_count=count,
                )
            )

        return candidates


class YearlyDetector:
    """Proposes year-in-review memories for past years with content."""

    BASE_SCORE = 0.8
    # Wait until mid-January for late imports
    EARLIEST_DAY = 15

    def detect(
        self,
        assets_by_month: dict[str, int],
        people: list,
        generated_keys: set[str],
        config: Config,
        today: date,
    ) -> list[MemoryCandidate]:
        years_with_content = _years_from_assets(assets_by_month)
        candidates = []

        for year in sorted(years_with_content, reverse=True):
            # Only propose after Jan 15 of the following year
            cutoff = date(year + 1, 1, self.EARLIEST_DAY)
            if today < cutoff:
                continue

            start = date(year, 1, 1)
            end = date(year, 12, 31)
            mem_key = make_memory_key("year_in_review", start, end)

            if mem_key in generated_keys:
                continue

            total = sum(
                count
                for month_key, count in assets_by_month.items()
                if month_key.startswith(f"{year}-")
            )

            # More recent years score higher
            years_ago = today.year - year
            recency = 1.0 - (years_ago * 0.1)
            score = self.BASE_SCORE * max(recency, 0.3)

            candidates.append(
                MemoryCandidate(
                    memory_type="year_in_review",
                    date_range_start=start,
                    date_range_end=end,
                    person_names=[],
                    memory_key=mem_key,
                    score=round(score, 3),
                    reason=f"{total} assets across the year, never generated",
                    asset_count=total,
                )
            )

        return candidates


class PersonSpotlightDetector:
    """Proposes person spotlight memories for top people in the most recent full year."""

    BASE_SCORE = 0.6
    TOP_N = 5

    def detect(
        self,
        assets_by_month: dict[str, int],
        people: list,
        generated_keys: set[str],
        config: Config,
        today: date,
        person_asset_counts: dict[str, int] | None = None,
    ) -> list[MemoryCandidate]:
        if not people:
            return []

        target_year = today.year - 1
        start = date(target_year, 1, 1)
        end = date(target_year, 12, 31)

        counts = person_asset_counts or {}

        # Filter to named people with thumbnails (proxy for "has content")
        visible = [p for p in people if p.name and p.thumbnail_path]
        if not visible:
            return []

        # Sort by asset count if available, otherwise keep Immich default order
        if counts:
            visible.sort(key=lambda p: counts.get(p.id, 0), reverse=True)

        top = visible[: self.TOP_N]
        max_count = max((counts.get(p.id, 1) for p in top), default=1)

        candidates = []
        for rank, person in enumerate(top):
            name_lower = person.name.lower()
            mem_key = make_memory_key("person_spotlight", start, end, [name_lower])

            if mem_key in generated_keys:
                continue

            asset_count = counts.get(person.id, 0)
            appearance_ratio = (
                asset_count / max_count if max_count > 0 else (len(top) - rank) / len(top)
            )
            score = self.BASE_SCORE * max(0.2, appearance_ratio)

            ordinal = _ordinal(rank + 1)
            count_str = f", {asset_count} assets" if asset_count else ""
            reason = f"{ordinal} most featured person{count_str}"

            candidates.append(
                MemoryCandidate(
                    memory_type="person_spotlight",
                    date_range_start=start,
                    date_range_end=end,
                    person_names=[person.name],
                    memory_key=mem_key,
                    score=round(score, 3),
                    reason=reason,
                    asset_count=asset_count,
                )
            )

        return candidates


class OnThisDayDetector:
    """Proposes 'On This Day' memories for dates with significant content across years."""

    BASE_SCORE = 0.5
    MIN_YEARS = 2

    def detect(
        self,
        assets_by_month: dict[str, int],
        people: list,
        generated_keys: set[str],
        config: Config,
        today: date,
    ) -> list[MemoryCandidate]:
        """Emit candidate if today's date has content in multiple prior years."""
        target_month_key = f"-{today.month:02d}"
        years_with_content = [
            int(k.split("-")[0])
            for k in assets_by_month
            if k.endswith(target_month_key) and int(k.split("-")[0]) < today.year
        ]

        if len(years_with_content) < self.MIN_YEARS:
            return []

        mem_key = make_memory_key(
            "on_this_day",
            date(today.year, today.month, today.day),
            date(today.year, today.month, today.day),
        )

        if mem_key in generated_keys:
            return []

        total_assets = sum(
            assets_by_month.get(f"{y}-{today.month:02d}", 0) for y in years_with_content
        )

        return [
            MemoryCandidate(
                memory_type="on_this_day",
                date_range_start=date(today.year, today.month, today.day),
                date_range_end=date(today.year, today.month, today.day),
                person_names=[],
                memory_key=mem_key,
                score=round(self.BASE_SCORE * min(1.0, len(years_with_content) / 5), 3),
                reason=f"Content on this date across {len(years_with_content)} years",
                asset_count=total_assets,
            )
        ]


def _last_n_completed_months(today: date, n: int) -> list[tuple[int, int]]:
    """Return the last N completed (year, month) pairs before today's month."""
    result = []
    year, month = today.year, today.month
    for _ in range(n):
        # Step back one month
        month -= 1
        if month == 0:
            month = 12
            year -= 1
        result.append((year, month))
    return result


def _years_from_assets(assets_by_month: dict[str, int]) -> set[int]:
    """Extract unique years from YYYY-MM keyed asset counts."""
    years = set()
    for key in assets_by_month:
        try:
            years.add(int(key.split("-")[0]))
        except (ValueError, IndexError):
            continue
    return years


def _ordinal(n: int) -> str:
    # WHY: custom instead of inflect — single use, not worth a dependency
    suffix = "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"
