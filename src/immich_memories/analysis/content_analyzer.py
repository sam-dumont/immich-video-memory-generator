"""Content analysis for video segments using vision LLMs.

Uses Ollama or any OpenAI-compatible server (mlx-vlm, vLLM, Groq, etc.)
to analyze video frames and describe what's happening, rate interestingness,
and detect activities.

Base class and parsing live in ``llm_response_parser``; provider
implementations live in ``_content_providers``.  This module provides
factory functions and re-exports.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from immich_memories.config_models import ContentAnalysisConfig, LLMConfig

from immich_memories.analysis._content_providers import (  # noqa: F401
    OllamaContentAnalyzer,
    OpenAICompatibleContentAnalyzer,
)
from immich_memories.analysis.llm_response_parser import (  # noqa: F401
    CONTENT_ANALYSIS_PROMPT,
    ContentAnalysis,
    ContentAnalyzer,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------


def get_content_analyzer(
    provider: str = "openai-compatible",
    base_url: str = "http://localhost:8080/v1",
    model: str = "",
    api_key: str = "",
    image_detail: str = "low",
    max_height: int = 480,
    num_ctx: int = 4096,
    timeout: float = 300.0,
) -> ContentAnalyzer | None:
    """Get content analyzer for the configured provider.

    Args:
        provider: "ollama" or "openai-compatible".
        base_url: API base URL.
        model: Model name.
        api_key: API key (only needed for cloud APIs).
        image_detail: Image detail level for OpenAI-compatible.
        max_height: Maximum frame height in pixels.
        num_ctx: Context window size (Ollama only).
        timeout: HTTP request timeout in seconds.

    Returns:
        ContentAnalyzer instance or None if provider is unknown.
    """
    if provider == "ollama":
        ollama = OllamaContentAnalyzer(
            model=model,
            base_url=base_url,
            max_height=max_height,
            num_ctx=num_ctx,
            timeout=timeout,
        )
        logger.info(f"Using Ollama for content analysis (model: {model}, num_ctx: {num_ctx})")
        return ollama

    if provider == "openai-compatible":
        openai_compat = OpenAICompatibleContentAnalyzer(
            model=model,
            base_url=base_url,
            api_key=api_key,
            image_detail=image_detail,
            max_height=max_height,
            timeout=timeout,
        )
        logger.info(
            f"Using OpenAI-compatible for content analysis (model: {model}, url: {base_url})"
        )
        return openai_compat

    logger.warning(f"Unknown LLM provider: {provider}")
    return None


def get_content_analyzer_from_config(
    content_analysis_config: ContentAnalysisConfig | None = None,
    llm_config: LLMConfig | None = None,
) -> ContentAnalyzer | None:
    """Get a content analyzer using settings from config.

    Uses the shared LLM settings from config.llm for provider selection.

    Args:
        content_analysis_config: ContentAnalysisConfig instance. Falls back to get_config().
        llm_config: LLMConfig instance. Falls back to get_config().

    Returns:
        ContentAnalyzer instance or None if no analyzer available.
    """
    if content_analysis_config is None or llm_config is None:
        from immich_memories.config import get_config

        config = get_config()
        if content_analysis_config is None:
            content_analysis_config = config.content_analysis
        if llm_config is None:
            llm_config = config.llm

    if not content_analysis_config.enabled:
        logger.info("Content analysis is disabled in config")
        return None

    return get_content_analyzer(
        provider=llm_config.provider,
        base_url=llm_config.base_url,
        model=llm_config.model,
        api_key=llm_config.api_key,
        image_detail=content_analysis_config.openai_image_detail,
        max_height=content_analysis_config.frame_max_height,
        timeout=float(llm_config.timeout_seconds),
    )
