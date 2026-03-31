"""Unit tests for density budget — gap-filler ordering and no-favorites fallback."""

from __future__ import annotations

from datetime import UTC, datetime

from immich_memories.analysis.density_budget import (
    AssetEntry,
    BucketQuota,
    _fill_bucket,
    compute_density_budget,
)


def _make_entry(
    asset_id: str,
    date: datetime,
    duration: float = 5.0,
    is_favorite: bool = False,
    score: float = 0.0,
    width: int = 1920,
    height: int = 1080,
) -> AssetEntry:
    return AssetEntry(
        asset_id=asset_id,
        asset_type="video",
        date=date,
        duration=duration,
        is_favorite=is_favorite,
        score=score,
        width=width,
        height=height,
    )


class TestGapFillerOrdering:
    """Gap-fillers must be selected by score, not randomly."""

    def test_highest_score_gap_filler_selected_first(self):
        """With tight budget, highest-scored non-favorite fills the bucket."""
        quota = BucketQuota(key="2021-W29", quota_seconds=6.0)
        assets = [
            _make_entry("low", datetime(2021, 7, 22, tzinfo=UTC), score=0.2),
            _make_entry("high", datetime(2021, 7, 23, tzinfo=UTC), score=0.8),
            _make_entry("mid", datetime(2021, 7, 24, tzinfo=UTC), score=0.5),
        ]

        _fill_bucket(quota, assets, favorite_buffer=1.5)

        # "high" should be first gap-filler (highest score)
        assert quota.gap_fill_ids[0] == "high"

    def test_all_zero_scores_still_selects(self):
        """When all scores are 0.0, selection still works (doesn't crash)."""
        quota = BucketQuota(key="2021-W29", quota_seconds=15.0)
        assets = [
            _make_entry(f"clip_{i}", datetime(2021, 7, 22 + i, tzinfo=UTC), score=0.0)
            for i in range(3)
        ]

        _fill_bucket(quota, assets, favorite_buffer=1.5)

        assert len(quota.gap_fill_ids) == 3

    def test_favorites_selected_before_gap_fillers(self):
        """Favorites fill bucket before gap-fillers regardless of score."""
        quota = BucketQuota(key="2021-W29", quota_seconds=10.0)
        assets = [
            _make_entry("fav", datetime(2021, 7, 22, tzinfo=UTC), score=0.3, is_favorite=True),
            _make_entry("nonfav", datetime(2021, 7, 23, tzinfo=UTC), score=0.9),
        ]

        _fill_bucket(quota, assets, favorite_buffer=1.5)

        assert "fav" in quota.favorite_ids
        assert "nonfav" in quota.gap_fill_ids


class TestDensityBudgetNoFavorites:
    """Density budget must produce deterministic results with zero favorites."""

    def test_no_favorites_selects_by_quality_score(self):
        """All non-favorites: highest quality_score clips fill budget first."""
        entries = [
            _make_entry("low_q", datetime(2021, 7, 22, tzinfo=UTC), score=0.15),
            _make_entry("mid_q", datetime(2021, 7, 23, tzinfo=UTC), score=0.40),
            _make_entry("hi_q", datetime(2021, 7, 24, tzinfo=UTC), score=0.65),
        ]

        buckets = compute_density_budget(
            assets=entries,
            target_duration_seconds=10.0,
            raw_multiplier=1.0,
        )

        all_gap_ids = []
        for b in buckets:
            all_gap_ids.extend(b.gap_fill_ids)

        assert "hi_q" in all_gap_ids
        # If budget is tight, "hi_q" should come before "low_q"
        if "low_q" in all_gap_ids:
            assert all_gap_ids.index("hi_q") < all_gap_ids.index("low_q")
