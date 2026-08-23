"""max_refinement_passes is the biggest warm-run cost dial; it needs a knob."""

from immich_memories.analysis.smart_pipeline import PipelineConfig
from immich_memories.config_loader import Config


def test_the_configured_pass_budget_reaches_the_pipeline() -> None:
    """Three refinement loops run up to this many times, each costing LLM calls.

    Anyone pointing llm.base_url at a paid API pays this multiplier, so it has
    to be settable rather than fixed at the dataclass default.
    """
    config = Config()
    config.analysis.max_refinement_passes = 3

    assert PipelineConfig.from_app_config(config).max_refinement_passes == 3


def test_the_default_pass_budget_is_unchanged() -> None:
    """A dial nobody sets must not change what anybody already gets."""
    assert PipelineConfig.from_app_config(Config()).max_refinement_passes == 10


def _generate_with(args: list[str], config: Config):
    """Run the CLI down to the point of generation, replacing only live boundaries."""
    from pathlib import Path
    from unittest.mock import MagicMock, patch

    from click.testing import CliRunner

    from immich_memories.cli import main

    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    client.get_photos_for_date_range.return_value = []
    asset = MagicMock(duration_seconds=10.0)
    # WHY: the CLI otherwise reads Immich and creates the real ~/.immich-memories config dir.
    with (
        # WHY: Immich is the external boundary — no live server in a unit test.
        patch("immich_memories.api.immich.SyncImmichClient", return_value=client),
        patch(
            "immich_memories.cli.generate.fetch_videos_and_live_photos",
            return_value=([asset], []),
        ),
        # WHY: generation is the boundary past the flag resolution under test.
        patch(
            "immich_memories.cli.generate.run_pipeline_and_generate",
            return_value=(Path("plan.mp4"), False, None),
        ),
        patch("immich_memories.cli.init_config_dir"),
        patch("immich_memories.cli.get_config", return_value=config),
    ):
        return CliRunner().invoke(main, args, catch_exceptions=False)


def test_the_flag_overrides_the_configured_pass_budget() -> None:
    """A per-run cost dial is worth a flag; the YAML value is the default, not the law."""
    config = Config()
    config.immich.url = "http://immich:2283"
    config.immich.api_key = "test-key"
    config.analysis.max_refinement_passes = 10

    result = _generate_with(
        [
            "generate",
            "--memory-type",
            "monthly_highlights",
            "--month",
            "7",
            "--year",
            "2024",
            "--refinement-passes",
            "3",
            "--dry-run",
        ],
        config,
    )

    assert result.exit_code == 0, result.output
    assert config.analysis.max_refinement_passes == 3


def test_the_fast_preset_spends_fewer_passes() -> None:
    """preset: fast is the CPU-only/NAS profile — the cost dial belongs in it."""
    assert Config(preset="fast").analysis.max_refinement_passes == 3


def test_the_fast_preset_yields_to_an_explicit_setting() -> None:
    """Anything the user set explicitly outranks the preset, as with every other knob."""
    config = Config(preset="fast", analysis={"max_refinement_passes": 8})

    assert config.analysis.max_refinement_passes == 8
