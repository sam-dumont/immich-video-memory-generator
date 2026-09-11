"""Explicit southern seasons reach the same factual editorial brief on each surface.

The probe matrix script inspection stays on the probe branch.
"""

import hashlib
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from immich_memories.analysis.editorial_people import adapt_editorial_people
from immich_memories.analysis.editorial_product_brief import build_editorial_brief
from immich_memories.analysis.editorial_runtime import EditorialRunContext
from immich_memories.analysis.editorial_runtime_backend import ProductionPostCardBackend
from immich_memories.analysis.editorial_runtime_ports import EditorialRuntimePorts
from immich_memories.analysis.selection_trace import Trace
from immich_memories.config_loader import Config
from immich_memories.memory_types.date_builders import build_season
from immich_memories.ui.state import AppState
from tests.conftest import make_clip


class _Captured(RuntimeError):
    pass


def _runtime_brief(context, config):
    captured = {}

    def capture(_workprint, **kwargs):
        captured["case"] = kwargs["case"]
        raise _Captured

    backend = ProductionPostCardBackend(
        config=config,
        context=context,
        people=adapt_editorial_people({}),
        thumbnail_cache=object(),
        store_path=context.artifact_dir / "unused.sqlite",
        ports=EditorialRuntimePorts(),
    )
    # WHY: capture_structure_input feeds the LLM prompt builder; intercepted to read its case arg
    with (
        # WHY: side_effect records the case kwarg then raises, skipping the real prompt/LLM call
        patch(
            "immich_memories.analysis.editorial_runtime_backend.capture_structure_input",
            side_effect=capture,
        ),
        pytest.raises(_Captured),
    ):
        backend.edit(object(), trace=Trace())
    return captured["case"].brief


def test_northern_season_keeps_its_entire_evaluated_brief_byte_exact():
    brief = build_editorial_brief("season", ())
    # Recorded from the parent commit before adding the hemisphere parameter.
    assert (
        hashlib.sha256(brief.encode()).hexdigest()
        == "341e08411bb127a2a12d84bc178b5b09be6ddb9544915aa5c43a87ffb5311469"
    )
    assert build_editorial_brief("season", (), hemisphere="north") == brief


def test_southern_season_states_the_corresponding_months_only():
    north = build_editorial_brief("season", (), hemisphere="north")
    south = build_editorial_brief("season", (), hemisphere="south")
    assert south == north.replace(
        "NORTHERN hemisphere: December is deep winter and June-August is summer",
        "SOUTHERN hemisphere: December is summer and June-August is winter",
    )
    assert "SOUTHERN hemisphere" in south


@pytest.mark.parametrize(
    "product",
    [
        "monthly_highlights",
        "trip",
        "special_day",
        "album",
        "holiday",
        "person_spotlight",
        "multi_person",
        "year_in_review",
        "on_this_day",
        "custom",
        "then_and_now",
    ],
)
def test_hemisphere_does_not_change_any_other_product_question(product):
    assert build_editorial_brief(product, (), hemisphere="south") == build_editorial_brief(
        product, ()
    )


@pytest.mark.parametrize("hemisphere", ["northern", "SOUTH", "", None])
def test_context_and_brief_reject_unrecognized_hemisphere(tmp_path, hemisphere):
    with pytest.raises(ValueError, match="hemisphere must be north or south"):
        build_editorial_brief("season", (), hemisphere=hemisphere)
    with pytest.raises(ValueError, match="hemisphere must be north or south"):
        EditorialRunContext(
            "season",
            "Summer",
            "season",
            (build_season("summer", 2024),),
            60,
            tmp_path,
            hemisphere=hemisphere,
        )


def test_actual_cli_hemisphere_flag_survives_the_generation_handoff(tmp_path):
    from immich_memories.cli import main

    config = Config(immich={"url": "http://immich.test", "api_key": "test-key"})
    client = MagicMock()
    client.__enter__.return_value = client
    client.get_photos_for_date_range.return_value = []
    clip = make_clip("source", duration=5.0)
    # WHY: exercises the full CLI generate command except these five external calls
    with (
        # WHY: init_config_dir would create real config directories on disk; skipped here
        patch("immich_memories.cli.init_config_dir"),
        # WHY: get_config would load the user's real config file; replaced with the test Config
        patch("immich_memories.cli.get_config", return_value=config),
        # WHY: SyncImmichClient is the Immich HTTP client; replaced so no server connection is made
        patch("immich_memories.api.immich.SyncImmichClient", return_value=client),
        # WHY: fetch_videos would call Immich for real assets; stubbed to return the test clip
        patch("immich_memories.cli.generate.fetch_videos", return_value=[clip.asset]),
        # WHY: run_pipeline_and_generate is the FFmpeg render entrypoint; captured, not executed
        patch(
            "immich_memories.cli.generate.run_pipeline_and_generate",
            return_value=(tmp_path / "memory.mp4", False, None),
        ) as generate,
    ):
        result = CliRunner().invoke(
            main,
            [
                "generate",
                "--memory-type",
                "season",
                "--season",
                "summer",
                "--year",
                "2024",
                "--hemisphere",
                "south",
                "--no-render",
                "--no-music",
                "--output",
                str(tmp_path / "memory.mp4"),
            ],
        )
    assert result.exit_code == 0, result.output
    assert generate.call_args.kwargs["memory_preset_params"]["hemisphere"] == "south"
    assert generate.call_args.kwargs["date_range"] == build_season("summer", 2024, "south")


@pytest.mark.parametrize("hemisphere", ["north", "south"])
def test_cli_and_ui_contexts_feed_the_same_runtime_season_statement(tmp_path, hemisphere):
    from immich_memories.cli._pipeline_runner import run_pipeline_and_generate
    from immich_memories.ui.pages.clip_pipeline import _build_ui_editorial_context

    config = Config(
        cache={"directory": str(tmp_path / "cache"), "database": str(tmp_path / "analysis.db")}
    )
    window = build_season("summer", 2024, hemisphere)
    clip = make_clip("source", duration=5.0, file_created_at=window.start)
    pipeline = MagicMock()
    pipeline.run_editorial_source.side_effect = _Captured
    # WHY: run_editorial_source raises _Captured so run_pipeline_and_generate exits early
    with (
        # WHY: build_smart_pipeline builds the real selection pipeline; stubbed to capture context
        patch(
            "immich_memories.analysis.editorial_runtime.build_smart_pipeline", return_value=pipeline
        ) as build,
        pytest.raises(_Captured),
    ):
        run_pipeline_and_generate(
            assets=[clip.asset],
            client=MagicMock(),
            config=config,
            progress=MagicMock(),
            duration=60,
            transition="cut",
            music=None,
            output_path=tmp_path / "memory.mp4",
            memory_type="season",
            person_names=[],
            date_range=window,
            memory_preset_params={"hemisphere": hemisphere},
            upload_to_immich=False,
            album=None,
            no_render=True,
        )
    cli_context = build.call_args.kwargs["editorial_context"]
    state = AppState(
        config=config,
        memory_type="season",
        date_ranges=[window],
        clips=[clip],
        target_duration=1.0,
        memory_preset_params={"hemisphere": hemisphere},
    )
    ui_context = _build_ui_editorial_context(state, config, [clip], [])
    assert cli_context.hemisphere == ui_context.hemisphere == hemisphere
    assert (
        _runtime_brief(cli_context, config)
        == _runtime_brief(ui_context, config)
        == build_editorial_brief(
            "season",
            (window,),
            hemisphere=hemisphere,
        )
    )
