"""LLM provider configuration for Immich Memories."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from immich_memories.config_models import expand_env_vars

# The generic reasoning control. "disabled" and "auto" are the two ends: never
# ask, and never say. The three levels in between are mapped to whatever the
# host takes: an effort on Claude, a level of its own on z.ai.
ThinkingLevel = Literal["disabled", "low", "high", "max", "auto"]

# Whether a fan-out stage may be queued instead of asked. Off is the default
# because a batch trades minutes of latency for half the price, and only a run
# nobody is waiting on can spend that.
BatchMode = Literal["off", "auto"]

_SWITCH_LEVELS = {"true": "high", "false": "disabled", "1": "high", "0": "disabled"}


class LLMConfig(BaseModel):
    """Shared LLM provider settings.

    Five accepted values, three code paths: "ollama" speaks the native Ollama
    API, "anthropic" speaks /v1/messages, and "openai-compatible", "openai" and
    "zai" all speak /v1/chat/completions — any server that does will work
    (OpenAI, Groq, mlx-vlm, vLLM).
    """

    provider: Literal["ollama", "openai-compatible", "openai", "zai", "anthropic"] = Field(
        default="openai-compatible",
        description=(
            "LLM provider. 'openai-compatible' covers any /chat/completions "
            "server (vLLM, mlx, Ollama's /v1, aggregators); 'openai' and "
            "'zai' are that same adapter with the provider's URL and "
            "reasoning dialect preset; 'anthropic' speaks the native "
            "/v1/messages API (Claude, or z.ai's Anthropic endpoint); "
            "'ollama' is the native Ollama generate API."
        ),
    )
    base_url: str = Field(
        default="http://localhost:8080/v1",
        description="API base URL",
    )
    model: str = Field(
        default="",
        description="Model name",
    )
    api_key: str = Field(
        default="",
        description="API key (optional, only needed for cloud APIs)",
    )
    timeout_seconds: int = Field(
        default=300,
        ge=10,
        le=3600,
        description="HTTP timeout for LLM requests in seconds (increase for slow local models)",
    )
    thinking: ThinkingLevel = Field(
        default="disabled",
        description=(
            "How hard the server may reason. 'disabled' never asks; 'low', "
            "'high' and 'max' run load-bearing calls (selection review, "
            "titles) in reasoning mode while bulk analysis stays fast; 'auto' "
            "sends no reasoning field at all and takes the host's default. "
            "The level reaches hosts that take one: Claude gets adaptive "
            "thinking and the level as an effort, z.ai gets the level itself. "
            "Elsewhere it is on or off and 'thinking_params' carries the "
            "dialect. The old 'true' and 'false' are still read, as 'high' "
            "and 'disabled'."
        ),
    )
    thinking_params: dict[str, Any] = Field(
        default_factory=lambda: {"chat_template_kwargs": {"enable_thinking": True}},
        description=(
            "Request fields merged into a thinking call. Default is the Qwen "
            "dialect (vLLM/mlx); OpenAI wants {'reasoning_effort': 'medium'}."
        ),
    )
    no_thinking_params: dict[str, Any] = Field(
        default_factory=lambda: {"chat_template_kwargs": {"enable_thinking": False}},
        description=(
            "Request fields merged into a NON-thinking call on a server whose "
            "chat template reasons by default. Omitting the enable switch does "
            "not disable it: bulk analysis then reasons at its small token "
            "budget, truncates mid-thought and returns nothing parseable. "
            "Servers that reason only when asked want {}. z.ai takes a level "
            "instead of a switch, {'thinking': {'type': ...}} with one of "
            "disabled, low, high, max; the preset picks the lowest level the "
            "model accepts, and a key set here wins over it."
        ),
    )
    max_tokens_param: str = Field(
        default="max_tokens",
        description=(
            "Name of the token-limit request field. OpenAI reasoning models "
            "want 'max_completion_tokens' (auto-negotiated if left default)."
        ),
    )
    drop_params: list[str] = Field(
        default_factory=list,
        description="Request fields to omit for servers that reject them (e.g. ['temperature']).",
    )
    extra_params: dict[str, Any] = Field(
        default_factory=dict,
        description="Request fields merged into every call, for provider-specific requirements.",
    )
    send_image_detail: bool = Field(
        default=True,
        description=(
            "Include OpenAI's optional image_url.detail field. Disable for compatible APIs "
            "whose strict vision schema accepts only image_url.url."
        ),
    )
    batch: BatchMode = Field(
        default="off",
        description=(
            "Whether a reading stage may hand its independent prompts to the "
            "provider's batch route instead of asking them one at a time. "
            "'auto' does so when the provider declares such a route and the "
            "stage has at least 'batch_min_requests' prompts that do not read "
            "each other's answers. OpenAI and Anthropic both charge half for "
            "it and answer within the day rather than within seconds, so it "
            "suits an unattended run and not a run someone is watching."
        ),
    )
    batch_min_requests: int = Field(
        default=8,
        ge=2,
        le=10_000,
        description=(
            "Fewest independent prompts in one stage worth a batch. Below this "
            "the asking is quicker than the queueing and the stage stays realtime."
        ),
    )
    batch_max_wait_minutes: int = Field(
        default=60,
        ge=1,
        le=1440,
        description=(
            "How long a stage waits for a batch before giving up on it. "
            "Whatever the provider has not answered by then is asked again in "
            "real time, so a slow queue costs a run its discount and not its run."
        ),
    )

    @property
    def reasons(self) -> bool:
        """Whether this server may reason at all when a load-bearing call asks."""
        return self.thinking != "disabled"

    @field_validator("thinking", mode="before")
    @classmethod
    def level_from_switch(cls, v: object) -> object:
        """Read the on/off switch this field used to be as the level it meant."""
        if isinstance(v, bool):
            return "high" if v else "disabled"
        if isinstance(v, str):
            return _SWITCH_LEVELS.get(v.strip().lower(), v)
        return v

    @field_validator("api_key", mode="before")
    @classmethod
    def expand_env(cls, v: str) -> str:
        """Expand environment variables in config values."""
        if isinstance(v, str):
            return expand_env_vars(v)
        return v
