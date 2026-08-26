"""The visual gateway banks exact contact-sheet evidence."""

from hashlib import sha256
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from immich_memories.analysis.contact_sheets import ContactSheetPage, TileRef
from immich_memories.analysis.selection_trace import Trace
from immich_memories.analysis.visual_request_planner import VisionRequestLimits
from immich_memories.config_models_llm import LLMConfig


def _page() -> ContactSheetPage:
    jpeg = b"exact sheet jpeg"
    return ContactSheetPage(
        sheet_id="episode-001",
        path=Path("/private-sheet.jpg"),
        jpeg_bytes=jpeg,
        sha256=sha256(jpeg).hexdigest(),
        tile_refs=(TileRef(1, "asset-a"), TileRef(2, "asset-b")),
        layout_version="layout-1",
    )


def _request(page: ContactSheetPage):
    from immich_memories.analysis.editorial_gateway import VisualEditorialRequest

    return VisualEditorialRequest(
        pass_name="cull",  # noqa: S106 - test-only pass identity
        pass_version="pass-1",  # noqa: S106 - test-only pass identity
        prompt="name only clear failures",
        prompt_version="prompt-1",
        schema_version="schema-1",
        pages=(page,),
        ordered_input_ids=("asset-a", "asset-b"),
        ordered_group_ids=("episode",),
        grounded_annotations=("known place",),
        upstream_material=("insight-1",),
        render_version="render-1",
        limits=VisionRequestLimits(),
    )


def test_gateway_attaches_exact_page_bytes_and_traces_the_same_hash(tmp_path) -> None:
    from immich_memories.analysis.editorial_gateway import VisualEditorialGateway

    page = _page()
    captured: dict[str, object] = {}

    async def _answer(prompt, config, **kwargs):
        from immich_memories.analysis.llm_query import LLMTransportAttempt

        captured["images"] = kwargs["images"]
        kwargs["transport_observer"](LLMTransportAttempt(1, "response", 200))
        return '{"rejected": []}'

    trace = Trace()
    gateway = VisualEditorialGateway(
        llm_config=LLMConfig(model="vision-test"), cache_path=tmp_path / "judgments.db", trace=trace
    )

    # WHY: query_llm is the sole external provider transport this gateway may use.
    with patch("immich_memories.analysis.editorial_gateway.query_llm", new=_answer):
        answer = gateway.ask(_request(page))

    assert sha256(captured["images"][0]).hexdigest() == page.sha256
    assert answer.raw_text == '{"rejected": []}'
    request_trace = trace.as_dict()["requests"][0]
    assert request_trace["attached_sheet_hashes"] == [page.sha256]
    assert request_trace["tile_count"] == 2
    assert request_trace["actual_calls"] == 1


def test_gateway_sends_the_exact_grounded_annotations_used_by_cache_identity(tmp_path) -> None:
    """Visible provider evidence and cache evidence cannot silently diverge."""
    from dataclasses import replace

    from immich_memories.analysis.editorial_gateway import VisualEditorialGateway
    from immich_memories.analysis.llm_query import LLMTransportAttempt

    prompts: list[str] = []

    async def _answer(prompt, _config, **kwargs):
        prompts.append(prompt)
        kwargs["transport_observer"](LLMTransportAttempt(1, "response", 200))
        return '{"rejected":[]}'

    first_request = replace(
        _request(_page()),
        grounded_annotations=(
            "tile:1 | episode:1 | taken:2026-08-25T12:00:00+00:00 | media:photo | "
            "favourite:true | source:camera",
            "tile:2 | episode:1 | taken:2026-08-25T12:01:00+00:00 | media:video | "
            "favourite:false | source:motion",
        ),
    )
    changed_request = replace(
        first_request,
        grounded_annotations=(
            first_request.grounded_annotations[0].replace("favourite:true", "favourite:false"),
            first_request.grounded_annotations[1],
        ),
    )
    gateway = VisualEditorialGateway(
        llm_config=LLMConfig(model="vision-test"),
        cache_path=tmp_path / "judgments.db",
        trace=Trace(),
    )

    # WHY: query_llm is the sole provider boundary; cache and request composition stay real.
    with patch("immich_memories.analysis.editorial_gateway.query_llm", new=_answer):
        first = gateway.ask(first_request)
        changed = gateway.ask(changed_request)

    assert prompts == [
        "name only clear failures\n\nGrounded annotations (ordered JSON):\n"
        '["tile:1 | episode:1 | taken:2026-08-25T12:00:00+00:00 | media:photo | '
        'favourite:true | source:camera","tile:2 | episode:1 | '
        'taken:2026-08-25T12:01:00+00:00 | media:video | favourite:false | source:motion"]',
        "name only clear failures\n\nGrounded annotations (ordered JSON):\n"
        '["tile:1 | episode:1 | taken:2026-08-25T12:00:00+00:00 | media:photo | '
        'favourite:false | source:camera","tile:2 | episode:1 | '
        'taken:2026-08-25T12:01:00+00:00 | media:video | favourite:false | source:motion"]',
    ]
    assert first.provenance.request_key != changed.provenance.request_key


