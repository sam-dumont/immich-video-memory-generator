"""SQLite-based cache for video analysis results."""

from __future__ import annotations

import json
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
from immich_memories.cache.database_rows import row_to_analysis, row_to_segment
from immich_memories.cache.schema_migrator import SchemaMigrator
from immich_memories.cache.versions import ANALYSIS_VERSION, SCHEMA_VERSION, SCORING_VERSION

if TYPE_CHECKING:
    from immich_memories.analysis.scenes import Scene
    from immich_memories.analysis.scoring import MomentScore
    from immich_memories.api.models import Asset, VideoClipInfo


class VideoAnalysisCache:
    """SQLite-based cache for video analysis results."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self._ensure_db_exists()
        SchemaMigrator(self._get_connection).migrate_to(SCHEMA_VERSION)

    def _ensure_db_exists(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _get_connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(
            self.db_path,
            timeout=5.0,  # busy_timeout=5000ms — retry on concurrent access
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
        model_version: str | None = None,
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
                    analysis_version, scoring_version, perceptual_hash, thumbnail_hash,
                    duration_seconds, width, height, bitrate, fps, codec,
                    color_space, color_transfer, color_primaries, bit_depth,
                    best_face_score, best_motion_score, best_stability_score,
                    best_audio_score, best_total_score,
                    motion_summary, audio_levels, file_created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    asset.id,
                    asset.checksum,
                    (asset.file_modified_at.isoformat() if asset.file_modified_at else None),
                    now,
                    ANALYSIS_VERSION,
                    SCORING_VERSION,
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

            if model_version is not None:
                conn.execute(
                    "UPDATE video_analysis SET model_version = ? WHERE asset_id = ?",
                    (model_version, asset.id),
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
            subjects = getattr(segment, "llm_subjects", None)
            activities = getattr(segment, "llm_activities", None)
            audio_cats = getattr(segment, "audio_categories", None)
            gaps = getattr(segment, "safe_cut_gaps", None)

            conn.execute(
                """
                INSERT INTO video_segments (
                    asset_id, segment_index, start_time, end_time,
                    face_score, motion_score, stability_score,
                    audio_score, total_score, face_positions,
                    llm_description, llm_category, llm_emotion, llm_setting,
                    llm_subjects, llm_activities,
                    llm_interestingness, llm_quality,
                    audio_categories, safe_cut_gaps,
                    transcript, transcript_language, transcript_confidence
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    getattr(segment, "llm_category", None),
                    getattr(segment, "llm_emotion", None),
                    getattr(segment, "llm_setting", None),
                    json.dumps(list(subjects)) if subjects else None,
                    json.dumps(list(activities)) if activities else None,
                    getattr(segment, "llm_interestingness", None),
                    getattr(segment, "llm_quality", None),
                    json.dumps(sorted(audio_cats)) if audio_cats else None,
                    json.dumps(gaps) if gaps else None,
                    getattr(segment, "transcript", None),
                    getattr(segment, "transcript_language", None),
                    getattr(segment, "transcript_confidence", None),
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

            analysis = row_to_analysis(row)

            if include_segments:
                analysis.segments = self._load_segments(conn, asset_id)

            return analysis

    def _load_segments(self, conn: sqlite3.Connection, asset_id: str) -> list[CachedSegment]:
        rows = conn.execute(
            """
            SELECT * FROM video_segments
            WHERE asset_id = ?
            ORDER BY segment_index
        """,
            (asset_id,),
        ).fetchall()

        return [row_to_segment(row) for row in rows]

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
        max_age_days: int,
    ) -> bool:
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT checksum, file_modified_at, analysis_timestamp,
                       analysis_version, scoring_version
                FROM video_analysis
                WHERE asset_id = ?
            """,
                (asset.id,),
            ).fetchone()

            if not row:
                return True
            if row["analysis_version"] != ANALYSIS_VERSION:
                return True
            if row["scoring_version"] != SCORING_VERSION:
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
