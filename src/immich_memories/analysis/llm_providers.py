"""Named LLM providers: the URL they answer on, the dialect they speak, the way they reason.

Two adapters carry every host: `/v1/messages` and `/chat/completions`. A named
provider is one of those adapters with a vendor's base URL and reasoning switch
filled in, applied only where the user left the field at its default. Deciding
which endpoint and which reasoning field happens here and nowhere else, so the
transport in `llm_query` only has to speak the two dialects.
"""

from __future__ import annotations

import re
from ipaddress import ip_address
from urllib.parse import urlsplit

from immich_memories.config_models_llm import LLMConfig

# z.ai's reasoning switch is a level, not a boolean: disabled, low, high, max.
# Measured 2026-09-14 on /api/paas/v4, glm-5.3-flash answers a request carrying
# {"type": "disabled"} with HTTP 400 code 1210, "This model always engages in
# thinking and cannot be disabled; please use low, high, or max", which names
# the levels it will take instead.
LOWEST_THINKING_LEVEL = "low"

# The Messages API version every host on this adapter is pinned to. Anthropic
# requires the header; the compatible hosts ignore one they do not know rather
# than refusing the request.
ANTHROPIC_VERSION = "2023-06-01"

# The three settings that ask for reasoning. They are also the words Claude
# takes as an effort and z.ai takes as a level, which is why one tuple answers
# for both hosts.
_ASKING_LEVELS = ("low", "high", "max")

# Named providers = the generic adapter plus the provider's URL and reasoning
# dialect, applied only where the user left the field at its default.
_PROVIDER_PRESETS: dict[str, dict] = {
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "thinking_params": {"reasoning_effort": "medium"},
        # Older GPT-5 models cannot turn reasoning off. Models with a verified
        # off switch override this fallback in _openai_reasoning below.
        "no_thinking_params": {"reasoning_effort": "minimal"},
        "always_reasons": True,
    },
    "anthropic": {
        "base_url": "https://api.anthropic.com",
        "no_thinking_params": {"thinking": {"type": "disabled"}},
        # Claude answers a request carrying a temperature with a 400 from the
        # 4.7 line on, so the greedy decoding every other provider gets is not
        # on offer there. Nothing is lost that could have been had.
        "drop_params": ["temperature"],
    },
    "zai": {
        "base_url": "https://api.z.ai/api/anthropic",
        "send_image_detail": False,
    },
}

# The whole GLM-5 line reasons unconditionally, so the cheapest level it
# accepts is what "do not reason" has to mean there; older lines take
# "disabled".
_ALWAYS_REASONING_MODELS = re.compile(r"^glm-5(\.\d+)?(-|$)")


def _openai_reasoning(config: LLMConfig) -> dict:
    """Use the model's off switch where it has been verified to accept one."""
    # Verified 2026-09-15: Luna rejects "minimal" with unsupported_value and
    # defaults to medium when the field is removed; "none" bills no reasoning.
    # Include pinned snapshots without extending this claim to other models.
    if re.fullmatch(r"gpt-5\.6-luna(?:-\d{4}-\d{2}-\d{2})?", config.model.strip().lower()):
        effort = config.no_thinking_params.get("reasoning_effort", "none")
        # Mirror request shaping: auto omits the switch, drops remove it, and
        # extra_params has the final word. Only an effective "none" means the
        # caller's token cap can be spent entirely on the answer.
        if config.thinking == "auto" or "reasoning_effort" in config.drop_params:
            effort = None
        effort = config.extra_params.get("reasoning_effort", effort)
        return {
            "no_thinking_params": {"reasoning_effort": "none"},
            "always_reasons": effort != "none",
        }
    return {}


def _zai_off_level(model: str) -> str:
    """The thinking level that stands in for "off" on one z.ai model."""
    return (
        LOWEST_THINKING_LEVEL
        if _ALWAYS_REASONING_MODELS.match(model.strip().lower())
        else "disabled"
    )


def _anthropic_reasoning(config: LLMConfig) -> dict:
    """Claude reasons adaptively, and the level it reasons at is an effort.

    The fixed `budget_tokens` of the older dialect is refused with HTTP 400 from
    the 4.7 line on, as is any sampling parameter. A host still serving that
    older shape is reached by writing `thinking_params` out by hand.
    """
    if config.thinking not in _ASKING_LEVELS:
        return {}
    return {
        "thinking_params": {
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": config.thinking},
        }
    }


def _zai_reasoning(config: LLMConfig) -> dict:
    """z.ai takes a level in both directions, and GLM-5 refuses the "off" one."""
    asking = config.thinking if config.thinking in _ASKING_LEVELS else LOWEST_THINKING_LEVEL
    return {
        "thinking_params": {"thinking": {"type": asking}},
        "no_thinking_params": {"thinking": {"type": _zai_off_level(config.model)}},
    }


_REASONING_BY_PROVIDER = {
    "openai": _openai_reasoning,
    "zai": _zai_reasoning,
    "anthropic": _anthropic_reasoning,
}


