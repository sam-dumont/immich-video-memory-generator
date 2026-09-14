"""The two request dialects, and what a reply in each of them says.

`llm_query` speaks these over a live connection and `llm_batch` writes the same
bodies into a batch file. Keeping the shapes in one place is what makes a
queued answer and a live one the same answer: one module decides what a request
looks like, and both transports read it rather than each writing their own.
"""

from __future__ import annotations

import base64
import logging
from collections.abc import Sequence

from immich_memories.analysis.llm_providers import (
    ANTHROPIC_VERSION,
    LOWEST_THINKING_LEVEL,
    resolved_llm_config,
)
from immich_memories.config_models_llm import LLMConfig

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
    return None


def apply_adaptations(payload: dict, adaptations: set[str]) -> None:
    if "no_chat_template_kwargs" in adaptations:
        payload.pop("chat_template_kwargs", None)
    if "max_completion_tokens" in adaptations and "max_tokens" in payload:
        payload["max_completion_tokens"] = payload.pop("max_tokens")
    if "default_temperature" in adaptations:
        payload.pop("temperature", None)
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


# Measured 2026-09-14 against z.ai's /api/anthropic route with glm-5.3-flash.
# Where /api/paas/v4 refuses `{"type": "disabled"}` outright with code 1210,
# this route accepts every level, answers HTTP 200, and then reasons anyway
# when it wants to: a caption-shaped ask at the 140-token cap the callers use
# spent all 140 tokens inside the thinking block and returned no text block at
# all. The reasoning those small asks produced ran 300 to 930 characters, so a
# thousand tokens of headroom carries it and the caller's cap keeps meaning
# what it says about the answer. A host told `disabled` outright is taken at
# its word and keeps the cap exact.
ANTHROPIC_REASONING_HEADROOM_TOKENS = 1024


def shape_for_provider(payload: dict, config: LLMConfig) -> None:
    """Apply the configured provider dialect before any auto-negotiation."""
    if config.max_tokens_param != "max_tokens" and "max_tokens" in payload:
        payload[config.max_tokens_param] = payload.pop("max_tokens")
    for name in config.drop_params:
        payload.pop(name, None)
    payload.update(config.extra_params)


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
    payload: dict, config: LLMConfig, thinking: bool, max_tokens: int, timeout: int
) -> int:
    """Put the reasoning switch and the tokens it will cost on one payload."""
    if thinking:
        # The dialect refuses any temperature but the default while reasoning.
        payload.pop("temperature", None)
        payload.update(_messages_reasoning(config.thinking_params))
        payload["max_tokens"] = max(max_tokens, THINKING_MIN_MAX_TOKENS)
        return max(timeout, THINKING_MIN_TIMEOUT_SECONDS)
    bulk = _messages_reasoning(config.no_thinking_params)
    payload.update(bulk)
    switch = bulk.get("thinking")
    if isinstance(switch, dict) and switch.get("type") != _REASONING_OFF:
        # A setting short of off is a request rather than a guarantee, so the
        # answer keeps the cap the caller asked for and the reasoning the host
        # does anyway gets its own room.
        payload["max_tokens"] = max_tokens + ANTHROPIC_REASONING_HEADROOM_TOKENS
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
    and a queued reasoning call is the one shape whose price is not halved.
    """
    resolved = resolved_llm_config(config)
    if resolved.provider == "anthropic":
        payload = anthropic_payload(prompt, resolved, DEFAULT_TEMPERATURE, max_tokens, ())
        apply_anthropic_reasoning(payload, resolved, False, max_tokens, 0)
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
