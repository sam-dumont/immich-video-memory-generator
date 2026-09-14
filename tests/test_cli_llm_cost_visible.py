"""A hosted run has to be able to say what it cost, and be right about it.

The defect this file exists for: a complete hosted run made 93 paid calls to
api.melious.ai and its end-of-run block printed no `LLM` line at all, so every
cell of the setup matrix published a null cost. Nothing was collecting at the
CLI, and the one scope that was -- `plan_structure`'s -- replaced rather than
joined it, so the 90 calls selection made were invisible outside the plan file.

The transport is faked and everything between it and the printed line is real:
`query_llm` parses the reply, `record_reply` counts it through the same
contextvar the run holds, and the count crosses the same async bridge the
editorial stages cross.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from immich_memories.analysis.llm_usage_record import USAGE_FILE
from immich_memories.config_models_llm import LLMConfig
from tests.test_editorial_source_route_surfaces import (
    _WINDOW,
    _config,
    _finished_selection,
    _source_pipeline,
)

_READER = LLMConfig(provider="openai-compatible", base_url="https://host/v1", model="glm-5.3-flash")


def _reply(prompt_tokens: int, completion_tokens: int, reasoning_tokens: int = 0):
    # WHY: the hosted provider is the external boundary; every reply below is one
    # HTTP POST that a real run would have been billed for.
    response = MagicMock()
    response.status_code = 200
    response.raise_for_status = lambda: None
    response.json = MagicMock(
        return_value={
            "model": "glm-5.3-flash",
            "choices": [{"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "completion_tokens_details": {"reasoning_tokens": reasoning_tokens},
            },
        }
    )
    return response


def _ask(question: str) -> None:
    """One reader question, resolved the way every editorial stage resolves one."""
    from immich_memories.analysis.editorial_async_bridge import _run_sync
    from immich_memories.analysis.llm_query import query_llm

    _run_sync(query_llm(question, _READER))


def _run_the_cli(tmp_path, attempt_dir, capsys):
    """Drive the real CLI runner over a reader that answers like a hosted one."""
    from immich_memories.analysis import llm_metrics
    from immich_memories.cli._pipeline_runner import run_pipeline_and_generate

    result = _finished_selection()
    result.stats["editorial_attempt_directory"] = str(attempt_dir)
    pipeline = _source_pipeline(result)

    def select(*_args, **_kwargs):
        # The preparation stage: two episode reads and the period account, all
        # of them before the planner opens a scope of its own.
        for _ in range(3):
            _ask("Read this event")
        # `plan_structure`: measures its own spend so the plan record can report it.
        with llm_metrics.collecting() as planner:
            for _ in range(4):
                _ask("Pick the carriers")
        assert planner.calls == 4
        return result.selected_clips, result

    pipeline.run_editorial_source.side_effect = select
    output = tmp_path / "memory.mp4"

    def render(_params):
        _ask("Title this")  # generation asks too, and it is on the same bill
        return output

    with (
        # WHY: stands in for the editorial pipeline so the reader path is the only
        # thing under test; its side effect drives the real query/record path.
        patch(
            "immich_memories.analysis.editorial_runtime.build_smart_pipeline", return_value=pipeline
        ),
        # WHY: replaces the FFmpeg render, which has nothing to do with the bill.
        patch("immich_memories.generate.generate_memory", side_effect=render),
        # WHY: the hosted provider itself. Eight replies, eight paid calls.
        patch(
            "httpx.AsyncClient.post",
            side_effect=[_reply(5400, 13800, 13469), *(_reply(1200, 90) for _ in range(7))],
        ),
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
    return capsys.readouterr().out


def test_the_run_block_reports_every_call_the_run_paid_for(tmp_path, capsys) -> None:
    """Eight POSTs went out: three in preparation, four in selection, one in generation."""
    attempt_dir = tmp_path / "attempt"
    attempt_dir.mkdir()

    printed = _run_the_cli(tmp_path, attempt_dir, capsys)

    assert "LLM" in printed
    assert "8 calls" in printed


def test_the_usage_record_holds_the_exact_tokens_the_provider_reported(tmp_path, capsys) -> None:
    """`13.9k prompt` is what a person reads; a bill needs 13,800."""
    attempt_dir = tmp_path / "attempt"
    attempt_dir.mkdir()

    _run_the_cli(tmp_path, attempt_dir, capsys)
    record = json.loads((attempt_dir / USAGE_FILE).read_text())

    assert record["calls"] == 8
    assert record["prompt_tokens"] == 5400 + 7 * 1200
    assert record["completion_tokens"] == 13800 + 7 * 90
    assert record["reasoning_tokens"] == 13469
    assert record["by_model"]["glm-5.3-flash"]["calls"] == 8
