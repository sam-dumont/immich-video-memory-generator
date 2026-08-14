"""Behavior tests for the local audio validation utility."""

from scripts.validate_local_audio import build_acestep_config


def test_high_quality_profile_uses_xl_executor_and_4b_planner():
    """The production validation profile must exercise both 4B components."""
    config = build_acestep_config("high")

    assert config.mode == "lib"
    assert config.model_variant == "acestep-v15-xl-turbo"
    assert config.lm_model_size == "4B"
    assert config.use_lm is True
