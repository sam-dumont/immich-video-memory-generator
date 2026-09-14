"""The two request dialects, what a reply in each of them says, and what it cost.

`llm_query` speaks these over a live connection and `llm_batch` writes the same
bodies into a batch file. Keeping the shapes in one place is what makes a
queued answer and a live one the same answer: one module decides what a request
looks like, and both transports read it rather than each writing their own.

Reading a reply lives here for the same reason, and because an answer channel is
not a token count. A reasoning model bills its private thinking inside the same
completion budget as its answer, so "the reply stopped at the limit" says nothing
about whether an answer was ever written. Reading the two apart is what lets the
caller be told which of them ran out — and what the budget below is for.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

import httpx

from immich_memories.analysis import llm_metrics
from immich_memories.analysis.llm_providers import (
    ANTHROPIC_VERSION,
    LOWEST_THINKING_LEVEL,
    resolved_llm_config,
)
from immich_memories.config_models_llm import LLMConfig

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

logger = logging.getLogger(__name__)

# Every model call this project makes is a judgement it wants back the same way
# twice: a description that feeds a decision, or a decision itself. Sampling was
# measured turning one real pack's answer over four repeats into four different
# answers, one of which named all 105 tiles. Greedy decoding also makes the
# judgement cache honest -- a banked answer is what re-asking would return.
DEFAULT_TEMPERATURE = 0.0

# Measured on the live endpoint: at 500 max_tokens a thinking call truncates
# mid-think and the reasoning leaks into the content channel; ~600-2300 chars
# of reasoning plus the answer fits comfortably under 4000. Latency ran
# 30-134s where non-thinking answered in 4-7s, so the read budget rises too.
THINKING_MIN_MAX_TOKENS = 4000
THINKING_MIN_TIMEOUT_SECONDS = 180

# Measured on real OpenAI: gpt-5-family models reject `max_tokens` (they want
# `max_completion_tokens`) and any temperature but the default. The 400 body
# names the offending parameter, so the call adapts once and remembers per
# (server, model) for the rest of the process.
PARAM_ADAPTATIONS: dict[tuple[str, str], set[str]] = {}


# z.ai's OpenAI-compatible route refuses "off" outright on the models that
# always reason: HTTP 400 code 1210, "This model always engages in thinking and
# cannot be disabled; please use low, high, or max".
THINKING_REQUIRED_CODE = "1210"
_LOWEST_LEVEL_ADAPTATION = "lowest_thinking_level"
_NO_REASONING_EFFORT = "no_reasoning_effort"


def adaptation_for(error: dict) -> str | None:
    if str(error.get("code", "")) == THINKING_REQUIRED_CODE:
        return _LOWEST_LEVEL_ADAPTATION
    message = str(error.get("message", ""))
    if "chat_template_kwargs" in message:
        return "no_chat_template_kwargs"
    if "max_tokens" in message and "max_completion_tokens" in message:
        return "max_completion_tokens"
    if "temperature" in message and ("not support" in message or "Unsupported" in message):
        return "default_temperature"
    if "reasoning_effort" in message:
        return _NO_REASONING_EFFORT
    return None


def apply_adaptations(payload: dict, adaptations: set[str]) -> None:
    if "no_chat_template_kwargs" in adaptations:
        payload.pop("chat_template_kwargs", None)
    if "max_completion_tokens" in adaptations and "max_tokens" in payload:
        payload["max_completion_tokens"] = payload.pop("max_tokens")
    if "default_temperature" in adaptations:
        payload.pop("temperature", None)
    if _NO_REASONING_EFFORT in adaptations:
        # A host that refuses the field still reasons; the raised ceiling is
        # what keeps the answer intact, and that is not sent back.
        payload.pop("reasoning_effort", None)
    if _LOWEST_LEVEL_ADAPTATION in adaptations:
        # The refusal is only ever to "off": a level the model will reason at
        # is left exactly as the caller asked for it.
        thinking = payload.get("thinking")
        if isinstance(thinking, dict) and thinking.get("type") == "disabled":
            payload["thinking"] = {"type": LOWEST_THINKING_LEVEL}


def announce_adaptation(adaptation: str, before: object, after: object) -> None:
    if adaptation == _LOWEST_LEVEL_ADAPTATION:
        logger.warning(
            "LLM provider refused thinking %s (code %s); retrying once with %s",
            before,
            THINKING_REQUIRED_CODE,
            after,
        )
    else:
        logger.info("LLM server dialect: adapting request (%s)", adaptation)


def shape_for_provider(payload: dict, config: LLMConfig) -> None:
    """Apply the configured provider dialect before any auto-negotiation."""
    if config.max_tokens_param != "max_tokens" and "max_tokens" in payload:
        payload[config.max_tokens_param] = payload.pop("max_tokens")
    for name in config.drop_params:
        payload.pop(name, None)
    payload.update(config.extra_params)


class LLMIncompleteResponse(ValueError):
    """A provider-confirmed output limit, with only the partial final-answer channel.

    `reasoning_tokens` is why the caller can tell the two limits apart: a
    reasoning host bills its private thinking inside the same budget, so a reply
    that hit the ceiling without writing a word ran out of room to think rather
    than room to answer.
    """

    def __init__(
        self,
        raw: object = None,
        *,
        finish_reason: str = "",
        completion_tokens: int = 0,
        reasoning_tokens: int = 0,
    ):
        self.raw = raw.rsplit("</think>", 1)[-1].lstrip() if isinstance(raw, str) else ""
        if self.raw.startswith("<think>"):
            self.raw = ""  # A truncated reasoning block contains no final-answer evidence.
        self.finish_reason = finish_reason
        self.completion_tokens = completion_tokens
        self.reasoning_tokens = reasoning_tokens
        detail = (
            f": reasoning used {reasoning_tokens} of {completion_tokens} tokens, no answer"
            if reasoning_tokens and not self.raw
            else ""
        )
        super().__init__("LLM returned incomplete content" + detail)


# Dropped connections are retried this many times before the call fails; a
# provider that stopped answering is announced on the first drop (provider_status).
TRANSPORT_RETRIES = 3


@dataclass(frozen=True)
class LLMTransportAttempt:
    """One actual HTTP POST outcome, kept separate from accepted-reply metrics."""

    attempt: int
    outcome: str
    status_code: int | None
    adaptation: str | None = None
    # What the provider said about the reply it just sent: where it stopped, what
    # it billed, and how much of that it spent thinking rather than answering.
    # Absent on every outcome that never reached a decoded completion.
    finish_reason: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None


@dataclass(frozen=True)
class LLMReply:
    """One decoded OpenAI-style completion: the answer, and what it cost to get."""

    content: str | None
    finish_reason: str
    prompt_tokens: int
    completion_tokens: int
    reasoning_tokens: int
    retry_without_thinking: bool = False


# Measured 2026-09-14 on the demo month's 17 KB episode read, at the reader's own
# 4,000-token ask, against Melious (api.melious.ai/v1) and OpenAI. Every model
# there returns `message.reasoning_content` and `usage.reasoning_tokens` with
# nothing having asked it to think, and both providers put `reasoning_tokens`
# inside `completion_tokens` — so the caller's answer budget was never the
# answer's. Asked for the cheapest reasoning on offer, that one prompt cost
# glm-5.3-flash 245 thinking tokens, gpt-5.6-luna 1,253, gemma-4-31b 4,126,
# deepseek-v4.1-flash 6,256 and muse-glimmer 13,469. 16,384 carries all five in
# one call, which matters more than it looks: a second call inherits what is
# left of the caller's read budget, and muse-glimmer's retry ran that out.
# It is a ceiling and not a spend -- a model that does not think that long is
# not billed for the room, and an endpoint that does not reason never gets it.
REASONING_HEADROOM_TOKENS = 16384
# One growth, for a model that thinks harder than any measured one. Granted
# once, on a reply that spent the answer's budget thinking, rather than paid for
# on every call. A host that needs it is also likely to need a longer
# `llm.timeout_seconds` than the 300 the matrix runs at.
GROWN_REASONING_HEADROOM_TOKENS = 32768

# The cheapest reasoning an OpenAI-compatible host admits to. Melious documents
# "low" | "medium" | "high" and ignores the field on a model that does not
# reason; OpenAI's own gpt-5 family takes "minimal" below that, which its preset
# carries. Reasoning cannot be turned off on either: muse-glimmer reasons by
# architecture, so the ask is for as little of it as the host will sell.
LOWEST_REASONING_EFFORT = "low"

# Which endpoints reason whether or not anybody asked, and how much room their
# thinking needs beside the answer. Learned from the first reply that bills
# reasoning and remembered per (server, model) for the rest of the process, the
# same way `PARAM_ADAPTATIONS` remembers a dialect.
_REASONING_HEADROOM: dict[tuple[str, str], int] = {}


def reasoning_headroom(key: tuple[str, str], *, declared: bool) -> int:
    """Tokens to add to this endpoint's answer budget, or 0 where it does not reason.

    A declared endpoint's grant is recorded on the way out, so the memory is the
    one record of the room this endpoint has actually been given and a reply that
    still ran short can be told from a first call that was never funded.
    """
    learned = _REASONING_HEADROOM.get(key)
    if learned is not None:
        return learned
    if not declared:
        return 0
    return _REASONING_HEADROOM.setdefault(key, REASONING_HEADROOM_TOKENS)


def remember_reasoning(key: tuple[str, str]) -> None:
    """Record an endpoint that thinks, so the next call budgets for the thinking."""
    _REASONING_HEADROOM.setdefault(key, REASONING_HEADROOM_TOKENS)


def learn_reasoning(key: tuple[str, str], reply: LLMReply) -> None:
    """Remember an endpoint that billed for thinking, so the next call budgets for it."""
    if reply.reasoning_tokens > 0:
        remember_reasoning(key)


def widen_for_reasoning(key: tuple[str, str], *, reasoning_tokens: int, answered: bool) -> bool:
    """Give this endpoint more room, and say whether the call is worth making again.

    An endpoint that had no reasoning budget at all spent the caller's answer
    tokens on thinking, and gets the standard room whether it wrote part of an
    answer or none: gemma-4-31b came back at 4,000 tokens with 3,163 of them
    spent thinking and an answer cut off mid-object. One that already had the
    room gets the larger allowance once, and only when nothing was written —
    with room for both, a cut-off answer is a cut-off answer and more tokens
    only buy a longer one.
    """
    if reasoning_tokens <= 0:
        return False
    granted = _REASONING_HEADROOM.get(key)
    if granted is None:
        _REASONING_HEADROOM[key] = REASONING_HEADROOM_TOKENS
        return True
    if answered or granted >= GROWN_REASONING_HEADROOM_TOKENS:
        return False
    _REASONING_HEADROOM[key] = GROWN_REASONING_HEADROOM_TOKENS
    return True


def apply_reasoning_headroom(payload: dict, max_tokens: int, headroom: int) -> None:
    """Ask for the least thinking on offer, and pay for it outside the answer's budget."""
    payload.setdefault("reasoning_effort", LOWEST_REASONING_EFFORT)
    payload["max_tokens"] = max_tokens + headroom


