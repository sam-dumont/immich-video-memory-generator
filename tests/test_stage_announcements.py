"""A stage can be announced from anywhere in a run, and a dead provider is named at once."""

from __future__ import annotations

import asyncio

from immich_memories.analysis.llm_wire import LLMTransportAttempt
from immich_memories.analysis.provider_status import watch_provider
from immich_memories.config_models_llm import LLMConfig
from immich_memories.operations.cut_progress import (
    StageUpdate,
    announce_stage,
    announcing_stages,
)


def test_an_announcement_reaches_the_attached_sink_and_nothing_else() -> None:
    seen: list[StageUpdate] = []
    announce_stage(StageUpdate("nobody listening"))
    with announcing_stages(seen.append):
        announce_stage(StageUpdate("kept"))
    announce_stage(StageUpdate("after"))
    assert [update.label for update in seen] == ["kept"]


def test_an_announcement_survives_the_sync_bridge_thread() -> None:
    """The gateway runs its coroutine on a helper thread when a loop is already running."""
    from immich_memories.analysis.editorial_gateway import _run_sync

    seen: list[StageUpdate] = []

    async def inner() -> str:
        announce_stage(StageUpdate("from the bridge"))
        return "ok"

    async def outer() -> None:
        with announcing_stages(seen.append):
            assert _run_sync(inner()) == "ok"

    asyncio.run(outer())
    assert [update.label for update in seen] == ["from the bridge"]


def test_the_first_dropped_connection_names_the_provider_and_the_retry() -> None:
    seen: list[StageUpdate] = []
    watch = watch_provider("reader", LLMConfig(base_url="http://omlx.local:9999/v1", model="m"))
    with announcing_stages(seen.append):
        watch(LLMTransportAttempt(1, "connection_error", None))
        watch(LLMTransportAttempt(2, "connection_error", None))
        watch(LLMTransportAttempt(3, "ok", 200))
    assert [update.label for update in seen] == [
        "Waiting for the reader at omlx.local:9999: connection dropped, retry 1 of 3",
        "Waiting for the reader at omlx.local:9999: connection dropped, retry 2 of 3",
    ]
    assert all(not update.counted for update in seen)


def test_a_counted_stage_says_what_it_is_doing_on_both_surfaces() -> None:
    update = StageUpdate("event evidence", done=2, total=5, verb="Reading")
    assert update.stage_label == "Reading event evidence: 2/5"
    restored = StageUpdate.from_record(update.as_record())
    assert restored is not None and restored.stage_label == update.stage_label
    assert StageUpdate("previews", "analysis", 1, 6).stage_label == "Preparing previews: 1/6"
