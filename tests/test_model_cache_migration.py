"""Model-aware semantic cache migration contracts."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from immich_memories.cache import database as cache_database
from immich_memories.cache.asset_score_cache import AssetScoreCache
from immich_memories.cache.database import VideoAnalysisCache


def test_v16_marks_existing_video_analysis_as_unversioned(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "v15.db"
    monkeypatch.setattr(cache_database, "SCHEMA_VERSION", 15)
    VideoAnalysisCache(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO video_analysis (
                asset_id, analysis_timestamp, analysis_version, scoring_version
            ) VALUES (?, datetime('now'), ?, ?)
            """,
            ("existing-video", cache_database.ANALYSIS_VERSION, cache_database.SCORING_VERSION),
        )
        conn.commit()

    monkeypatch.setattr(cache_database, "SCHEMA_VERSION", 16)
    cache = VideoAnalysisCache(db_path)
    cache = VideoAnalysisCache(db_path)

    analysis = cache.get_analysis("existing-video", include_segments=False)
    with sqlite3.connect(db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(video_analysis)")}
        version = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]

    assert "model_version" in columns
    assert version == 16
    assert analysis is not None
    assert analysis.model_version is None


def test_v22_carries_every_banked_row_onto_the_version_key(tmp_path: Path, monkeypatch) -> None:
    """Rows written under the old single-asset key stay readable, versions and all."""
    db_path = tmp_path / "v21.db"
    monkeypatch.setattr(cache_database, "SCHEMA_VERSION", 21)
    VideoAnalysisCache(db_path)
    banked = [
        ("current", 0.81, "qwen#look2"),
        ("stale", 0.62, "qwen#look1"),
        ("unversioned", 0.43, None),
    ]
    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            "INSERT INTO asset_scores (asset_id, asset_type, metadata_score,"
            " combined_score, model_version) VALUES (?, 'photo', 0.5, ?, ?)",
            banked,
        )
        conn.commit()

    monkeypatch.setattr(cache_database, "SCHEMA_VERSION", 22)
    VideoAnalysisCache(db_path)

    cache = AssetScoreCache(db_path)
    # A row that predates versions is addressable under the empty version, not lost.
    served = {
        asset_id: cache.get_asset_scores_batch([asset_id], model_version=version or "")
        for asset_id, _score, version in banked
    }

    assert [served[asset_id][asset_id]["combined_score"] for asset_id, _s, _v in banked] == [
        0.81,
        0.62,
        0.43,
    ]
