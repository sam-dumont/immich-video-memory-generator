"""A prepared day is judged from caption text, and nothing is ever sent a picture."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from immich_memories.analysis.special_day import PROMPT_VERSION, ask_if_special
from immich_memories.automation.special_day_scan import scan_year
from immich_memories.config_models_llm import LLMConfig


def test_a_prepared_day_is_answered_once_and_then_from_the_bank(tmp_path):
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
        )
        repeated = scan_year(
            assets,
            llm_config=config,
            home=None,
            ask=1,
            captions=captions,
            judgment_cache_path=tmp_path / "judgments.db",
        )
    assert found == repeated
    assert len(found) == 1
    assert post.call_count == 1
    payload = post.call_args.kwargs["json"]
    assert "images" not in payload
    assert "finish line" in payload["prompt"]
    assert "2021-04-13T09:" in payload["prompt"]
    assert PROMPT_VERSION in payload["prompt"]


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


def test_a_day_the_bank_barely_touched_is_not_guessed_at(tmp_path):
    """Three captions out of thirty, and nothing else written: nobody can say.

    It used to buy tiles, which is how a scan that had never seen this day
    still returned a verdict about it. Now the day is recorded unjudged and no
    live call is made at all.
    """
    assets, captions = _a_real_day(captioned=3)

    # WHY: intercept the external LLM HTTP request; that it never happens is the subject.
    with patch("httpx.AsyncClient.post", return_value=_verdict_response(_VERDICT)) as post:
        found = scan_year(
            assets,
            llm_config=LLMConfig(model="text-reader", provider="ollama"),
            home=None,
            ask=1,
            captions=captions,
            judgment_cache_path=tmp_path / "judgments.db",
        )
    assert post.call_count == 0
    assert [(day.day.isoformat(), day.judged) for day in found] == [("2021-04-13", False)]


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


def test_the_caption_ask_is_given_a_reasoning_host_s_leash():
    """A hosted reasoning model bills thinking whether or not the call asked for it."""
    from immich_memories.analysis.special_day import _THINKING_TIMEOUT_SECONDS

    assets, captions = _a_real_day(captioned=30)
    config = LLMConfig(model="text-reader", provider="ollama", thinking="high")
    waited = []

    async def record(prompt, llm_config, **kwargs):
        waited.append(kwargs["timeout_seconds"])
        return _VERDICT

    # WHY: replace the provider call itself; only the budget it is handed is under test.
    with patch("immich_memories.analysis.llm_query.query_llm", record):
        ask_if_special(assets, config, captions=captions)
    assert waited == [_THINKING_TIMEOUT_SECONDS]


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