def _anthropic_content(prompt: str, images: Sequence[bytes]) -> str | list[dict]:
    """The message body: a bare string without pictures, blocks with them.

    Same arrangement as the OpenAI path, prompt first and the tiles behind it
    in order. The reading contracts were written and graded against that one,
    and several of them are answered per picture in the order the pictures
    arrived, so the two adapters have to hand the model the same thing.
    """
    if not images:
        return prompt
    return [
        {"type": "text", "text": prompt},
        *(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": base64.b64encode(image).decode("utf-8"),
                },
            }
            for image in images
        ),
    ]


# A reasoning block is written for the dialect its server speaks, so only the
# fields the Messages API itself defines are carried onto this wire: the generic
# default is Qwen's `chat_template_kwargs`, which no Messages host understands.
# Anything else a host needs belongs in `extra_params`.
_MESSAGES_REASONING_FIELDS = ("thinking", "output_config")

# The one setting that means the host will not reason at all. Every other value
# is a request it may answer in its own way, which is what the headroom pays for.
_REASONING_OFF = "disabled"


def _messages_reasoning(params: dict) -> dict:
    return {name: params[name] for name in _MESSAGES_REASONING_FIELDS if name in params}


def apply_anthropic_reasoning(
    payload: dict,
    config: LLMConfig,
    thinking: bool,
    max_tokens: int,
    timeout: int,
    endpoint: tuple[str, str] | None,
) -> int:
    """Put the reasoning switch and the tokens it will cost on one payload.

    `endpoint` is None for a queued batch line, which is granted no headroom: a
    line that spends the answer's budget thinking comes back empty and is re-asked
    live, where the ledger applies.
    """
    if thinking:
        # The dialect refuses any temperature but the default while reasoning.
        payload.pop("temperature", None)
        payload.update(_messages_reasoning(config.thinking_params))
        payload["max_tokens"] = max(max_tokens, THINKING_MIN_MAX_TOKENS)
        return max(timeout, THINKING_MIN_TIMEOUT_SECONDS)
    bulk = _messages_reasoning(config.no_thinking_params)
    payload.update(bulk)
    switch = bulk.get("thinking")
    asked_off = isinstance(switch, dict) and switch.get("type") == _REASONING_OFF
    # A setting short of off is a request rather than a guarantee, so the answer
    # keeps the cap the caller asked for and the reasoning the host does anyway
    # gets its own room, on the same learned ledger the OpenAI dialects use. A
    # host told `disabled` is taken at its word until one of its replies thinks
    # anyway: measured 2026-09-14, z.ai's /api/anthropic route ignores `disabled`
    # and honours `low`, and a 64-token ask carrying one picture spent all 64
    # inside the thinking block and returned no text block at all.
    headroom = (
        0
        if endpoint is None
        else reasoning_headroom(endpoint, declared=config.always_reasons or not asked_off)
    )
    if headroom:
        payload["max_tokens"] = max_tokens + headroom
    return timeout


