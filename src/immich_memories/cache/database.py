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

from immich_memories.cache.database_migrations import (
    SCHEMA_VERSION,
    DatabaseMigrationsMixin,
)
from immich_memories.cache.database_models import (  # noqa: F401
    CachedSegment,
    CachedVideoAnalysis,
    SimilarVideo,
    _hamming_distance,
)
from immich_memories.cache.database_queries import DatabaseQueryMixin

if TYPE_CHECKING:
    from immich_memories.analysis.scenes import Scene
    from immich_memories.analysis.scoring import MomentScore
    from immich_memories.api.models import Asset, VideoClipInfo

logger = logging.getLogger(__name__)


class VideoAnalysisCache(DatabaseMigrationsMixin, DatabaseQueryMixin):
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

    # === Quick Video Metadata Methods ===

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
                f"SELECT * FROM video_metadata WHERE asset_id IN ({placeholders})",  # noqa: S608
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

    # === Core CRUD Methods ===

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
