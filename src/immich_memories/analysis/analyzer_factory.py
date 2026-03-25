"""Factory function for creating UnifiedSegmentAnalyzer from configuration.

Extracted from unified_analyzer.py to keep file size manageable.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from immich_memories.analysis.scoring import SceneScorer

if TYPE_CHECKING:
    from immich_memories.config_loader import Config

logger = logging.getLogger(__name__)


def create_unified_analyzer_from_config(config: Config):
    """Create a UnifiedSegmentAnalyzer from current configuration.

    Args:
        config: App config.

    Returns:
        Configured UnifiedSegmentAnalyzer instance.
    """
    from immich_memories.analysis.unified_analyzer import UnifiedSegmentAnalyzer

    # Get content analyzer if enabled
    content_analyzer = None
    content_weight = 0.0

    if config.content_analysis.enabled:
        try:
            from immich_memories.analysis.content_analyzer import get_content_analyzer

            content_analyzer = get_content_analyzer(
                provider=config.llm.provider,
                base_url=config.llm.base_url,
                model=config.llm.model,
                api_key=config.llm.api_key,
            )
            if content_analyzer:
                content_weight = config.content_analysis.weight
            else:
                logger.warning("Content analysis enabled but no analyzer available")
        except Exception as e:
            logger.warning(f"Failed to initialize content analyzer: {e}")

    # Get audio content analysis settings
    audio_content_enabled = config.audio_content.enabled
    audio_content_weight = config.audio_content.weight if audio_content_enabled else 0.0

    # Log duration scoring config
    logger.info(
        f"Duration scoring config: base={config.analysis.optimal_clip_duration:.1f}s, "
        f"max={config.analysis.max_optimal_duration:.1f}s, "
        f"ratio={config.analysis.target_extraction_ratio * 100:.0f}%"
    )

    return UnifiedSegmentAnalyzer(
        scorer=SceneScorer(
            content_analysis_config=config.content_analysis,
            analysis_config=config.analysis,
        ),
        content_analyzer=content_analyzer,
        min_segment_duration=config.analysis.min_segment_duration,
        max_segment_duration=config.analysis.max_segment_duration,
        silence_threshold_db=config.analysis.silence_threshold_db,
        min_silence_duration=config.analysis.min_silence_duration,
        cut_point_merge_tolerance=config.analysis.cut_point_merge_tolerance,
        content_weight=content_weight,
        audio_content_enabled=audio_content_enabled,
        audio_content_weight=audio_content_weight,
        optimal_clip_duration=config.analysis.optimal_clip_duration,
        max_optimal_duration=config.analysis.max_optimal_duration,
        target_extraction_ratio=config.analysis.target_extraction_ratio,
        audio_content_config=config.audio_content,
        analysis_config=config.analysis,
    )
