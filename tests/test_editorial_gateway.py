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
