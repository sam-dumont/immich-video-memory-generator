"""A prepared day is judged from caption text, with no second photo send."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from immich_memories.analysis.special_day import ask_if_special
from immich_memories.automation.special_day_scan import scan_year
from immich_memories.config_models_llm import LLMConfig


def test_prepared_scan_never_downloads_thumbnails_and_reuses_its_answer(tmp_path):
    assets = [
        SimpleNamespace(
            id=f"a-{h}-{m}",
            file_created_at=datetime(2021, 4, 13, h, m, tzinfo=UTC),
            exif_info=None,
            people=[],
        )
        for h in range(9, 17)
        for m in (0, 20, 40)
    ]
    captions = {a.id: "A group runs across a finish line" for a in assets}
    response = httpx.Response(
        200,
        request=httpx.Request("POST", "http://localhost"),
        json={
            "response": json.dumps(
                {
                    "special": True,
                    "title": "At the finish line",
                    "subtitle": "",
                    "what": "A race",
                    "window": None,
                }
            ),
            "done": True,
        },
    )

    def thumbnail_for(asset_id):
        raise AssertionError("Prepared days must not download pictures")

    config = LLMConfig(model="text-reader", provider="ollama")
    # WHY: intercept the external LLM HTTP request; the scan and bank run unchanged.
    with patch("httpx.AsyncClient.post", return_value=response) as post:
        found = scan_year(
            assets,
            llm_config=config,
            home=None,
            ask=1,
            captions=captions,
            judgment_cache_path=tmp_path / "judgments.db",
            thumbnail_for=thumbnail_for,
        )
        repeated = scan_year(
            assets,
            llm_config=config,
            home=None,
            ask=1,
            captions=captions,
            judgment_cache_path=tmp_path / "judgments.db",
            thumbnail_for=thumbnail_for,
        )
    assert found == repeated
    assert len(found) == 1
    assert post.call_count == 1
    payload = post.call_args.kwargs["json"]
    assert "images" not in payload
    assert "finish line" in payload["prompt"]
    assert "2021-04-13T09:" in payload["prompt"]
    assert "special-day-captions-v1" in payload["prompt"]


def _a_real_day(captioned: int) -> tuple[list, dict[str, str]]:
    """A day that clears the scan's candidate bar, with the first N pictures described."""
    assets = [
        SimpleNamespace(
            id=f"a-{h}-{m}",
            file_created_at=datetime(2021, 4, 13, h, m, tzinfo=UTC),
            exif_info=None,
            people=[],
        )
        for h in range(9, 19)
        for m in (0, 20, 40)
    ]
    return assets, {a.id: "A group runs across a finish line" for a in assets[:captioned]}


def _verdict_response(body: str) -> httpx.Response:
    return httpx.Response(
        200,
        request=httpx.Request("POST", "http://localhost"),
        json={"response": body, "done": True},
    )


_VERDICT = json.dumps(
    {
        "special": True,
        "title": "At the finish line",
        "subtitle": "",
        "what": "A race",
        "window": None,
    }
)


def test_a_day_the_bank_barely_touched_keeps_its_pictures(tmp_path):
    """Three captions out of thirty is not a prepared day, and tiles beat one line."""
    assets, captions = _a_real_day(captioned=3)
    downloaded = []

    def thumbnail_for(asset_id):
        downloaded.append(asset_id)
        return b"jpeg-bytes"

    # WHY: intercept the external LLM HTTP request; the scan and its sampling run unchanged.
    with patch("httpx.AsyncClient.post", return_value=_verdict_response(_VERDICT)) as post:
        scan_year(
            assets,
            llm_config=LLMConfig(model="text-reader", provider="ollama"),
            home=None,
            ask=1,
            captions=captions,
            judgment_cache_path=tmp_path / "judgments.db",
            thumbnail_for=thumbnail_for,
        )
    assert downloaded
    payload = post.call_args.kwargs["json"]
    assert payload["images"]
    assert "special-day-captions-v1" not in payload["prompt"]


def test_the_caption_ask_leaves_reasoning_to_the_transport(tmp_path):
    """A declared reasoning host budgets thinking beside the answer, not inside it."""
    from immich_memories.analysis.llm_wire import THINKING_MIN_MAX_TOKENS

    assets, captions = _a_real_day(captioned=30)
    config = LLMConfig(
        # A model name of its own: the reasoning budget is learned per endpoint.
        model="caption-ask-probe",
        provider="openai-compatible",
        base_url="http://reader.invalid/v1",
        thinking="high",
        always_reasons=True,
    )
    reply = httpx.Response(
        200,
        request=httpx.Request("POST", "http://reader.invalid/v1/chat/completions"),
        json={
            "choices": [{"message": {"content": _VERDICT}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "reasoning_tokens": 0},
        },
    )
    # WHY: intercept the external LLM HTTP request so the posted budget can be read.
    with patch("httpx.AsyncClient.post", return_value=reply) as post:
        ask_if_special(
            assets,
            config,
            captions=captions,
            judgment_cache_path=tmp_path / "judgments.db",
        )
    payload = post.call_args.kwargs["json"]
    assert payload["reasoning_effort"] == "low"
    assert payload["max_tokens"] > THINKING_MIN_MAX_TOKENS


def test_the_caption_ask_waits_as_long_as_the_picture_ask():
    """Same model, same judgement, same leash — a timeout here reads as "not special"."""
    assets, captions = _a_real_day(captioned=30)
    config = LLMConfig(model="text-reader", provider="ollama", thinking="high")
    waited = []

    async def record(prompt, llm_config, **kwargs):
        waited.append(kwargs["timeout_seconds"])
        return _VERDICT

    # WHY: replace the provider call itself; only the budget it is handed is under test.
    with patch("immich_memories.analysis.llm_query.query_llm", record):
        ask_if_special(assets, config, captions=captions)
        ask_if_special(assets, config, thumbnails=[(assets[0], b"jpeg-bytes")])
    assert waited[0] == waited[-1]


def test_a_fenced_answer_is_still_an_answer_without_a_bank():
    """The uncached route reads the model's raw text as leniently as the picture route."""
    assets, captions = _a_real_day(captioned=30)
    fenced = _verdict_response(f"```json\n{_VERDICT}\n```")
    # WHY: intercept the external LLM HTTP request; the reply shape is what is under test.
    with patch("httpx.AsyncClient.post", return_value=fenced):
        verdict = ask_if_special(
            assets, LLMConfig(model="text-reader", provider="ollama"), captions=captions
        )
    assert verdict.special
    assert verdict.title == "At the finish line"
