"""Dry-run prepares inputs; no-render runs the sole production selector."""

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from immich_memories.config_loader import Config
from immich_memories.timeperiod import DateRange


def _run(**kwargs):
    from immich_memories.cli._pipeline_runner import run_pipeline_and_generate

    clip = MagicMock()
    clip.asset.id = "asset-1"
    clip.width, clip.height = 1920, 1080
    config = Config()

    # WHY: Immich is the external boundary — no live server in a unit test.
    with (
        patch("immich_memories.generate.assets_to_clips", return_value=[clip]),
        # WHY: the pipeline is the collaborator under inspection.
        patch("immich_memories.analysis.editorial_runtime.build_smart_pipeline") as pipeline_type,
    ):
        pipeline = pipeline_type.return_value
        pipeline.run_analysis.return_value = []
        pipeline.run_planning_analysis.return_value = []
        # Stop at the call under inspection: everything past it is rendering,
        # which is the whole thing this flag exists to avoid.
        pipeline.run_editorial_source.side_effect = RuntimeError("stop at selection")
        try:
            run_pipeline_and_generate(
                assets=[clip.asset],
                client=MagicMock(),
                config=config,
                progress=MagicMock(),
                duration=60,
                transition="cut",
                music=None,
                output_path=Path("/tmp/never-written.mp4"),
                memory_type="trip",
                person_names=[],
                date_range=DateRange(start=datetime(2026, 1, 1), end=datetime(2026, 1, 2)),
                upload_to_immich=False,
                album=None,
                source="auto",
                **kwargs,
            )
        except RuntimeError as exc:
            if "stop at selection" not in str(exc):
                raise
        if kwargs.get("dry_run"):
            pipeline_type.assert_not_called()
        return pipeline


def test_no_render_runs_the_real_selection() -> None:
    """The production source route owns all selection and media checks."""
    pipeline = _run(no_render=True)

    pipeline.run_editorial_source.assert_called_once()
    pipeline.run_selection.assert_not_called()
    pipeline.run_analysis.assert_not_called()
    pipeline.run_planning_analysis.assert_not_called()


def test_dry_run_prepares_without_an_alternate_selection(capsys) -> None:
    pipeline = _run(dry_run=True)

    pipeline.run_editorial_source.assert_not_called()
    pipeline.run_selection.assert_not_called()
    pipeline.run_planning_analysis.assert_not_called()
    assert "Selection: pending" in capsys.readouterr().out


def test_dry_run_labels_auto_canvas_as_provisional(capsys) -> None:
    _run(dry_run=True, output_orientation="auto")
    assert "provisional until selection" in capsys.readouterr().out


def test_the_flag_exists_and_says_what_it_does() -> None:
    """A flag nobody can find is a flag nobody uses."""
    from click.testing import CliRunner

    from immich_memories.cli import main

    # WHY: config-dir creation is a filesystem boundary; --help must not touch it.
    with patch("immich_memories.cli.init_config_dir"):
        help_text = CliRunner().invoke(main, ["generate", "--help"]).output

    assert "--no-render" in help_text


def test_no_render_is_not_dry_run() -> None:
    """The two must not be the same switch wearing two names.

    Dry-run never enters selection. No-render selects exactly what would ship.
    """
    real = _run(no_render=True)
    cheap = _run(dry_run=True)

    real.run_editorial_source.assert_called_once()
    cheap.run_editorial_source.assert_not_called()
    cheap.run_planning_analysis.assert_not_called()
