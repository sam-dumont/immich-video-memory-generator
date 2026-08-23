"""LLM provider configuration for Immich Memories."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from immich_memories.config_models import expand_env_vars


class LLMConfig(BaseModel):
    """Shared LLM provider settings.

    Two providers: "ollama" (native Ollama API) or "openai-compatible"
    (any server speaking /v1/chat/completions — OpenAI, Groq, mlx-vlm, vLLM, etc.).
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
    thinking: bool = Field(
        default=False,
        description=(
            "Server supports a reasoning switch. When on, load-bearing calls "
            "(selection review, titles) run the model in reasoning mode; bulk "
            "analysis stays fast."
        ),
    )
    thinking_params: dict[str, Any] = Field(
        default_factory=lambda: {"chat_template_kwargs": {"enable_thinking": True}},
        description=(
            "Request fields merged into a thinking call. Default is the Qwen "
            "dialect (vLLM/mlx); OpenAI wants {'reasoning_effort': 'medium'}."
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

    @field_validator("api_key", mode="before")
    @classmethod
    def expand_env(cls, v: str) -> str:
        """Expand environment variables in config values."""
        if isinstance(v, str):
            return expand_env_vars(v)
        return v
