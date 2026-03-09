"""Database migration methods for video analysis cache."""

from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger(__name__)

# Current schema version - increment when schema changes
SCHEMA_VERSION = 5


class DatabaseMigrationsMixin:
    """Mixin providing database migration methods.

    Requires the consuming class to provide:
    - self._get_connection() -> contextmanager yielding sqlite3.Connection
    """

    def _run_migrations(self) -> None:
        """Run database migrations."""
        with self._get_connection() as conn:
            # Check current version
            conn.execute("""
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL DEFAULT (datetime('now')),
                    description TEXT
                )
            """)

            result = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
            current_version = result[0] or 0

            if current_version < SCHEMA_VERSION:
                self._apply_migrations(conn, current_version)

    def _apply_migrations(self, conn: sqlite3.Connection, from_version: int) -> None:
        """Apply migrations from current version to latest."""
        migrations = {
            1: self._migration_v1_initial,
            2: self._migration_v2_thumbnails,
            3: self._migration_v3_remove_thumbnail_blobs,
            4: self._migration_v4_run_tracking,
            5: self._migration_v5_add_rotation,
        }

        for version in range(from_version + 1, SCHEMA_VERSION + 1):
            if version in migrations:
                logger.info(f"Applying migration v{version}")
                migrations[version](conn)
                conn.execute(
                    "INSERT INTO schema_migrations (version, description) VALUES (?, ?)",
                    (version, f"Migration to v{version}"),
                )
                conn.commit()

    def _migration_v1_initial(self, conn: sqlite3.Connection) -> None:
        """Initial schema creation."""
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS video_analysis (
                asset_id TEXT PRIMARY KEY,
                checksum TEXT,
                file_modified_at TEXT,
                analysis_timestamp TEXT NOT NULL,
                analysis_version INTEGER NOT NULL DEFAULT 1,
                perceptual_hash TEXT,
                thumbnail_hash TEXT,
                duration_seconds REAL,
                width INTEGER,
                height INTEGER,
                bitrate INTEGER,
                fps REAL,
                codec TEXT,
                color_space TEXT,
                color_transfer TEXT,
                color_primaries TEXT,
                bit_depth INTEGER,
                best_face_score REAL,
                best_motion_score REAL,
                best_stability_score REAL,
                best_audio_score REAL,
                best_total_score REAL,
                motion_summary TEXT,
                audio_levels TEXT,
                file_created_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_video_analysis_hash
                ON video_analysis(perceptual_hash);
            CREATE INDEX IF NOT EXISTS idx_video_analysis_created
                ON video_analysis(file_created_at);
            CREATE INDEX IF NOT EXISTS idx_video_analysis_checksum
                ON video_analysis(checksum);

            CREATE TABLE IF NOT EXISTS video_segments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                asset_id TEXT NOT NULL,
                segment_index INTEGER NOT NULL,
                start_time REAL NOT NULL,
                end_time REAL NOT NULL,
                start_frame INTEGER,
                end_frame INTEGER,
                face_score REAL,
                motion_score REAL,
                stability_score REAL,
                audio_score REAL,
                total_score REAL,
                face_positions TEXT,
                motion_vectors TEXT,
                keyframe_path TEXT,
                FOREIGN KEY (asset_id) REFERENCES video_analysis(asset_id)
                    ON DELETE CASCADE,
                UNIQUE(asset_id, segment_index)
            );

            CREATE INDEX IF NOT EXISTS idx_segments_asset
                ON video_segments(asset_id);
            CREATE INDEX IF NOT EXISTS idx_segments_score
                ON video_segments(total_score DESC);

            CREATE TABLE IF NOT EXISTS hash_index (
                asset_id TEXT PRIMARY KEY,
                hash_chunk_0 TEXT,
                hash_chunk_1 TEXT,
                hash_chunk_2 TEXT,
                hash_chunk_3 TEXT,
                full_hash TEXT NOT NULL,
                FOREIGN KEY (asset_id) REFERENCES video_analysis(asset_id)
                    ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_hash_chunk_0
                ON hash_index(hash_chunk_0);
            CREATE INDEX IF NOT EXISTS idx_hash_chunk_1
                ON hash_index(hash_chunk_1);
            CREATE INDEX IF NOT EXISTS idx_hash_chunk_2
                ON hash_index(hash_chunk_2);
            CREATE INDEX IF NOT EXISTS idx_hash_chunk_3
                ON hash_index(hash_chunk_3);
        """)

    def _migration_v2_thumbnails(self, conn: sqlite3.Connection) -> None:
        """Add thumbnails table for caching thumbnails in DB."""
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS thumbnails (
                asset_id TEXT NOT NULL,
                size TEXT NOT NULL,  -- 'preview', 'thumbnail', etc.
                data BLOB NOT NULL,
                cached_at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (asset_id, size)
            );

            CREATE INDEX IF NOT EXISTS idx_thumbnails_asset
                ON thumbnails(asset_id);

            -- Quick metadata cache (video info without full analysis)
            CREATE TABLE IF NOT EXISTS video_metadata (
                asset_id TEXT PRIMARY KEY,
                checksum TEXT,
                duration_seconds REAL,
                width INTEGER,
                height INTEGER,
                bitrate INTEGER,
                fps REAL,
                codec TEXT,
                color_space TEXT,
                color_transfer TEXT,
                color_primaries TEXT,
                bit_depth INTEGER,
                cached_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_video_metadata_checksum
                ON video_metadata(checksum);
        """)

    def _migration_v3_remove_thumbnail_blobs(self, conn: sqlite3.Connection) -> None:
        """Remove thumbnail BLOBs from database.

        Thumbnails are now stored in file system via ThumbnailCache.
        This reduces database size significantly (~64MB for 557 thumbnails).
        """
        # Drop the thumbnails table entirely - data is now in file cache
        conn.execute("DROP TABLE IF EXISTS thumbnails")
        logger.info("Dropped thumbnails table - thumbnails now stored in file cache")

    def _migration_v4_run_tracking(self, conn: sqlite3.Connection) -> None:
        """Add run tracking tables for pipeline versioning and statistics."""
        conn.executescript("""
            -- Pipeline runs table
            CREATE TABLE IF NOT EXISTS pipeline_runs (
                run_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                completed_at TEXT,
                status TEXT NOT NULL DEFAULT 'running',
                person_name TEXT,
                person_id TEXT,
                date_range_start TEXT,
                date_range_end TEXT,
                target_duration_minutes INTEGER DEFAULT 10,
                output_path TEXT,
                output_size_bytes INTEGER DEFAULT 0,
                output_duration_seconds REAL DEFAULT 0.0,
                clips_analyzed INTEGER DEFAULT 0,
                clips_selected INTEGER DEFAULT 0,
                errors_count INTEGER DEFAULT 0,
                system_info TEXT  -- JSON
            );

            CREATE INDEX IF NOT EXISTS idx_runs_created
                ON pipeline_runs(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_runs_status
                ON pipeline_runs(status);
            CREATE INDEX IF NOT EXISTS idx_runs_person
                ON pipeline_runs(person_name);

            -- Phase statistics table
            CREATE TABLE IF NOT EXISTS phase_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                phase_name TEXT NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                duration_seconds REAL DEFAULT 0.0,
                items_processed INTEGER DEFAULT 0,
                items_total INTEGER DEFAULT 0,
                errors TEXT,  -- JSON array
                extra_metrics TEXT,  -- JSON dict
                FOREIGN KEY (run_id) REFERENCES pipeline_runs(run_id)
                    ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_phase_run ON phase_stats(run_id);
            CREATE INDEX IF NOT EXISTS idx_phase_name
                ON phase_stats(phase_name);
        """)
        logger.info("Created pipeline_runs and phase_stats tables for run tracking")

    def _migration_v5_add_rotation(self, conn: sqlite3.Connection) -> None:
        """Add rotation column to video_metadata table.

        iPhone videos often have rotation metadata that affects displayed
        orientation. For example, a video stored as 1920x1080 with 90deg
        rotation is actually portrait.
        """
        conn.execute("""
            ALTER TABLE video_metadata ADD COLUMN rotation INTEGER DEFAULT 0
        """)
        logger.info("Added rotation column to video_metadata table")
