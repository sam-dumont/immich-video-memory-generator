"""Tests for AssetScoreCache — extracted from VideoAnalysisCache."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from immich_memories.cache.asset_score_cache import AssetScoreCache


@pytest.fixture
def cache(tmp_path: Path) -> AssetScoreCache:
    """Create a cache with the asset_scores table pre-created."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS asset_scores (
            asset_id TEXT PRIMARY KEY,
            asset_type TEXT NOT NULL,
            llm_interest REAL,
            llm_quality REAL,
            llm_emotion TEXT,
            llm_description TEXT,
            metadata_score REAL NOT NULL,
            combined_score REAL NOT NULL,
            analyzed_at TEXT NOT NULL DEFAULT (datetime('now')),
            model_version TEXT
        );
    """)
    conn.close()
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

    def test_batch_lookup(self, cache: AssetScoreCache):
        cache.save_asset_score("a1", "VIDEO", 0.5, 0.6)
        cache.save_asset_score("a2", "IMAGE", 0.3, 0.4)

        result = cache.get_asset_scores_batch(["a1", "a2", "a3"])
        assert "a1" in result
        assert "a2" in result
        assert "a3" not in result

    def test_batch_empty_ids(self, cache: AssetScoreCache):
        assert cache.get_asset_scores_batch([]) == {}

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

    def test_upsert_overwrites(self, cache: AssetScoreCache):
        cache.save_asset_score("abc", "VIDEO", 0.5, 0.6)
        cache.save_asset_score("abc", "VIDEO", 0.9, 0.95)

        result = cache.get_asset_score("abc")
        assert result is not None
        assert result["combined_score"] == 0.95
