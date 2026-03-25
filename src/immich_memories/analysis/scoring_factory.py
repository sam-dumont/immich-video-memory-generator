"""Factory for creating video analyzers from configuration."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from immich_memories.analysis.unified_analyzer import UnifiedSegmentAnalyzer
    from immich_memories.config_loader import Config


def create_analyzer_from_config(config: Config) -> UnifiedSegmentAnalyzer:
    """Create a UnifiedSegmentAnalyzer configured from app config.

    All scoring goes through UnifiedSegmentAnalyzer which combines visual
    scoring (SceneScorer) with audio analysis (PANNs) and LLM content scoring.
    """
    from immich_memories.analysis.analyzer_factory import (
        create_unified_analyzer_from_config,
    )

    return create_unified_analyzer_from_config(config)
