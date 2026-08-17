"""Configured values must reach the analyzer the pipeline actually builds.

`ClipAnalyzer` constructed `UnifiedSegmentAnalyzer` directly with its own list of
keyword arguments, while `create_unified_analyzer_from_config` -- which passes
the full set -- had no caller outside tests. Five settings therefore had no
effect in production, including the `speech:` block documented as controlling
boundary placement, and three of the five fields that distinguish the
`clip_style` presets from one another.

No mocks: the collaborators passed as None are genuinely untouched by analyzer
construction, so this exercises the real production path.
"""

from __future__ import annotations

import pytest

from immich_memories.analysis.clip_analyzer import ClipAnalyzer
from immich_memories.analysis.smart_pipeline import PipelineConfig
from immich_memories.config_loader import Config


@pytest.fixture
def analyzer_from_config():
    def build(**overrides):
        config = Config()
        config.speech.vad_threshold = 0.55
        config.speech.min_silence_ms = 400
        config.analysis.min_silence_duration = 0.2
        config.analysis.optimal_clip_duration = 7.0
        config.analysis.max_optimal_duration = 15.0
        config.analysis.target_extraction_ratio = 0.25
        for key, value in overrides.items():
            setattr(config.analysis, key, value)
        clip_analyzer = ClipAnalyzer(
            config=PipelineConfig(),
            client=None,
            analysis_cache=None,
            preview_builder=None,
            app_config=config,
        )
        return clip_analyzer._get_unified_analyzer()

    return build


def test_speech_config_reaches_the_production_analyzer(analyzer_from_config):
    """`speech.vad_threshold` and `min_silence_ms` were silently replaced by defaults."""
    analyzer = analyzer_from_config()

    speech_config = analyzer._speech_analysis.speech_config
    assert speech_config.vad_threshold == 0.55
    assert speech_config.min_silence_ms == 400


def test_duration_settings_reach_the_production_analyzer(analyzer_from_config):
    """Three of the five fields behind `clip_style` never arrived.

    Without them `fast-cuts` and `long-cuts` differed from `balanced` only in
    their min/max segment bounds -- the duration curve peaked in the same place
    for every style.
    """
    analyzer = analyzer_from_config()

    assert analyzer.optimal_clip_duration == 7.0
    assert analyzer.max_optimal_duration == 15.0
    assert analyzer.target_extraction_ratio == 0.25
    assert analyzer.min_silence_duration == 0.2