def test_gateway_returns_its_physical_trace_and_uses_explicit_request_budget(tmp_path) -> None:
    """A fused logical consumer can reuse one answer without recounting its wire call."""
    from dataclasses import replace

    from immich_memories.analysis.editorial_gateway import VisualEditorialGateway

    captured: dict[str, object] = {}

    async def _answer(_prompt, _config, **kwargs):
        from immich_memories.analysis.llm_query import LLMTransportAttempt

        captured.update(kwargs)
        kwargs["transport_observer"](LLMTransportAttempt(1, "response", 200))
        return '{"episode_reading":{}}'

    trace = Trace()
    gateway = VisualEditorialGateway(
        llm_config=LLMConfig(model="vision-test"), cache_path=tmp_path / "judgments.db", trace=trace
    )
    request = replace(
        _request(_page()),
        pass_name="episode-scan",  # noqa: S106 - test-only pass identity
        limits=VisionRequestLimits(max_output_tokens=2400, timeout_seconds=90),
    )

    # WHY: query_llm is the sole external provider transport this gateway may use.
    with patch("immich_memories.analysis.editorial_gateway.query_llm", new=_answer):
        answer = gateway.ask(request)

    assert captured["max_tokens"] == 2400
    assert captured["timeout_seconds"] == 90
    assert answer.request_trace.actual_calls == 1
    assert answer.request_trace.provenance.request_key == answer.provenance.request_key
    assert trace.requests == [answer.request_trace]


def test_visual_request_budget_changes_cache_identity(tmp_path) -> None:
    """A larger response envelope cannot reuse an answer truncated to a smaller budget."""
    from dataclasses import replace

    from immich_memories.analysis.editorial_gateway import VisualEditorialGateway
    from immich_memories.analysis.llm_query import LLMTransportAttempt

    calls: list[None] = []

    async def _answer(*_args, **kwargs):
        calls.append(None)
        kwargs["transport_observer"](LLMTransportAttempt(1, "response", 200))
        return '{"episode_reading":{}}'

    gateway = VisualEditorialGateway(
        llm_config=LLMConfig(model="vision-test"),
        cache_path=tmp_path / "judgments.db",
        trace=Trace(),
    )
    first = replace(
        _request(_page()),
        limits=VisionRequestLimits(max_output_tokens=1200, timeout_seconds=60),
    )
    second = replace(
        first,
        limits=VisionRequestLimits(max_output_tokens=2400, timeout_seconds=90),
    )

    # WHY: query_llm is the external transport; the visual cache remains real.
    with patch("immich_memories.analysis.editorial_gateway.query_llm", new=_answer):
        first_answer = gateway.ask(first)
        second_answer = gateway.ask(second)

    assert len(calls) == 2
    assert first_answer.provenance.request_key != second_answer.provenance.request_key


def test_gateway_reuses_banked_answer_with_original_and_reuse_provenance(tmp_path) -> None:
    from immich_memories.analysis.editorial_gateway import VisualEditorialGateway

    page = _page()
    trace = Trace()
    gateway = VisualEditorialGateway(
        llm_config=LLMConfig(model="vision-test"), cache_path=tmp_path / "judgments.db", trace=trace
    )

    calls: list[None] = []

    async def _answer(*_args, **_kwargs):
        from immich_memories.analysis.llm_query import LLMTransportAttempt

        calls.append(None)
        _kwargs["transport_observer"](LLMTransportAttempt(1, "response", 200))
        return '{"rejected": []}'

    # WHY: query_llm is the sole external provider transport this gateway may use.
    with patch("immich_memories.analysis.editorial_gateway.query_llm", new=_answer):
        gateway.ask(_request(page))
        reused = gateway.ask(_request(page))

    assert len(calls) == 1
    assert reused.provenance.cache_hit is True
    assert reused.original_provenance.cache_hit is False
    assert reused.request_trace.actual_calls == 0
    assert reused.request_trace.cache_hit is True
    reuse_trace = trace.as_dict()["requests"][-1]
    assert reuse_trace["cache_hit"] is True
    assert reuse_trace["original_provenance"]["cache_hit"] is False


