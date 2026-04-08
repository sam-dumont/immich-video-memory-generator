"""Tests for scoring factory — creates UnifiedSegmentAnalyzer from config."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from immich_memories.analysis.analyzer_factory import create_analyzer_from_config
from immich_memories.config_loader import Config


class TestCreateAnalyzerFromConfig:
    def test_returns_unified_segment_analyzer(self):
        config = Config()
        analyzer = create_analyzer_from_config(config)
        from immich_memories.analysis.unified_analyzer import UnifiedSegmentAnalyzer

        assert isinstance(analyzer, UnifiedSegmentAnalyzer)

    def test_inner_scorer_exists(self):
        config = Config()
        analyzer = create_analyzer_from_config(config)
        assert analyzer.scorer is not None

    def test_duration_params_from_config(self):
        config = Config()
        config.analysis.optimal_clip_duration = 7.0
        config.analysis.max_optimal_duration = 12.0
        analyzer = create_analyzer_from_config(config)
        assert analyzer.optimal_clip_duration == 7.0
        assert analyzer.max_optimal_duration == 12.0

    def test_content_analysis_disabled_by_default(self):
        config = Config()
        analyzer = create_analyzer_from_config(config)
        assert analyzer.content_analyzer is None
        assert analyzer.content_weight == 0.0

    def test_audio_content_config_passed(self):
        config = Config()
        analyzer = create_analyzer_from_config(config)
        assert not analyzer.audio_content_enabled

    def test_content_analysis_enabled_creates_analyzer(self):
        config = Config()
        config.content_analysis.enabled = True

        # WHY: mock to avoid real LLM initialization
        mock_analyzer = MagicMock()
        with patch(
            "immich_memories.analysis.content_analyzer.get_content_analyzer",
            return_value=mock_analyzer,
        ):
            analyzer = create_analyzer_from_config(config)

        assert analyzer.content_analyzer is mock_analyzer
        assert analyzer.content_weight == config.content_analysis.weight

    def test_content_analysis_enabled_but_no_analyzer(self):
        config = Config()
        config.content_analysis.enabled = True

        # WHY: mock returns None to simulate no analyzer available
        with patch(
            "immich_memories.analysis.content_analyzer.get_content_analyzer",
            return_value=None,
        ):
            analyzer = create_analyzer_from_config(config)

        assert analyzer.content_analyzer is None
        assert analyzer.content_weight == 0.0
