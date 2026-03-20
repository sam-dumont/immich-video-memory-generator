"""Tests for scoring factory — creates SceneScorer from config."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from immich_memories.analysis.scoring_factory import create_scorer_from_config
from immich_memories.config_loader import Config


class TestCreateScorerFromConfig:
    def test_default_config_no_content_analysis(self):
        config = Config()
        scorer = create_scorer_from_config(config)

        # Base weights without content analysis
        assert scorer.face_weight == 0.35
        assert scorer.motion_weight == 0.20
        assert scorer.stability_weight == 0.15
        assert scorer.audio_weight == 0.15
        assert scorer.duration_weight == 0.15
        assert scorer.content_weight == 0.0

    def test_duration_params_from_config(self):
        config = Config()
        scorer = create_scorer_from_config(config)

        assert scorer._optimal_duration == config.analysis.optimal_clip_duration
        assert scorer._max_optimal_duration == config.analysis.max_optimal_duration
        assert scorer._target_extraction_ratio == config.analysis.target_extraction_ratio
        assert scorer._min_duration == config.analysis.min_segment_duration

    def test_content_analysis_enabled_scales_weights(self):
        config = Config()
        config.content_analysis.enabled = True
        content_weight = config.content_analysis.weight  # default 0.35

        # WHY: mock the content analyzer to avoid real LLM initialization
        mock_analyzer = MagicMock()
        with patch(
            "immich_memories.analysis.content_analyzer.get_content_analyzer",
            return_value=mock_analyzer,
        ):
            scorer = create_scorer_from_config(config)

        # Weights should be scaled down by (1 - content_weight)
        scale = 1 - content_weight
        assert scorer.content_weight == content_weight
        assert abs(scorer.face_weight - 0.35 * scale) < 1e-9
        assert abs(scorer.motion_weight - 0.20 * scale) < 1e-9

    def test_content_analysis_enabled_but_no_analyzer(self):
        config = Config()
        config.content_analysis.enabled = True

        # WHY: mock returns None to simulate no analyzer available
        with patch(
            "immich_memories.analysis.content_analyzer.get_content_analyzer",
            return_value=None,
        ):
            scorer = create_scorer_from_config(config)

        # Should fall back to base weights (no content weight)
        assert scorer.content_weight == 0.0
        assert scorer.face_weight == 0.35

    def test_explicit_config_required(self):
        """create_scorer_from_config requires a Config object (no fallback)."""
        config = Config()
        scorer = create_scorer_from_config(config)
        assert scorer.face_weight == 0.35
