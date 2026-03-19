"""Tests for SQLite busy_timeout on all database connections.

WHY: Without busy_timeout, concurrent access (scheduler + UI + CLI) raises
'database is locked' immediately instead of retrying for up to 5 seconds.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def temp_db(tmp_path):
    return tmp_path / "test.db"


class TestSqliteBusyTimeout:
    """All database connections must set busy_timeout = 5000ms."""

    def test_video_analysis_cache_sets_busy_timeout(self, temp_db):
        from immich_memories.cache.database import VideoAnalysisCache

        cache = VideoAnalysisCache(temp_db)
        with cache._get_connection() as conn:
            result = conn.execute("PRAGMA busy_timeout").fetchone()
            assert result[0] == 5000

    def test_asset_score_cache_sets_busy_timeout(self, temp_db):
        from immich_memories.cache.asset_score_cache import AssetScoreCache

        cache = AssetScoreCache(temp_db)
        with cache._get_connection() as conn:
            result = conn.execute("PRAGMA busy_timeout").fetchone()
            assert result[0] == 5000

    def test_run_database_sets_busy_timeout(self, temp_db):
        from immich_memories.tracking.run_database import RunDatabase

        # WHY: RunDatabase triggers migrations via VideoAnalysisCache — needs a real path
        db = RunDatabase(temp_db)
        with db._get_connection() as conn:
            result = conn.execute("PRAGMA busy_timeout").fetchone()
            assert result[0] == 5000
