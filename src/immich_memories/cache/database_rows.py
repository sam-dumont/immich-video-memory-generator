"""SQLite row conversion for the video analysis cache."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from immich_memories.cache.database_models import CachedSegment, CachedVideoAnalysis


def _row_value(row: sqlite3.Row, key: str, default: Any) -> Any:
    """Read a possibly absent column from a legacy-schema row."""
    try:
        return row[key]
    except (IndexError, KeyError):
        return default


def row_to_analysis(row: sqlite3.Row) -> CachedVideoAnalysis:
    """Convert a video-analysis row into its cache model."""
    return CachedVideoAnalysis(
        asset_id=row["asset_id"],
        checksum=row["checksum"],
        file_modified_at=(
            datetime.fromisoformat(row["file_modified_at"]) if row["file_modified_at"] else None
        ),
        analysis_timestamp=datetime.fromisoformat(row["analysis_timestamp"]),
        analysis_version=_row_value(row, "analysis_version", 0),
        scoring_version=_row_value(row, "scoring_version", 1),
        model_version=_row_value(row, "model_version", None),
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


def row_to_segment(row: sqlite3.Row) -> CachedSegment:
    """Convert a segment row, including optional semantic and audio fields."""
    face_positions = None
    if row["face_positions"]:
        positions = json.loads(row["face_positions"])
        face_positions = [tuple(position) for position in positions]

    keys = row.keys()

    def optional_str(name: str) -> str | None:
        return str(row[name]) if name in keys and row[name] is not None else None

    def optional_float(name: str) -> float | None:
        value = row[name] if name in keys else None
        return float(value) if value is not None else None

    subjects_raw = optional_str("llm_subjects")
    audio_categories_raw = optional_str("audio_categories")

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
        llm_description=optional_str("llm_description"),
        llm_category=optional_str("llm_category"),
        llm_emotion=optional_str("llm_emotion"),
        llm_setting=optional_str("llm_setting"),
        llm_subjects=(json.loads(subjects_raw) if subjects_raw else None),
        llm_interestingness=optional_float("llm_interestingness"),
        llm_quality=optional_float("llm_quality"),
        audio_categories=(json.loads(audio_categories_raw) if audio_categories_raw else None),
        transcript=optional_str("transcript"),
        transcript_language=optional_str("transcript_language"),
        transcript_confidence=optional_float("transcript_confidence"),
    )
