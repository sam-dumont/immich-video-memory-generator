"""Running the real selection without producing a video.

--dry-run cannot do this. It welds three separate decisions together: use only
cached analysis, skip the verify pass, and stop before rendering. So the cheap
mode runs a *different* selection than the real one, and there was no way to
exercise the real one without also spending minutes encoding an mp4 nobody
wanted.
"""

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
        patch("immich_memories.analysis.smart_pipeline.SmartPipeline") as pipeline_type,
    ):
        pipeline = pipeline_type.return_value
        pipeline.run_analysis.return_value = []
        pipeline.run_planning_analysis.return_value = []
        # Stop at the call under inspection: everything past it is rendering,
        # which is the whole thing this flag exists to avoid.
        pipeline.run_selection.side_effect = RuntimeError("stop at selection")
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
        return pipeline


def test_no_render_runs_the_real_selection() -> None:
    """The verify pass is what makes it the real one, so it has to stay on."""
    pipeline = _run(no_render=True)

    assert pipeline.run_selection.call_args.kwargs["verify"] is True
    pipeline.run_analysis.assert_called()
    pipeline.run_planning_analysis.assert_not_called()


def test_dry_run_still_means_the_cheap_plan() -> None:
    """Unchanged: --dry-run stays the cached-only, unverified preview."""
    pipeline = _run(dry_run=True)

    assert pipeline.run_selection.call_args.kwargs["verify"] is False
    pipeline.run_planning_analysis.assert_called()


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

    --dry-run uses cached analysis only and skips verify; --no-render runs
    both for real. If they ever collapse into one, the cheap preview silently
    becomes the expensive path or the real one silently becomes approximate.
    """
    real = _run(no_render=True)
    cheap = _run(dry_run=True)

    assert real.run_selection.call_args.kwargs["verify"] is True
    assert cheap.run_selection.call_args.kwargs["verify"] is False
    real.run_analysis.assert_called()
    cheap.run_planning_analysis.assert_called()
