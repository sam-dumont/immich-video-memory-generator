"""SQLite-based cache for video analysis results."""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from immich_memories.cache.database_models import (  # noqa: F401
    CachedSegment,
    CachedVideoAnalysis,
    SimilarVideo,
    _hamming_distance,
)

if TYPE_CHECKING:
    from immich_memories.analysis.scenes import Scene
    from immich_memories.analysis.scoring import MomentScore
    from immich_memories.api.models import Asset, VideoClipInfo

logger = logging.getLogger(__name__)

# Current schema version - increment when schema changes
SCHEMA_VERSION = 6


class VideoAnalysisCache:
    """SQLite-based cache for video analysis results."""

    def __init__(self, db_path: Path | None = None):
        """Uses config default if db_path not specified."""
        if db_path is None:
            from immich_memories.config import get_config

            config = get_config()
            db_path = config.cache.database_path

        self.db_path = Path(db_path)
        self._ensure_db_exists()
        self._run_migrations()

    def _ensure_db_exists(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _get_connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(
            self.db_path,
            detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")  # Better concurrent access
        try:
            yield conn
        finally:
            conn.close()

    # =========================================================================
    # Migrations (from DatabaseMigrationsMixin)
    # =========================================================================

    def _run_migrations(self) -> None:
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
        migrations = {
            1: self._migration_v1_initial,
            2: self._migration_v2_thumbnails,
            3: self._migration_v3_remove_thumbnail_blobs,
            4: self._migration_v4_run_tracking,
            5: self._migration_v5_add_rotation,
            6: self._migration_v6_add_llm_and_audio,
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

    # =========================================================================
    # Quick Video Metadata Methods
    # =========================================================================

    def save_video_metadata(
        self,
        asset_id: str,
        checksum: str | None = None,
        duration_seconds: float | None = None,
        width: int | None = None,
        height: int | None = None,
        bitrate: int | None = None,
        fps: float | None = None,
        codec: str | None = None,
        color_space: str | None = None,
        color_transfer: str | None = None,
        color_primaries: str | None = None,
        bit_depth: int | None = None,
        rotation: int | None = None,
    ) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO video_metadata (
                    asset_id, checksum, duration_seconds, width, height,
                    bitrate, fps, codec, color_space, color_transfer,
                    color_primaries, bit_depth, rotation, cached_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                (
                    asset_id,
                    checksum,
                    duration_seconds,
                    width,
                    height,
                    bitrate,
                    fps,
                    codec,
                    color_space,
                    color_transfer,
                    color_primaries,
                    bit_depth,
                    rotation or 0,
                ),
            )
            conn.commit()

    def get_video_metadata(self, asset_id: str) -> dict | None:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM video_metadata WHERE asset_id = ?", (asset_id,)
            ).fetchone()
            if not row:
                return None
            return {
                "duration_seconds": row["duration_seconds"],
                "width": row["width"],
                "height": row["height"],
                "bitrate": row["bitrate"],
                "fps": row["fps"],
                "codec": row["codec"],
                "color_space": row["color_space"],
                "color_transfer": row["color_transfer"],
                "color_primaries": row["color_primaries"],
                "bit_depth": row["bit_depth"],
                "rotation": row["rotation"] if "rotation" in row else 0,  # noqa: SIM401
            }

    def get_video_metadata_batch(self, asset_ids: list[str]) -> dict[str, dict]:
        if not asset_ids:
            return {}

        with self._get_connection() as conn:
            placeholders = ",".join("?" * len(asset_ids))
            rows = conn.execute(
                f"SELECT * FROM video_metadata WHERE asset_id IN ({placeholders})",  # noqa: S608  # nosemgrep: sqlalchemy-execute-raw-query — placeholders are parameterized ?-marks
                asset_ids,
            ).fetchall()

            result = {}
            for row in rows:
                result[row["asset_id"]] = {
                    "duration_seconds": row["duration_seconds"],
                    "width": row["width"],
                    "height": row["height"],
                    "bitrate": row["bitrate"],
                    "fps": row["fps"],
                    "codec": row["codec"],
                    "color_space": row["color_space"],
                    "color_transfer": row["color_transfer"],
                    "color_primaries": row["color_primaries"],
                    "bit_depth": row["bit_depth"],
                    "rotation": (row["rotation"] if "rotation" in row else 0),  # noqa: SIM401
                }
            return result

    # =========================================================================
    # Core CRUD Methods
    # =========================================================================

    def save_analysis(
        self,
        asset: Asset,
        video_info: VideoClipInfo | None = None,
        perceptual_hash: str | None = None,
        thumbnail_hash: str | None = None,
        segments: list[MomentScore] | list | None = None,
        scenes: list[Scene] | None = None,
        motion_summary: dict | None = None,
        audio_levels: dict | None = None,
    ) -> None:
        now = datetime.now().isoformat()

        # Compute best scores from segments
        best_scores = self._compute_best_scores(segments)

        with self._get_connection() as conn:
            # Insert/update main analysis record
            conn.execute(
                """
                INSERT OR REPLACE INTO video_analysis (
                    asset_id, checksum, file_modified_at, analysis_timestamp,
                    analysis_version, perceptual_hash, thumbnail_hash,
                    duration_seconds, width, height, bitrate, fps, codec,
                    color_space, color_transfer, color_primaries, bit_depth,
                    best_face_score, best_motion_score, best_stability_score,
                    best_audio_score, best_total_score,
                    motion_summary, audio_levels, file_created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    asset.id,
                    asset.checksum,
                    (asset.file_modified_at.isoformat() if asset.file_modified_at else None),
                    now,
                    SCHEMA_VERSION,
                    perceptual_hash,
                    thumbnail_hash,
                    (video_info.duration_seconds if video_info else asset.duration_seconds),
                    video_info.width if video_info else None,
                    video_info.height if video_info else None,
                    video_info.bitrate if video_info else None,
                    video_info.fps if video_info else None,
                    video_info.codec if video_info else None,
                    video_info.color_space if video_info else None,
                    video_info.color_transfer if video_info else None,
                    video_info.color_primaries if video_info else None,
                    video_info.bit_depth if video_info else None,
                    best_scores.get("face"),
                    best_scores.get("motion"),
                    best_scores.get("stability"),
                    best_scores.get("audio"),
                    best_scores.get("total"),
                    json.dumps(motion_summary) if motion_summary else None,
                    json.dumps(audio_levels) if audio_levels else None,
                    (asset.file_created_at.isoformat() if asset.file_created_at else None),
                ),
            )

            # Delete existing segments
            conn.execute("DELETE FROM video_segments WHERE asset_id = ?", (asset.id,))

            # Insert segments
            if segments:
                self._save_segments_from_moments(conn, asset.id, segments)
            elif scenes:
                self._save_segments_from_scenes(conn, asset.id, scenes)

            # Update hash index
            if perceptual_hash:
                self._update_hash_index(conn, asset.id, perceptual_hash)

            conn.commit()

    def _compute_best_scores(self, segments: list | None) -> dict:
        if not segments:
            return {}

        # Ensure all values are Python floats (not numpy.float64)
        return {
            "face": float(max(s.face_score for s in segments)),
            "motion": float(max(s.motion_score for s in segments)),
            "stability": float(max(s.stability_score for s in segments)),
            "audio": float(max(s.audio_score for s in segments)),
            "total": float(max(s.total_score for s in segments)),
        }

    def _save_segments_from_moments(
        self,
        conn: sqlite3.Connection,
        asset_id: str,
        segments: list,
    ) -> None:
        for i, segment in enumerate(segments):
            # Serialize list fields to JSON
            activities = getattr(segment, "llm_activities", None)
            subjects = getattr(segment, "llm_subjects", None)
            audio_cats = getattr(segment, "audio_categories", None)

            conn.execute(
                """
                INSERT INTO video_segments (
                    asset_id, segment_index, start_time, end_time,
                    face_score, motion_score, stability_score,
                    audio_score, total_score, face_positions,
                    llm_description, llm_emotion, llm_setting,
                    llm_activities, llm_subjects,
                    llm_interestingness, llm_quality,
                    audio_categories
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    asset_id,
                    i,
                    segment.start_time,
                    segment.end_time,
                    segment.face_score,
                    segment.motion_score,
                    segment.stability_score,
                    segment.audio_score,
                    segment.total_score,
                    (json.dumps(segment.face_positions) if segment.face_positions else None),
                    getattr(segment, "llm_description", None),
                    getattr(segment, "llm_emotion", None),
                    getattr(segment, "llm_setting", None),
                    json.dumps(list(activities)) if activities else None,
                    json.dumps(list(subjects)) if subjects else None,
                    getattr(segment, "llm_interestingness", None),
                    getattr(segment, "llm_quality", None),
                    json.dumps(sorted(audio_cats)) if audio_cats else None,
                ),
            )

    def _save_segments_from_scenes(
        self,
        conn: sqlite3.Connection,
        asset_id: str,
        scenes: list[Scene],
    ) -> None:
        for i, scene in enumerate(scenes):
            conn.execute(
                """
                INSERT INTO video_segments (
                    asset_id, segment_index, start_time, end_time,
                    start_frame, end_frame, keyframe_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    asset_id,
                    i,
                    scene.start_time,
                    scene.end_time,
                    scene.start_frame,
                    scene.end_frame,
                    scene.keyframe_path,
                ),
            )

    def _update_hash_index(
        self,
        conn: sqlite3.Connection,
        asset_id: str,
        hash_value: str,
    ) -> None:
        # Pad hash to 16 chars if needed
        padded = hash_value.ljust(16, "0")[:16]

        conn.execute(
            """
            INSERT OR REPLACE INTO hash_index (
                asset_id, hash_chunk_0, hash_chunk_1,
                hash_chunk_2, hash_chunk_3, full_hash
            ) VALUES (?, ?, ?, ?, ?, ?)
        """,
            (
                asset_id,
                padded[0:4],
                padded[4:8],
                padded[8:12],
                padded[12:16],
                hash_value,
            ),
        )

    def delete_analysis(self, asset_id: str) -> bool:
        with self._get_connection() as conn:
            cursor = conn.execute("DELETE FROM video_analysis WHERE asset_id = ?", (asset_id,))
            conn.commit()
            return cursor.rowcount > 0

    def clear_all(self) -> int:
        with self._get_connection() as conn:
            cursor = conn.execute("DELETE FROM video_analysis")
            count = cursor.rowcount
            conn.commit()
            return count

    # =========================================================================
    # Query Methods (from DatabaseQueryMixin)
    # =========================================================================

    def get_analysis(
        self,
        asset_id: str,
        include_segments: bool = True,
    ) -> CachedVideoAnalysis | None:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM video_analysis WHERE asset_id = ?", (asset_id,)
            ).fetchone()

            if not row:
                return None

            analysis = self._row_to_analysis(row)

            if include_segments:
                analysis.segments = self._load_segments(conn, asset_id)

            return analysis

    def _row_to_analysis(self, row: sqlite3.Row) -> CachedVideoAnalysis:
        return CachedVideoAnalysis(
            asset_id=row["asset_id"],
            checksum=row["checksum"],
            file_modified_at=(
                datetime.fromisoformat(row["file_modified_at"]) if row["file_modified_at"] else None
            ),
            analysis_timestamp=datetime.fromisoformat(row["analysis_timestamp"]),
            perceptual_hash=row["perceptual_hash"],
            thumbnail_hash=row["thumbnail_hash"],
            duration_seconds=row["duration_seconds"],
            width=row["width"],
            height=row["height"],
            bitrate=row["bitrate"],
            fps=row["fps"],
            codec=row["codec"],
            color_space=row["color_space"],
            color_transfer=row["color_transfer"],
            color_primaries=row["color_primaries"],
            bit_depth=row["bit_depth"],
            best_face_score=row["best_face_score"],
            best_motion_score=row["best_motion_score"],
            best_stability_score=row["best_stability_score"],
            best_audio_score=row["best_audio_score"],
            best_total_score=row["best_total_score"],
            motion_summary=(json.loads(row["motion_summary"]) if row["motion_summary"] else None),
            audio_levels=(json.loads(row["audio_levels"]) if row["audio_levels"] else None),
            file_created_at=(
                datetime.fromisoformat(row["file_created_at"]) if row["file_created_at"] else None
            ),
        )

    def _load_segments(self, conn: sqlite3.Connection, asset_id: str) -> list[CachedSegment]:
        rows = conn.execute(
            """
            SELECT * FROM video_segments
            WHERE asset_id = ?
            ORDER BY segment_index
        """,
            (asset_id,),
        ).fetchall()

        return [self._row_to_segment(row) for row in rows]

    def _row_to_segment(self, row: sqlite3.Row) -> CachedSegment:
        """Convert database row to CachedSegment (including LLM + audio data)."""
        face_positions = None
        if row["face_positions"]:
            positions = json.loads(row["face_positions"])
            face_positions = [tuple(p) for p in positions]

        # Safely access v6+ columns (absent in pre-migration databases)
        keys = row.keys()

        def _str(name: str) -> str | None:
            return str(row[name]) if name in keys and row[name] is not None else None

        def _float(name: str) -> float | None:
            val = row[name] if name in keys else None
            return float(val) if val is not None else None

        activities_raw = _str("llm_activities")
        subjects_raw = _str("llm_subjects")
        audio_cats_raw = _str("audio_categories")

        return CachedSegment(
            segment_index=row["segment_index"],
            start_time=row["start_time"],
            end_time=row["end_time"],
            start_frame=row["start_frame"],
            end_frame=row["end_frame"],
            face_score=row["face_score"],
            motion_score=row["motion_score"],
            stability_score=row["stability_score"],
            audio_score=row["audio_score"],
            total_score=row["total_score"],
            face_positions=face_positions,
            motion_vectors=(json.loads(row["motion_vectors"]) if row["motion_vectors"] else None),
            keyframe_path=row["keyframe_path"],
            llm_description=_str("llm_description"),
            llm_emotion=_str("llm_emotion"),
            llm_setting=_str("llm_setting"),
            llm_activities=(json.loads(activities_raw) if activities_raw else None),
            llm_subjects=(json.loads(subjects_raw) if subjects_raw else None),
            llm_interestingness=_float("llm_interestingness"),
            llm_quality=_float("llm_quality"),
            audio_categories=(json.loads(audio_cats_raw) if audio_cats_raw else None),
        )

    @staticmethod
    def _row_is_stale(row: sqlite3.Row, asset: Asset) -> bool:
        """Return True if the cached row is stale due to file modification."""
        if asset.checksum and row["checksum"]:
            return asset.checksum != row["checksum"]
        if asset.file_modified_at and row["file_modified_at"]:
            cached_modified = datetime.fromisoformat(row["file_modified_at"])
            return asset.file_modified_at > cached_modified
        return False

    def needs_reanalysis(
        self,
        asset: Asset,
        max_age_days: int | None = None,
    ) -> bool:
        if max_age_days is None:
            from immich_memories.config import get_config

            max_age_days = get_config().cache.max_age_days

        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT checksum, file_modified_at, analysis_timestamp,
                       analysis_version
                FROM video_analysis
                WHERE asset_id = ?
            """,
                (asset.id,),
            ).fetchone()

            if not row:
                return True
            if row["analysis_version"] != SCHEMA_VERSION:
                return True
            if self._row_is_stale(row, asset):
                return True

            analysis_time = datetime.fromisoformat(row["analysis_timestamp"])
            return (datetime.now() - analysis_time).days > max_age_days

    def find_similar_videos(
        self,
        hash_value: str,
        threshold: int | None = None,
        exclude_asset_id: str | None = None,
    ) -> list[SimilarVideo]:
        """Find videos with similar perceptual hashes.

        Args:
            hash_value: The hash to compare against.
            threshold: Hamming distance threshold (default: 8).
            exclude_asset_id: Asset ID to exclude from results.

        Returns:
            List of similar videos sorted by distance.
        """
        if threshold is None:
            threshold = 8

        # Use chunk-based pre-filtering for efficiency
        padded = hash_value.ljust(16, "0")[:16]
        chunks = [padded[0:4], padded[4:8], padded[8:12], padded[12:16]]

        with self._get_connection() as conn:
            # Pre-filter: find candidates with at least one matching chunk
            query = """
                SELECT asset_id, full_hash FROM hash_index
                WHERE (hash_chunk_0 = ? OR hash_chunk_1 = ?
                       OR hash_chunk_2 = ? OR hash_chunk_3 = ?)
            """
            params: list = chunks.copy()

            if exclude_asset_id:
                query += " AND asset_id != ?"
                params.append(exclude_asset_id)

            rows = conn.execute(query, params).fetchall()

            # Full Hamming distance check on candidates
            similar = []
            for row in rows:
                distance = _hamming_distance(hash_value, row["full_hash"])
                if distance <= threshold:
                    similar.append(
                        SimilarVideo(
                            asset_id=row["asset_id"],
                            hash_value=row["full_hash"],
                            hamming_distance=distance,
                        )
                    )

            # Sort by distance
            similar.sort(key=lambda x: x.hamming_distance)
            return similar

    def get_uncached_asset_ids(
        self,
        asset_ids: list[str],
        checksums: dict[str, str | None] | None = None,
    ) -> list[str]:
        if not asset_ids:
            return []

        with self._get_connection() as conn:
            # Get cached asset IDs
            placeholders = ",".join("?" * len(asset_ids))
            rows = conn.execute(
                f"SELECT asset_id, checksum FROM video_analysis WHERE asset_id IN ({placeholders})",  # noqa: S608  # nosemgrep: sqlalchemy-execute-raw-query — parameterized ?-marks
                asset_ids,
            ).fetchall()

            cached = {row["asset_id"]: row["checksum"] for row in rows}

            # Find uncached or stale
            uncached = [
                asset_id
                for asset_id in asset_ids
                if (
                    asset_id not in cached
                    or checksums
                    and asset_id in checksums
                    and cached[asset_id] != checksums[asset_id]
                )
            ]

            return uncached

    def get_all_hashes(self) -> dict[str, str]:
        with self._get_connection() as conn:
            rows = conn.execute("SELECT asset_id, full_hash FROM hash_index").fetchall()
            return {row["asset_id"]: row["full_hash"] for row in rows}

    def get_stats(self) -> dict:
        with self._get_connection() as conn:
            total = conn.execute("SELECT COUNT(*) FROM video_analysis").fetchone()[0]

            with_hash = conn.execute(
                "SELECT COUNT(*) FROM video_analysis WHERE perceptual_hash IS NOT NULL"
            ).fetchone()[0]

            total_segments = conn.execute("SELECT COUNT(*) FROM video_segments").fetchone()[0]

            oldest = conn.execute("SELECT MIN(analysis_timestamp) FROM video_analysis").fetchone()[
                0
            ]

            newest = conn.execute("SELECT MAX(analysis_timestamp) FROM video_analysis").fetchone()[
                0
            ]

            return {
                "total_videos": total,
                "videos_with_hash": with_hash,
                "total_segments": total_segments,
                "oldest_analysis": oldest,
                "newest_analysis": newest,
                "database_size_bytes": (
                    self.db_path.stat().st_size if self.db_path.exists() else 0
                ),
            }