def _preset_for(config: LLMConfig) -> dict | None:
    """The named provider's preset, with the model- and level-dependent parts filled in."""
    preset = _PROVIDER_PRESETS.get(config.provider)
    reasoning = _REASONING_BY_PROVIDER.get(config.provider)
    if preset is None or reasoning is None:
        return preset
    return {**preset, **reasoning(config)}


def _dialect_for(provider: str, base_url: str) -> str:
    """The adapter a named provider speaks, given the URL it will actually use.

    z.ai serves both dialects on one host, so there the base URL's path picks:
    `.../api/anthropic` wants `/v1/messages`; `.../api/paas/v4` wants
    `/chat/completions`. Posting the OpenAI path to the Anthropic base gets a
    200 carrying `{"code":500,"msg":"404 NOT_FOUND"}`.
    """
    if provider == "anthropic":
        return "anthropic"
    if provider != "zai":
        return "openai-compatible"
    path = urlsplit(base_url).path.rstrip("/")
    return "anthropic" if path.endswith("/anthropic") else "openai-compatible"


def _preset_updates(config: LLMConfig) -> dict:
    preset = _preset_for(config)
    if preset is None:
        return {}
    fields = type(config).model_fields
    updates: dict = {}
    for name, value in preset.items():
        default = fields[name].get_default(call_default_factory=True)
        if getattr(config, name) == default:
            updates[name] = value
        elif name in ("thinking_params", "no_thinking_params"):
            # The provider's own reasoning switch is not interchangeable with the
            # generic one it was replaced by, so both are sent. A key the user
            # named themselves still wins, which is how a z.ai thinking level
            # other than the preset's is chosen.
            updates[name] = {**value, **getattr(config, name)}
    updates["provider"] = _dialect_for(config.provider, updates.get("base_url", config.base_url))
    return updates


# The batch route each dialect's hosts declare, where the dialect has one at
# all. Declared per dialect rather than per vendor because the batch shapes were
# copied wholesale alongside the realtime ones: Melious answers OpenAI's
# /v1/batches on a base URL configured as `openai-compatible`. A host that never
# copied it answers 404, which is what the probe in `llm_batch` is for -- z.ai's
# Anthropic route does exactly that (measured 2026-09-14). Ollama has no batch
# shape of any kind, so it declares nothing.
_BATCH_ROUTES = {"openai-compatible": "openai", "anthropic": "anthropic"}


def batch_route_for(config: LLMConfig) -> str | None:
    """The batch route this provider declares, before any host has been asked."""
    return _BATCH_ROUTES.get(resolved_llm_config(config).provider)


def resolved_llm_config(config: LLMConfig) -> LLMConfig:
    """Return the provider configuration that will actually reach the wire."""
    updates = _preset_updates(config)
    if config.thinking == "auto":
        # Leaving it to the host is a request field that is not there, in
        # either direction: nothing turning reasoning on, nothing turning it off.
        updates["thinking_params"] = {}
        updates["no_thinking_params"] = {}
    return config.model_copy(update=updates) if updates else config


# What the reader may overlap when the config names no number. A hosted endpoint
# is a fleet and answers four as easily as one; a model on this machine or this
# private network is one process in front of one accelerator, and four sockets
# there buy nothing but three requests queued inside a 300-second timeout, which
# fails quietly rather than loudly. Four is deliberately modest: it is the
# smallest number that overlaps at all on the stages that can overlap, and small
# enough that a provider's per-key rate window absorbs it.
HOSTED_READER_CONCURRENCY = 4
LOCAL_READER_CONCURRENCY = 1

# The host names that mean "this very machine", including Docker's name for the
# engine's host, which is how a containerised run reaches a model server on the
# Mac it is running on.
_SAME_MACHINE = frozenset(
    {
        "localhost",
        "0.0.0.0",  # noqa: S104 - read from a URL, never bound to
        "host.docker.internal",
        "host.containers.internal",
    }
)


def _reachable_only_from_here(host: str) -> bool:
    """Whether this endpoint is a model we share a machine or a private network with.

    A bare name with no dot is a container or service name, which cannot be a public
    endpoint; a literal address is judged by its range. Anything that resolves off this
    network is somebody's fleet. Deciding from the URL is the only fact available without
    a DNS lookup, and it errs toward serial, which is the direction that cannot cost a run.
    """
    name = host.strip("[]").lower()
    if not name or name in _SAME_MACHINE or "." not in name.replace(":", ""):
        return True
    try:
        address = ip_address(name)
    except ValueError:
        return False
    return address.is_loopback or address.is_private or address.is_link_local


def reader_concurrency(config: LLMConfig) -> int:
    """How many independent reader jobs this endpoint is worth asking at once."""
    if config.reader_concurrency is not None:
        return config.reader_concurrency
    host = urlsplit(resolved_llm_config(config).base_url).hostname or ""
    return (
        LOCAL_READER_CONCURRENCY if _reachable_only_from_here(host) else HOSTED_READER_CONCURRENCY
    )