def anthropic_payload(
    prompt: str,
    config: LLMConfig,
    temperature: float,
    max_tokens: int,
    images: Sequence[bytes],
) -> dict:
    return {
        "model": config.model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": _anthropic_content(prompt, images)}],
        "temperature": temperature,
    }


def _openai_content(
    prompt: str, images: Sequence[bytes], image_detail: str | None = "low"
) -> str | list[dict]:
    """The message body: a bare string without pictures, parts with them."""
    if not images:
        return prompt
    return [
        {"type": "text", "text": prompt},
        *(
            {
                "type": "image_url",
                "image_url": {
                    "url": "data:image/jpeg;base64," + base64.b64encode(image).decode("utf-8"),
                    **({"detail": image_detail} if image_detail is not None else {}),
                },
            }
            for image in images
        ),
    ]


def apply_thinking_budget(payload: dict, config: LLMConfig, max_tokens: int, timeout: int) -> int:
    """Raise the payload token ceiling in place and answer with the raised timeout."""
    payload.update(config.thinking_params)
    thinking_budget = payload.get("thinking_budget")
    if isinstance(thinking_budget, int) and not isinstance(thinking_budget, bool):
        payload["max_tokens"] = max(max_tokens + max(0, thinking_budget), THINKING_MIN_MAX_TOKENS)
    else:
        payload["max_tokens"] = max(max_tokens, THINKING_MIN_MAX_TOKENS)
    return max(timeout, THINKING_MIN_TIMEOUT_SECONDS)


