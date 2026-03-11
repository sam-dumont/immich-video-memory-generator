"""Query and read methods for video analysis cache."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import TYPE_CHECKING

from immich_memories.cache.database_migrations import SCHEMA_VERSION
from immich_memories.cache.database_models import (
    CachedSegment,
    CachedVideoAnalysis,
    SimilarVideo,
    _hamming_distance,
)

if TYPE_CHECKING:
    from immich_memories.api.models import Asset


class DatabaseQueryMixin:
    """Mixin providing query/read methods for video analysis cache.

    Requires the consuming class to provide:
    - self.db_path: Path
    - self._get_connection() -> contextmanager yielding sqlite3.Connection
    """

    def get_analysis(
        self,
        asset_id: str,
        include_segments: bool = True,
    ) -> CachedVideoAnalysis | None:
        """Get cached analysis for an asset.

        Args:
            asset_id: The asset ID.
            include_segments: Whether to load segment data.

        Returns:
            Cached analysis or None if not found.
        """
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
        """Convert database row to CachedVideoAnalysis."""
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
        """Load segments for an asset."""
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

    def needs_reanalysis(
        self,
        asset: Asset,
        max_age_days: int | None = None,
    ) -> bool:
        """Check if a video needs re-analysis.

        Args:
            asset: The asset to check.
            max_age_days: Override for cache max age.

        Returns:
            True if video needs re-analysis.
        """
        if max_age_days is None:
            from immich_memories.config import get_config

            config = get_config()
            max_age_days = config.cache.max_age_days

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
                return True  # Not in cache

            # Check if schema version changed
            if row["analysis_version"] != SCHEMA_VERSION:
                return True

            # Check if file was modified
            if asset.checksum and row["checksum"]:
                if asset.checksum != row["checksum"]:
                    return True
            elif asset.file_modified_at and row["file_modified_at"]:
                cached_modified = datetime.fromisoformat(row["file_modified_at"])
                if asset.file_modified_at > cached_modified:
                    return True

            # Check cache age
            analysis_time = datetime.fromisoformat(row["analysis_timestamp"])
            age = datetime.now() - analysis_time
            return age.days > max_age_days

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
            params: list = list(chunks)

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
        """Get asset IDs that need analysis (not cached or stale).

        Args:
            asset_ids: List of asset IDs to check.
            checksums: Optional dict of asset_id -> checksum for staleness.

        Returns:
            List of asset IDs needing analysis.
        """
        if not asset_ids:
            return []

        with self._get_connection() as conn:
            # Get cached asset IDs
            placeholders = ",".join("?" * len(asset_ids))
            rows = conn.execute(
                f"SELECT asset_id, checksum FROM video_analysis WHERE asset_id IN ({placeholders})",  # noqa: S608
                asset_ids,
            ).fetchall()

            cached = {row["asset_id"]: row["checksum"] for row in rows}

            # Find uncached or stale
            uncached = []
            for asset_id in asset_ids:
                if (
                    asset_id not in cached
                    or checksums
                    and asset_id in checksums
                    and cached[asset_id] != checksums[asset_id]
                ):
                    uncached.append(asset_id)

            return uncached

    def get_all_hashes(self) -> dict[str, str]:
        """Get all perceptual hashes for clustering.

        Returns:
            Dict of asset_id -> hash_value.
        """
        with self._get_connection() as conn:
            rows = conn.execute("SELECT asset_id, full_hash FROM hash_index").fetchall()
            return {row["asset_id"]: row["full_hash"] for row in rows}

    def get_stats(self) -> dict:
        """Get cache statistics.

        Returns:
            Dictionary with cache stats.
        """
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
                    self.db_path.stat().st_size  # type: ignore[attr-defined]
                    if self.db_path.exists()  # type: ignore[attr-defined]
                    else 0
                ),
            }