def test_gateway_rejects_a_page_whose_digest_does_not_match_its_bytes(tmp_path) -> None:
    from immich_memories.analysis.editorial_gateway import VisualEditorialGateway

    bad_page = ContactSheetPage(
        sheet_id="episode-001",
        path=Path("/private-sheet.jpg"),
        jpeg_bytes=b"wrong bytes",
        sha256="not-a-real-digest",
        tile_refs=(TileRef(1, "asset-a"),),
        layout_version="layout-1",
    )
    gateway = VisualEditorialGateway(
        llm_config=LLMConfig(model="vision-test"),
        cache_path=tmp_path / "judgments.db",
        trace=Trace(),
    )

    import pytest

    with pytest.raises(ValueError, match="digest"):
        gateway.ask(_request(bad_page))


def test_gateway_traces_each_null_content_retry_as_a_wire_attempt(tmp_path) -> None:
    from immich_memories.analysis.editorial_gateway import VisualEditorialGateway

    response = AsyncMock(status_code=200)
    response.raise_for_status = lambda: None
    response.json = MagicMock(
        return_value={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}
    )
    null = AsyncMock(status_code=200)
    null.raise_for_status = lambda: None
    null.json = MagicMock(
        return_value={"choices": [{"message": {"content": None}, "finish_reason": "stop"}]}
    )
    trace = Trace()
    gateway = VisualEditorialGateway(
        llm_config=LLMConfig(model="vision-test"), cache_path=tmp_path / "judgments.db", trace=trace
    )

    # WHY: the LLM server is the external boundary; this verifies its retry ledger.
    with patch("httpx.AsyncClient.post", side_effect=[null, response]):
        gateway.ask(_request(_page()))

    request_trace = trace.as_dict()["requests"][0]
    assert request_trace["actual_calls"] == 2
    assert [attempt["outcome"] for attempt in request_trace["attempts"]] == [
        "null_content",
        "response",
    ]


def test_gateway_rejects_whitespace_without_banking_the_real_post(tmp_path) -> None:
    from immich_memories.analysis.editorial_gateway import VisualEditorialGateway
    from immich_memories.analysis.llm_query import LLMTransportAttempt

    trace = Trace()
    gateway = VisualEditorialGateway(
        llm_config=LLMConfig(model="vision-test"), cache_path=tmp_path / "judgments.db", trace=trace
    )

    async def _whitespace(*_args, **kwargs):
        kwargs["transport_observer"](LLMTransportAttempt(1, "response", 200))
        return " \n\t"

    # WHY: a completed transport with blank editorial content must still be traceable, not banked.
    with (
        patch("immich_memories.analysis.editorial_gateway.query_llm", new=_whitespace),
        pytest.raises(ValueError, match="nonblank"),
    ):
        gateway.ask(_request(_page()))

    request_trace = trace.as_dict()["requests"][0]
    assert request_trace["actual_calls"] == 1
    assert request_trace["attempts"][0]["outcome"] == "response"
    assert gateway.cache.answer_for(request_trace["provenance"]["request_key"]) is None


def test_gateway_traces_an_invalid_provider_response_as_one_real_post(tmp_path) -> None:
    from immich_memories.analysis.editorial_gateway import VisualEditorialGateway

    response = MagicMock(status_code=200)
    response.raise_for_status.return_value = None
    response.json.side_effect = ValueError("not json")
    trace = Trace()
    gateway = VisualEditorialGateway(
        llm_config=LLMConfig(model="vision-test"), cache_path=tmp_path / "judgments.db", trace=trace
    )

    # WHY: malformed provider content arrives after a real POST and must not look like a free failure.
    with (
        patch("httpx.AsyncClient.post", return_value=response),
        pytest.raises(ValueError, match="not json"),
    ):
        gateway.ask(_request(_page()))

    request_trace = trace.as_dict()["requests"][0]
    assert request_trace["actual_calls"] == 1
    assert [attempt["outcome"] for attempt in request_trace["attempts"]] == ["invalid_response"]
    assert gateway.cache.answer_for(request_trace["provenance"]["request_key"]) is None


@pytest.mark.asyncio
async def test_gateway_works_inside_an_active_event_loop(tmp_path) -> None:
    from immich_memories.analysis.editorial_gateway import VisualEditorialGateway
    from immich_memories.analysis.llm_query import LLMTransportAttempt

    gateway = VisualEditorialGateway(
        llm_config=LLMConfig(model="vision-test"),
        cache_path=tmp_path / "judgments.db",
        trace=Trace(),
    )

    async def _answer(*_args, **kwargs):
        kwargs["transport_observer"](LLMTransportAttempt(1, "response", 200))
        return "complete"

    # WHY: query_llm is the provider boundary; the active loop is the behavior under test.
    with patch("immich_memories.analysis.editorial_gateway.query_llm", new=_answer):
        assert gateway.ask(_request(_page())).raw_text == "complete"
