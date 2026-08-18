"""Project compatible cached analysis back onto in-memory clips."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from immich_memories.api.models import VideoClipInfo
    from immich_memories.cache.database_models import CachedSegment, CachedVideoAnalysis
    from immich_memories.config_loader import Config


def is_compatible_analysis_cache(cached: CachedVideoAnalysis | None, config: Config) -> bool:
    """Return whether a cache row is safe to reuse under the active configuration."""
    if not (cached and cached.segments):
        return False
    if config.content_analysis.enabled:
        return cached.model_version == config.llm.model
    return True


def semantic_payload_from_segment(segment: CachedSegment) -> dict[str, object] | None:
    """Extract persisted semantic fields from one cached segment."""
    payload: dict[str, object] = {
        "description": segment.llm_description,
        "category": segment.llm_category,
        "emotion": segment.llm_emotion,
        "setting": segment.llm_setting,
        "activities": segment.llm_activities,
        "subjects": segment.llm_subjects,
        "interestingness": segment.llm_interestingness,
        "quality": segment.llm_quality,
    }
    return payload if any(value is not None for value in payload.values()) else None


def apply_semantic_payload(
    clip: VideoClipInfo,
    payload: dict[str, object] | None,
) -> None:
    """Apply semantic fields to the clip used by review and generation."""
    if not payload:
        return
    clip.llm_description = cast(str | None, payload.get("description"))
    clip.llm_category = cast(str | None, payload.get("category"))
    clip.llm_emotion = cast(str | None, payload.get("emotion"))
    clip.llm_setting = cast(str | None, payload.get("setting"))
    clip.llm_activities = cast(list[str] | None, payload.get("activities"))
    clip.llm_subjects = cast(list[str] | None, payload.get("subjects"))
    clip.llm_interestingness = cast(float | None, payload.get("interestingness"))
    clip.llm_quality = cast(float | None, payload.get("quality"))


def apply_cached_segment(clip: VideoClipInfo, segment: CachedSegment) -> dict[str, object] | None:
    """Apply both semantic and audio results from a cached segment."""
    payload = semantic_payload_from_segment(segment)
    apply_semantic_payload(clip, payload)
    if segment.audio_categories:
        clip.audio_categories = list(segment.audio_categories)
    return payload
