"""Real CLI/UI parameter builders conserve the frozen editorial title policy."""

from unittest.mock import MagicMock, patch

import pytest

from immich_memories.processing.editorial_timing import (
    bind_editorial_timeline,
    prepare_certified_timeline,
    timing_policy_for_params,
)
from immich_memories.ui.state import AppState
from tests.test_editorial_source_route_surfaces import (
    _WINDOW,
    _config,
    _finished_selection,
    _source_pipeline,
)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr("socket.socket.connect", lambda *_a, **_k: pytest.fail("network work"))


def _binding(policy, result):
    assets = {clip.asset.id: clip.asset for clip in result.selected_clips}
    carriers = [{"asset_id": key, "seconds": 4.0} for key in assets]
    return bind_editorial_timeline(policy, policy.resolve(carriers, assets), list(assets))


def test_cli_actual_context_and_generation_reuse_exact_timing(tmp_path):
    from immich_memories.cli._pipeline_runner import run_pipeline_and_generate

    result = _finished_selection()
    pipeline = _source_pipeline(result)
    captured = {}

    def build(**kwargs):
        captured["context"] = kwargs["editorial_context"]
        return pipeline

    def plan(*_args, **_kwargs):
        result.stats["editorial_render_timing"] = _binding(
            captured["context"].render_timing, result
        )
        return result.selected_clips, result

    pipeline.run_editorial_source.side_effect = plan
    output = tmp_path / "memory.mp4"

    def render(params):
        assert params.target_duration_seconds == 60
        assert timing_policy_for_params(params) == captured["context"].render_timing
        prepare_certified_timeline(params)
        assert params.timeline_plan.transition_budget == 0
        assert params.editorial_render_timing is result.stats["editorial_render_timing"]
        return output

    # WHY: replaces the selection pipeline and render step so timing context can be checked.
    with (
        # WHY: the pipeline is a stand-in; its side effect captures the editorial context passed.
        patch("immich_memories.analysis.editorial_runtime.build_smart_pipeline", side_effect=build),
        # WHY: replaces the FFmpeg render; the side effect asserts on the params it receives.
        patch("immich_memories.generate.generate_memory", side_effect=render) as generated,
    ):
        run_pipeline_and_generate(
            assets=[result.selected_clips[0].asset, result.selected_clips[2].asset],
            photo_assets=[result.selected_clips[1].asset],
            include_photos=True,
            client=MagicMock(),
            config=_config(tmp_path),
            progress=MagicMock(),
            duration=60,
            transition="cut",
            music=None,
            no_music=True,
            output_path=output,
            memory_type="monthly_highlights",
            person_names=[],
            date_range=_WINDOW,
            upload_to_immich=False,
            album=None,
        )
    generated.assert_called_once()


def test_ui_actual_context_generation_and_later_setting_change(tmp_path):
    from immich_memories.ui.pages._step4_generate import _build_generation_params
    from immich_memories.ui.pages.clip_pipeline import _build_ui_editorial_context

    result = _finished_selection()
    config = _config(tmp_path)
    config.defaults.transition_duration = 0.8
    state = AppState(
        config=config,
        memory_type="monthly_highlights",
        date_ranges=[_WINDOW],
        target_duration=1.0,
        clips=result.selected_clips,
        pipeline_selected_clips=result.selected_clips,
        editorial_selections=result.editorial_selections,
        generation_options={"transition": "Cut"},
        clip_segments=result.clip_segments,
    )
    context = _build_ui_editorial_context(state, config, state.clips, [])
    state.editorial_render_timing = _binding(context.render_timing, result)
    # WHY: avoids opening a real Immich connection; the test only checks the handed-off params.
    with patch("immich_memories.api.immich.SyncImmichClient"):
        params = _build_generation_params(state, result.selected_clips, tmp_path / "memory.mp4")
        assert params.transition_duration == 0.8
        assert context.render_timing == timing_policy_for_params(params)
        prepare_certified_timeline(params)
        state.generation_options["transition"] = "Crossfade"
        changed = _build_generation_params(state, result.selected_clips, tmp_path / "other.mp4")
        assert changed.editorial_owner_edits["timing_policy_changed"] is True
        assert changed.editorial_render_timing is not state.editorial_render_timing
        prepare_certified_timeline(changed)
        changed.transition = "cut"
        with pytest.raises(ValueError, match="timing settings changed"):
            prepare_certified_timeline(changed)  # Unrecorded mutation still fails.
        state.editorial_render_timing = None
        legacy = _build_generation_params(state, result.selected_clips, tmp_path / "legacy.mp4")
        assert legacy.transition_duration == 0.5  # Existing uncertified UI behavior.
