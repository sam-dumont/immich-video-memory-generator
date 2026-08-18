"""Bumping ANALYSIS_VERSION must actually invalidate the analyzer's cache."""

from __future__ import annotations

from datetime import datetime

from immich_memories.analysis.cache_projection import is_compatible_analysis_cache
from immich_memories.cache.database_models import CachedSegment, CachedVideoAnalysis
from immich_memories.cache.versions import ANALYSIS_VERSION
from immich_memories.config_loader import Config


def _cached(analysis_version: int) -> CachedVideoAnalysis:
    return CachedVideoAnalysis(
        asset_id="asset-1",
        checksum=None,
        file_modified_at=None,
        analysis_timestamp=datetime(2026, 6, 1),
        analysis_version=analysis_version,
        model_version="a-model",
        segments=[CachedSegment(segment_index=0, start_time=0.0, end_time=3.0)],
    )


def test_analysis_from_an_older_generation_is_not_reused() -> None:
    """needs_reanalysis() has always compared analysis_version, but the analyzer's
    own cache check did not, so bumping the constant invalidated nothing on the
    path that actually runs — new analysis fields silently stayed empty."""
    config = Config()
    config.content_analysis.enabled = True
    config.llm.model = "a-model"

    assert not is_compatible_analysis_cache(_cached(ANALYSIS_VERSION - 1), config)
    assert is_compatible_analysis_cache(_cached(ANALYSIS_VERSION), config)
