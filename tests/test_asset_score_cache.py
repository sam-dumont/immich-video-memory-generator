"""Tests for AssetScoreCache — extracted from VideoAnalysisCache."""

from __future__ import annotations

from pathlib import Path

import pytest

from immich_memories.cache.asset_score_cache import AssetScoreCache
from immich_memories.cache.database import VideoAnalysisCache


@pytest.fixture
def cache(tmp_path: Path) -> AssetScoreCache:
    """A cache on the migrated schema, rather than a hand-copy of it.

    The hand-written CREATE TABLE this replaced had drifted from the real one
    and pinned the primary key that #698 is about, so it could not have shown
    the bug.
    """
    db_path = tmp_path / "test.db"
    VideoAnalysisCache(db_path)
    return AssetScoreCache(db_path)


class TestAssetScoreCache:
    def test_save_and_get(self, cache: AssetScoreCache):
        cache.save_asset_score(
            asset_id="abc",
            asset_type="VIDEO",
            metadata_score=0.7,
            combined_score=0.85,
            llm_interest=0.9,
        )
        result = cache.get_asset_score("abc")
        assert result is not None
        assert result["asset_id"] == "abc"
        assert result["combined_score"] == 0.85
        assert result["llm_interest"] == 0.9

    def test_get_missing_returns_none(self, cache: AssetScoreCache):
        assert cache.get_asset_score("nonexistent") is None

    def test_cache_stats(self, cache: AssetScoreCache):
        cache.save_asset_score("v1", "VIDEO", 0.5, 0.6)
        cache.save_asset_score("v2", "VIDEO", 0.7, 0.8, llm_interest=0.9)
        cache.save_asset_score("p1", "IMAGE", 0.3, 0.4)

        stats = cache.get_cache_stats()
        assert stats["total"] == 3
        assert stats["by_type"]["VIDEO"] == 2
        assert stats["by_type"]["IMAGE"] == 1
        assert stats["with_llm"] == 1
        assert stats["oldest"] is not None
        assert stats["newest"] is not None

    def test_stats_separate_the_assets_from_the_looks_banked_about_them(
        self, cache: AssetScoreCache
    ) -> None:
        """One asset under two versions is one asset and two looks."""
        cache.save_asset_score("photo-1", "IMAGE", 0.5, 0.81, model_version="qwen#look1")
        cache.save_asset_score("photo-1", "IMAGE", 0.5, 0.42, model_version="qwen#look2")
        cache.save_asset_score("photo-2", "IMAGE", 0.5, 0.33, model_version="qwen#look2")

        stats = cache.get_cache_stats()

        assert stats["assets"] == 2
        assert stats["total"] == 3

    def test_upsert_overwrites(self, cache: AssetScoreCache):
        cache.save_asset_score("abc", "VIDEO", 0.5, 0.6)
        cache.save_asset_score("abc", "VIDEO", 0.9, 0.95)

        result = cache.get_asset_score("abc")
        assert result is not None
        assert result["combined_score"] == 0.95
