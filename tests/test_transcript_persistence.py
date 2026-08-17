"""Transcripts survive a round trip through the analysis cache."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from immich_memories.cache import database as cache_database
from immich_memories.cache.database import VideoAnalysisCache
from immich_memories.cache.database_models import CachedSegment


@pytest.fixture
def mock_asset():
    """# WHY: replaces the Immich Asset model, an API boundary; save_analysis only
    reads five attributes off it."""
    asset = MagicMock()
    asset.id = "test-asset-123"
    asset.checksum = "abc123"
    asset.file_modified_at = datetime(2024, 1, 15, 12, 0, 0)
    asset.file_created_at = datetime(2024, 1, 15, 10, 0, 0)
    asset.duration_seconds = 30.0
    return asset


def test_v17_adds_transcript_columns_to_an_existing_database(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "v16.db"
    monkeypatch.setattr(cache_database, "SCHEMA_VERSION", 16)
    VideoAnalysisCache(db_path)

    monkeypatch.setattr(cache_database, "SCHEMA_VERSION", 17)
    VideoAnalysisCache(db_path)

    with sqlite3.connect(db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(video_segments)")}
        version = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]

    assert {"transcript", "transcript_language", "transcript_confidence"} <= columns
    assert version == 17


def test_transcript_round_trips_through_the_cache(tmp_path: Path, mock_asset):
    cache = VideoAnalysisCache(tmp_path / "cache.db")
    # All five score fields are set because _compute_best_scores takes max() over
    # them with no None guard.
    segment = CachedSegment(
        segment_index=0,
        start_time=1.0,
        end_time=4.0,
        face_score=0.8,
        motion_score=0.6,
        stability_score=0.7,
        audio_score=0.5,
        total_score=0.7,
        transcript="on va a la plage",
        transcript_language="fr",
        transcript_confidence=0.82,
    )

    cache.save_analysis(asset=mock_asset, segments=[segment])
    loaded = cache.get_analysis(mock_asset.id, include_segments=True)

    assert loaded is not None
    stored = loaded.segments[0]
    assert stored.transcript == "on va a la plage"
    assert stored.transcript_language == "fr"
    assert stored.transcript_confidence == 0.82


def test_a_segment_without_a_transcript_stores_nulls(tmp_path: Path, mock_asset):
    """Declining to transcribe is the common case and must not write a placeholder."""
    cache = VideoAnalysisCache(tmp_path / "cache.db")
    segment = CachedSegment(
        segment_index=0,
        start_time=0.0,
        end_time=3.0,
        face_score=0.4,
        motion_score=0.4,
        stability_score=0.4,
        audio_score=0.4,
        total_score=0.5,
    )

    cache.save_analysis(asset=mock_asset, segments=[segment])
    loaded = cache.get_analysis(mock_asset.id, include_segments=True)

    assert loaded is not None
    assert loaded.segments[0].transcript is None
    assert loaded.segments[0].transcript_confidence is None


def test_scoring_version_is_not_bumped_by_transcripts():
    """No scoring path reads a transcript, so every cached score stays valid.

    Bumping would invalidate every scored segment in the library to record a
    change that cannot move a number.
    """
    assert cache_database.SCORING_VERSION == 2
