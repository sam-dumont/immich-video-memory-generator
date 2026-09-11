"""max_refinement_passes is the biggest warm-run cost dial; config has to reach it."""

from immich_memories.analysis.smart_pipeline import PipelineConfig
from immich_memories.config_loader import Config


def test_the_configured_pass_budget_reaches_the_pipeline() -> None:
    """Three refinement loops run up to this many times, each costing LLM calls.

    Anyone pointing llm.base_url at a paid API pays this multiplier, so YAML has
    to reach it rather than leaving the dataclass default fixed.
    """
    config = Config()
    config.analysis.max_refinement_passes = 3

    assert PipelineConfig.from_app_config(config).max_refinement_passes == 3


def test_the_default_pass_budget_is_unchanged() -> None:
    """A dial nobody sets must not change what anybody already gets."""
    assert PipelineConfig.from_app_config(Config()).max_refinement_passes == 10


def test_the_fast_preset_spends_fewer_passes() -> None:
    """preset: fast is the CPU-only/NAS profile — the cost dial belongs in it."""
    assert Config(preset="fast").analysis.max_refinement_passes == 3


def test_the_fast_preset_yields_to_an_explicit_setting() -> None:
    """Anything the user set explicitly outranks the preset, as with every other knob."""
    config = Config(preset="fast", analysis={"max_refinement_passes": 8})

    assert config.analysis.max_refinement_passes == 8
