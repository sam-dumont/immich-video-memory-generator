"""Config defaults and analyzer constructor defaults must agree.

They silently diverged while `ClipAnalyzer` was omitting five arguments: the
constructor defaults were what actually ran, the config defaults were what the
documentation described, and nobody could tell which was in force. Wiring the
config through changed real behaviour purely because the two lists disagreed.

Now that config always reaches the analyzer the constructor default is only a
fallback, but a disagreement between them still means the documented value and
the running value differ for anyone constructing directly. Pinning them together
is what stops this recurring.
"""

from __future__ import annotations

import inspect

import pytest

from immich_memories.analysis.unified_analyzer import UnifiedSegmentAnalyzer
from immich_memories.config_models_analysis import AnalysisConfig

SHARED_PARAMETERS = [
    "min_segment_duration",
    "max_segment_duration",
    "silence_threshold_db",
    "min_silence_duration",
    "cut_point_merge_tolerance",
    "optimal_clip_duration",
    "max_optimal_duration",
    "target_extraction_ratio",
]


@pytest.mark.parametrize("name", SHARED_PARAMETERS)
def test_config_default_matches_constructor_default(name: str):
    constructor_default = (
        inspect.signature(UnifiedSegmentAnalyzer.__init__).parameters[name].default
    )

    assert getattr(AnalysisConfig(), name) == constructor_default, (
        f"{name}: config says {getattr(AnalysisConfig(), name)}, "
        f"constructor says {constructor_default} -- one of them is not what runs"
    )