def openai_payload(
    prompt: str,
    config: LLMConfig,
    temperature: float,
    max_tokens: int,
    images: Sequence[bytes],
    image_detail: str,
) -> dict:
    return {
        "model": config.model,
        "messages": [
            {
                "role": "user",
                "content": _openai_content(
                    prompt, images, image_detail if config.send_image_detail else None
                ),
            }
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }


def openai_headers(config: LLMConfig) -> dict[str, str]:
    """The bearer header every /chat/completions host takes, batch route included."""
    return {"Authorization": f"Bearer {config.api_key}"} if config.api_key else {}


def anthropic_headers(config: LLMConfig) -> dict[str, str]:
    """The key and version headers every /v1/messages host takes, batch route included."""
    headers = {"anthropic-version": ANTHROPIC_VERSION}
    if config.api_key:
        headers["x-api-key"] = config.api_key
    return headers


def batch_text_payload(config: LLMConfig, prompt: str, *, max_tokens: int) -> dict:
    """The body one realtime text call would POST, for a batch line to carry.

    Built from the dialect helpers the live path uses rather than beside them,
    so a banked batch answer is the answer asking in real time would have given.
    Reasoning is never requested here: the batched stages are the bulk reads,
    and a queued reasoning call is the one shape whose price is not halved. Nor
    is the live path's reasoning headroom granted — a queued line that spends
    the answer's budget thinking comes back empty and is re-asked live, which is
    what this route already does with every answer its stage refuses.
    """
    resolved = resolved_llm_config(config)
    if resolved.provider == "anthropic":
        payload = anthropic_payload(prompt, resolved, DEFAULT_TEMPERATURE, max_tokens, ())
        apply_anthropic_reasoning(payload, resolved, False, max_tokens, 0, None)
        shape_for_provider(payload, resolved)
        return payload
    payload = openai_payload(prompt, resolved, DEFAULT_TEMPERATURE, max_tokens, (), "low")
    if resolved.no_thinking_params:
        payload.update(resolved.no_thinking_params)
    shape_for_provider(payload, resolved)
    apply_adaptations(
        payload,
        PARAM_ADAPTATIONS.setdefault((resolved.base_url.rstrip("/"), resolved.model), set()),
    )
    return payload


def read_batch_answer(config: LLMConfig, body: dict) -> str:
    """The final answer out of one completed reply, in whichever dialect it arrived.

    Truncation is not distinguished here the way the live path distinguishes it:
    a batched answer that hit its ceiling comes back as the partial text, and the
    stage's own parser refuses it, which sends that one prompt back to the live
    path with the doubled budget it would have got anyway.
    """
    if resolved_llm_config(config).provider == "anthropic":
        content = body.get("content")
        if not isinstance(content, list):
            raise TypeError("Anthropic response content is not a list")
        texts = [block["text"] for block in content if block.get("type") == "text"]
        if not texts:
            raise ValueError(reasoning_only_detail(body))
        return texts[0]
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError(no_choices_detail(body))
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise TypeError("OpenAI response message is not an object")
    return openai_answer_content(message) or ""


def _billed(choice: dict, usage: dict, content: str | None) -> LLMReply:
    """One reply's answer channel beside the three numbers the provider billed it at."""
    return LLMReply(
        content,
        str(choice.get("finish_reason") or ""),
        int(usage.get("prompt_tokens", 0) or 0),
        int(usage.get("completion_tokens", 0) or 0),
        int(
            usage.get("reasoning_tokens")
            or (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
            or 0
        ),
    )


def _openai_completion(
    choice: dict,
    usage: dict,
    thinking: bool,
    require_complete: bool,
    observer: Callable[[LLMTransportAttempt], None] | None,
    attempt: int,
    status_code: int,
) -> LLMReply:
    message = choice["message"]
    if not isinstance(message, dict):
        raise TypeError("OpenAI response message is not an object")
    if str(choice.get("finish_reason") or "") != "length":
        return _billed(choice, usage, openai_answer_content(message))
    # Read the answer channel, never the completion count: with headroom the
    # reply is meant to bill more tokens than the caller asked for, and it is an
    # empty content field — not a number — that says nothing was written.
    answered = message.get("content")
    partial = answered if isinstance(answered, str) else ""
    reply = _billed(choice, usage, partial)
    if require_complete:
        observe(observer, attempt, "incomplete", status_code, reply=reply)
        llm_metrics.record_truncation()
        raise LLMIncompleteResponse(
            partial,
            finish_reason=reply.finish_reason,
            completion_tokens=reply.completion_tokens,
            reasoning_tokens=reply.reasoning_tokens,
        )
    if thinking:
        observe(observer, attempt, "thinking_fallback", status_code)
        return replace(reply, content=None, retry_without_thinking=True)
    return replace(reply, content=openai_answer_content(message))


def openai_answer_content(message: dict) -> str | None:
    """Return only the final answer, never the model's private reasoning."""
    if "content" not in message:
        # oMLX occasionally returns a message carrying only its role (an empty answer with
        # finish_reason=stop). Measured 2026-09-02 on the 30B: ~1.6 % of calls. It is the same
        # failure as a truncated answer for the caller, so it is reported the same way.
        raise ValueError("LLM returned empty content")
    content = message["content"]
    reasoning = message.get("reasoning_content")
    if content is not None and not isinstance(content, str):
        raise TypeError("OpenAI response content is not text")
    if reasoning is not None and not isinstance(reasoning, str):
        raise TypeError("OpenAI response reasoning_content is not text")
    if content and "</think>" in content:
        # Older compatible servers sometimes inline the reasoning channel.
        # The final close marker is the boundary; only what follows is an answer.
        content = content.rsplit("</think>", 1)[1].lstrip()
    return content


def anthropic_usage(
    response: httpx.Response, observer: Callable[[LLMTransportAttempt], None] | None
) -> tuple[dict, dict]:
    """Decode the native Anthropic body far enough to retain usage metrics."""
    try:
        body = response_body(response)
        usage = body.get("usage") or {}
        if not isinstance(usage, dict):
            raise TypeError("Anthropic usage is not an object")
    except (TypeError, ValueError):
        record_invalid_response(observer, response.status_code)
        raise
    return body, usage


def anthropic_answer(
    body: dict, response: httpx.Response, observer: Callable[[LLMTransportAttempt], None] | None
) -> str | None:
    """The first text block, or None when the reply carried reasoning only.

    A reasoning model puts one or more `thinking` blocks in front of its
    answer, and on z.ai's route it does so whether or not it was asked to, so
    a reply with no text block at all is a normal shape here rather than a
    malformed body.
    """
    try:
        content = body["content"]
        if not isinstance(content, list):
            raise TypeError("Anthropic response content is not a list")
        texts = [block["text"] for block in content if block.get("type") == "text"]
    except (KeyError, TypeError, AttributeError):
        record_invalid_response(observer, response.status_code)
        raise
    return texts[0] if texts else None


def anthropic_reasoned(body: dict) -> bool:
    """Whether this reply thought before answering, whatever it was asked to do.

    The dialect bills thinking inside `output_tokens` rather than reporting it
    separately, so the blocks are the only evidence that an endpoint reasons.
    """
    content = body.get("content")
    return isinstance(content, list) and any(
        str(block.get("type")).startswith("thinking") for block in content
    )


def reasoning_only_detail(body: dict) -> str:
    """Name what came back instead of an answer, and where it stopped."""
    content = body.get("content")
    kinds = (
        sorted({str(block.get("type")) for block in content}) if isinstance(content, list) else []
    )
    return (
        "LLM provider returned no text block: "
        f"stop_reason {str(body.get('stop_reason'))!r}, blocks {kinds}"
    )


def no_choices_detail(body: dict) -> str:
    """Some gateways answer 200 with their own error envelope instead of a completion."""
    code = body.get("code")
    message = body.get("msg") or body.get("message")
    if code is None and not message:
        return f"LLM provider returned no choices; body fields: {sorted(body)[:10]}"
    return f"LLM provider returned no choices: code {code}, msg {str(message)[:300]!r}"


def _completion_and_usage(
    response: httpx.Response, observer: Callable[[LLMTransportAttempt], None] | None
) -> tuple[dict, dict, dict]:
    try:
        body = response_body(response)
        error = body.get("error")
        if isinstance(error, dict):
            code = str(error.get("code", "unknown"))[:80]
            message = str(error.get("message", "provider returned an error"))[:300]
            raise ValueError(f"LLM provider error {code}: {message}")
        if "choices" not in body:
            raise ValueError(no_choices_detail(body))
        choice = body["choices"][0]
        usage = body.get("usage") or {}
        if not isinstance(choice, dict) or not isinstance(usage, dict):
            raise TypeError("OpenAI response has an invalid body shape")
    except (KeyError, TypeError, ValueError, IndexError):
        record_invalid_response(observer, response.status_code)
        raise
    return body, choice, usage


def interpret_openai_response(
    response: httpx.Response,
    thinking: bool,
    require_complete: bool,
    observer: Callable[[LLMTransportAttempt], None] | None,
    attempt: int,
) -> LLMReply:
    """Parse one OpenAI-style reply and preserve its completed-post outcome."""
    body, choice, usage = _completion_and_usage(response, observer)
    llm_metrics.record_reply(
        prompt_tokens=usage.get("prompt_tokens", 0) or 0,
        cached_prompt_tokens=(usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
        or 0,
        completion_tokens=usage.get("completion_tokens", 0) or 0,
        # A subset of completion_tokens in this dialect, reported apart because
        # it is the part a reasoning model adds and nobody asked for.
        reasoning_tokens=(usage.get("completion_tokens_details") or {}).get("reasoning_tokens", 0)
        or 0,
        model=served_model(body),
    )
    try:
        return _openai_completion(
            choice, usage, thinking, require_complete, observer, attempt, response.status_code
        )
    except (KeyError, TypeError, AttributeError):
        record_invalid_response(observer, response.status_code)
        raise


def observe(
    observer: Callable[[LLMTransportAttempt], None] | None,
    attempt: int,
    outcome: str,
    status_code: int | None,
    adaptation: str | None = None,
    *,
    reply: LLMReply | None = None,
) -> None:
    if observer is not None:
        observer(
            LLMTransportAttempt(
                attempt,
                outcome,
                status_code,
                adaptation,
                finish_reason=None if reply is None else reply.finish_reason,
                prompt_tokens=None if reply is None else reply.prompt_tokens,
                completion_tokens=None if reply is None else reply.completion_tokens,
                reasoning_tokens=None if reply is None else reply.reasoning_tokens,
            )
        )


def response_body(response: httpx.Response) -> dict:
    """Decode one provider response as the object every dialect requires."""
    body = response.json()
    if not isinstance(body, dict):
        raise TypeError("LLM response body is not an object")
    return body


def served_model(body: dict) -> str | None:
    """What the server said it answered with, when it said anything.

    The config's model id is what was asked for. A gateway is free to serve
    something else, and the bill follows what it served.
    """
    served = body.get("model")
    return served if isinstance(served, str) and served else None


def record_invalid_response(
    observer: Callable[[LLMTransportAttempt], None] | None, status_code: int
) -> None:
    """Trace the one completed POST whose content could not be parsed."""
    observe(observer, 1, "invalid_response", status_code)


# Enough of a refused call to act on, short enough to sit on a warning line.
PROVIDER_MESSAGE_CHARS = 300


def _provider_error_detail(response: httpx.Response) -> str:
    """The provider's own code and message, bounded, or "" when it named neither.

    httpx's HTTPStatusError text is the status line and a link to MDN. The
    reason a call was refused exists only in the body, so without this the
    operator reads "400 Bad Request" and has nothing to go on.
    """
    try:
        body = response.json()
    except ValueError:
        return ""
    if not isinstance(body, dict):
        return ""
    error = body.get("error")
    if isinstance(error, str):
        # Ollama's refusals are a bare sentence under `error`, with no code.
        return f"message {error[:PROVIDER_MESSAGE_CHARS]!r}"
    named = error if isinstance(error, dict) else body
    code = named.get("code")
    message = named.get("message") or named.get("msg")
    if code is None and not message:
        return ""
    return f"code {code}, message {str(message)[:PROVIDER_MESSAGE_CHARS]!r}"


def ensure_success(
    response: httpx.Response, observer: Callable[[LLMTransportAttempt], None] | None
) -> None:
    """Raise on a 4xx or 5xx, carrying the provider's own code and message."""
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        observe(observer, 1, "http_error", response.status_code)
        detail = _provider_error_detail(response)
        if not detail:
            raise
        summary = f"{str(exc).splitlines()[0]} - provider said {detail}"
        raise httpx.HTTPStatusError(summary, request=exc.request, response=response) from exc
