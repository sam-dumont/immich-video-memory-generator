"""Schema creation and migration for the analysis cache database.

Every ``_migration_vN_*`` step is frozen once shipped: an existing cache is
upgraded by replaying only the steps its recorded version has not seen, so
editing a released step leaves already-migrated databases inconsistent.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import TYPE_CHECKING, Protocol

from immich_memories.cache.migration_sql import execute_migration_script
from immich_memories.cache.migration_v11 import migrate_automation_history
from immich_memories.cache.migration_v12 import migrate_automation_attempt_identity
from immich_memories.cache.migration_v13 import migrate_delivery_state
from immich_memories.cache.migration_v14 import migrate_operational_phases
from immich_memories.cache.migration_v15 import migrate_notification_health
from immich_memories.cache.migration_v16 import migrate_video_analysis_model_version
from immich_memories.cache.migration_v17 import migrate_segment_transcripts
from immich_memories.cache.migration_v19 import migrate_target_duration_seconds

if TYPE_CHECKING:
    from contextlib import AbstractContextManager

logger = logging.getLogger(__name__)


class ConnectionSource(Protocol):
    """Opens a connection to the cache database the migrator should upgrade."""

    def __call__(self) -> AbstractContextManager[sqlite3.Connection]: ...


class SchemaMigrator:
    """Applies the cache database schema ladder, one version at a time."""

    def __init__(self, connect: ConnectionSource) -> None:
        self._connect = connect

    def migrate_to(self, target_version: int) -> None:
        """Bring the database up to ``target_version``, creating it if new.

        Version discovery happens inside the same exclusive transaction as the
        migrations themselves, so a second initializer racing the first blocks
        until the ladder is recorded instead of replaying it.
        """
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS schema_migrations (
                        version INTEGER PRIMARY KEY,
                        applied_at TEXT NOT NULL DEFAULT (datetime('now')),
                        description TEXT
                    )
                """)

                result = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
                current_version = result[0] or 0

                if current_version < target_version:
                    self._apply_migrations(conn, current_version, target_version)
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def _apply_migrations(
        self,
        conn: sqlite3.Connection,
        from_version: int,
        target_version: int,
    ) -> None:
        migrations = {
            1: self._migration_v1_initial,
            2: self._migration_v2_thumbnails,
            3: self._migration_v3_remove_thumbnail_blobs,
            4: self._migration_v4_run_tracking,
            5: self._migration_v5_add_rotation,
            6: self._migration_v6_add_llm_and_audio,
            7: self._migration_v7_asset_scores,
            8: self._migration_v8_scoring_version,
            9: self._migration_v9_automation,
            10: self._migration_v10_automation_state,
            11: self._migration_v11_automation_history,
            12: migrate_automation_attempt_identity,
            13: migrate_delivery_state,
            14: migrate_operational_phases,
            15: migrate_notification_health,
            16: migrate_video_analysis_model_version,
            17: migrate_segment_transcripts,
            18: self._migration_v18_llm_category,
            19: migrate_target_duration_seconds,
            20: self._migration_v20_safe_cut_gaps,
        }

        for version in range(from_version + 1, target_version + 1):
            if version in migrations:
                logger.info(f"Applying migration v{version}")
                migrations[version](conn)
                conn.execute(
                    "INSERT INTO schema_migrations (version, description) VALUES (?, ?)",
                    (version, f"Migration to v{version}"),
                )

    def _migration_v1_initial(self, conn: sqlite3.Connection) -> None:
        execute_migration_script(
            conn,
            """
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
        """,
        )

    def _migration_v2_thumbnails(self, conn: sqlite3.Connection) -> None:
        execute_migration_script(
            conn,
            """
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
        """,
        )

    def _migration_v3_remove_thumbnail_blobs(self, conn: sqlite3.Connection) -> None:
        """Remove thumbnail BLOBs from database.

        Thumbnails are now stored in file system via ThumbnailCache.
        This reduces database size significantly (~64MB for 557 thumbnails).
        """
        # Drop the thumbnails table entirely - data is now in file cache
        conn.execute("DROP TABLE IF EXISTS thumbnails")
        logger.info("Dropped thumbnails table - thumbnails now stored in file cache")

    def _migration_v4_run_tracking(self, conn: sqlite3.Connection) -> None:
        execute_migration_script(
            conn,
            """
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
        """,
        )
        logger.info("Created pipeline_runs and phase_stats tables for run tracking")

    def _migration_v5_add_rotation(self, conn: sqlite3.Connection) -> None:
        """Add rotation column to video_metadata table."""
        conn.execute("""
            ALTER TABLE video_metadata ADD COLUMN rotation INTEGER DEFAULT 0
        """)
        logger.info("Added rotation column to video_metadata table")

    def _migration_v6_add_llm_and_audio(self, conn: sqlite3.Connection) -> None:
        """Add LLM analysis and audio category columns to video_segments."""
        columns = [
            ("llm_description", "TEXT"),
            ("llm_emotion", "TEXT"),
            ("llm_setting", "TEXT"),
            ("llm_activities", "TEXT"),  # JSON array
            ("llm_subjects", "TEXT"),  # JSON array
            ("llm_interestingness", "REAL"),
            ("llm_quality", "REAL"),
            ("audio_categories", "TEXT"),  # JSON array
        ]
        for col_name, col_type in columns:
            conn.execute(
                f"ALTER TABLE video_segments ADD COLUMN {col_name} {col_type}"
            )  # nosemgrep: sqlalchemy-execute-raw-query — col_name/col_type are hardcoded above
        logger.info("Added LLM and audio_categories columns to video_segments")

    def _migration_v20_safe_cut_gaps(self, conn: sqlite3.Connection) -> None:
        """Persist where a cut may land, beside the boundaries it justifies.

        Without it a cached rerun restores start and end without the evidence
        behind them, so the hold pass refuses to extend anything and ships a
        shorter cut than the identical cold run. Existing rows stay NULL and
        behave as they did until the asset is analysed again.
        """
        conn.execute("ALTER TABLE video_segments ADD COLUMN safe_cut_gaps TEXT")
        logger.info("Added safe_cut_gaps column to video_segments")

    def _migration_v18_llm_category(self, conn: sqlite3.Connection) -> None:
        """Add the closed-set subject category to video_segments.

        Existing rows stay NULL and fall back to keyword classification of
        llm_description until they are re-analysed.
        """
        conn.execute("ALTER TABLE video_segments ADD COLUMN llm_category TEXT")
        logger.info("Added llm_category column to video_segments")

    def _migration_v7_asset_scores(self, conn: sqlite3.Connection) -> None:
        """Add asset_scores table for cache-first LLM scoring."""
        execute_migration_script(
            conn,
            """
            CREATE TABLE IF NOT EXISTS asset_scores (
                asset_id TEXT PRIMARY KEY,
                asset_type TEXT NOT NULL,
                llm_interest REAL,
                llm_quality REAL,
                llm_emotion TEXT,
                llm_description TEXT,
                llm_category TEXT,
                safe_cut_gaps TEXT,
                metadata_score REAL NOT NULL,
                combined_score REAL NOT NULL,
                analyzed_at TEXT NOT NULL DEFAULT (datetime('now')),
                model_version TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_asset_scores_type
                ON asset_scores(asset_type);
            CREATE INDEX IF NOT EXISTS idx_asset_scores_combined
                ON asset_scores(combined_score DESC);
        """,
        )
        logger.info("Created asset_scores table for cache-first scoring")

    def _migration_v8_scoring_version(self, conn: sqlite3.Connection) -> None:
        """Add scoring_version column to track scoring algorithm changes."""
        conn.execute("""
            ALTER TABLE video_analysis
            ADD COLUMN scoring_version INTEGER NOT NULL DEFAULT 1
        """)
        logger.info("Added scoring_version column to video_analysis")

    def _migration_v9_automation(self, conn: sqlite3.Connection) -> None:
        """Add memory_type, memory_key, source columns for automation dedup."""
        conn.execute("ALTER TABLE pipeline_runs ADD COLUMN memory_type TEXT")
        conn.execute("ALTER TABLE pipeline_runs ADD COLUMN memory_key TEXT")
        conn.execute("ALTER TABLE pipeline_runs ADD COLUMN source TEXT DEFAULT 'manual'")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_memory_key ON pipeline_runs(memory_key)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_runs_memory_type ON pipeline_runs(memory_type)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_source ON pipeline_runs(source)")
        logger.info("Added memory_type, memory_key, source columns for automation")

    def _migration_v10_automation_state(self, conn: sqlite3.Connection) -> None:
        """Add durable automation state and exact run identity fields."""
        conn.execute("ALTER TABLE pipeline_runs ADD COLUMN memory_category TEXT")
        conn.execute(
            """
            ALTER TABLE pipeline_runs
            ADD COLUMN memory_people_json TEXT NOT NULL DEFAULT '[]'
            """
        )
        conn.execute(
            """
            CREATE TABLE automation_attempts (
                id TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                outcome TEXT NOT NULL,
                reason TEXT NOT NULL,
                candidate_category TEXT,
                memory_type TEXT,
                memory_key TEXT,
                run_id TEXT,
                error TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX idx_auto_attempts_started
            ON automation_attempts(started_at DESC)
            """
        )
        logger.info("Added automation attempt state and exact run identity columns")

    def _migration_v11_automation_history(self, conn: sqlite3.Connection) -> None:
        """Normalize legacy local timestamps and restore conservative run identity."""
        migrate_automation_history(conn)
        logger.info("Normalized automation history timestamps and identity")
