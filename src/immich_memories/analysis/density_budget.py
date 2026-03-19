"""Density-proportional time budget for clip selection.

Replaces the rigid favorites-only prefiltering with an adaptive budget
that distributes raw footage quotas across time buckets proportional to
asset density. Dense months (summer, holidays) get more clips.

All asset types (videos, photos, live photos) count toward density.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class BucketQuota:
    """A time bucket with its asset density and raw footage quota."""

    key: str  # "2025-07" for month, "2025-W28" for week
    start: datetime | None = None
    total_assets: int = 0
    weight: float = 0.0
    quota_seconds: float = 0.0

    # Filled during selection
    favorite_ids: list[str] = field(default_factory=list)
    favorite_duration: float = 0.0
    gap_fill_ids: list[str] = field(default_factory=list)
    gap_fill_duration: float = 0.0

    @property
    def filled_seconds(self) -> float:
        return self.favorite_duration + self.gap_fill_duration

    @property
    def gap_seconds(self) -> float:
        return max(0, self.quota_seconds - self.filled_seconds)

    @property
    def is_filled(self) -> bool:
        return self.filled_seconds >= self.quota_seconds


@dataclass
class AssetEntry:
    """Lightweight asset reference for budget calculation."""

    asset_id: str
    asset_type: str  # "video", "photo", "live_photo"
    date: datetime
    duration: float
    is_favorite: bool
    score: float = 0.0
    width: int = 0
    height: int = 0


def compute_density_budget(
    assets: list[AssetEntry],
    target_duration_seconds: float,
    title_overhead_seconds: float = 50.0,
    raw_multiplier: float = 2.0,
    bucket_mode: str = "auto",
    favorite_buffer: float = 1.5,
) -> list[BucketQuota]:
    """Compute density-proportional time budget across time buckets.

    Args:
        assets: All assets (videos, photos, live photos) with dates.
        target_duration_seconds: Target video length (e.g., 600 for 10min).
        title_overhead_seconds: Time eaten by titles, dividers, transitions.
        raw_multiplier: Raw footage needed per content second (2.0 = need 2× for trimming).
        bucket_mode: "month", "week", or "auto" (month for >6 months, week otherwise).
        favorite_buffer: Analyze up to this × quota of favorites per bucket.
    """
    if not assets:
        return []

    # WHY: for short targets (e.g., 25s test), overhead can exceed budget.
    # Use at most 20% of target for overhead, ensuring positive content budget.
    effective_overhead = min(title_overhead_seconds, target_duration_seconds * 0.2)
    content_budget = target_duration_seconds - effective_overhead
    raw_budget = content_budget * raw_multiplier

    # Determine bucket granularity
    dates = [a.date for a in assets]
    span_days = (max(dates) - min(dates)).days
    if bucket_mode == "auto":
        bucket_mode = "month" if span_days > 180 else "week"

    # Group assets into buckets
    buckets: dict[str, list[AssetEntry]] = defaultdict(list)
    for a in assets:
        key = _bucket_key(a.date, bucket_mode)
        buckets[key].append(a)

    # Estimate month divider overhead
    divider_overhead = len(buckets) * 2.0
    raw_budget -= divider_overhead * raw_multiplier

    total_assets = len(assets)

    # Build quotas and fill each bucket
    result: list[BucketQuota] = []
    for key in sorted(buckets):
        bucket_assets = buckets[key]
        weight = len(bucket_assets) / total_assets
        quota = BucketQuota(
            key=key,
            start=min(a.date for a in bucket_assets),
            total_assets=len(bucket_assets),
            weight=weight,
            quota_seconds=raw_budget * weight,
        )
        _fill_bucket(quota, bucket_assets, favorite_buffer)
        result.append(quota)

    return result


def _fill_bucket(quota: BucketQuota, assets: list[AssetEntry], favorite_buffer: float) -> None:
    """Fill a bucket with favorites first, then gap-fillers by score."""
    favorites = sorted([a for a in assets if a.is_favorite], key=lambda a: a.score, reverse=True)
    max_fav_seconds = quota.quota_seconds * favorite_buffer

    for fav in favorites:
        if quota.favorite_duration >= max_fav_seconds:
            break
        quota.favorite_ids.append(fav.asset_id)
        quota.favorite_duration += fav.duration

    if quota.is_filled:
        return

    non_favs = sorted([a for a in assets if not a.is_favorite], key=lambda a: a.score, reverse=True)
    for nf in non_favs:
        if quota.is_filled:
            break
        quota.gap_fill_ids.append(nf.asset_id)
        quota.gap_fill_duration += nf.duration


def log_budget_summary(buckets: list[BucketQuota], raw_budget: float) -> None:
    """Log a human-readable budget summary."""
    total_fav = sum(len(b.favorite_ids) for b in buckets)
    total_gap = sum(len(b.gap_fill_ids) for b in buckets)
    total_fav_dur = sum(b.favorite_duration for b in buckets)
    total_gap_dur = sum(b.gap_fill_duration for b in buckets)

    logger.info(
        f"Density budget: {len(buckets)} buckets, "
        f"{total_fav} favorites ({total_fav_dur:.0f}s) + "
        f"{total_gap} gap-fillers ({total_gap_dur:.0f}s) = "
        f"{total_fav_dur + total_gap_dur:.0f}s / {raw_budget:.0f}s raw budget"
    )

    unfilled = [b for b in buckets if not b.is_filled]
    if unfilled:
        logger.info(f"  {len(unfilled)} buckets still have gaps: {[b.key for b in unfilled]}")


def _bucket_key(dt: datetime, mode: str) -> str:
    if mode == "week":
        iso = dt.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"
    return dt.strftime("%Y-%m")
