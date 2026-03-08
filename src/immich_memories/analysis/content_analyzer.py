"""Content analysis for video segments using vision LLMs.

Uses Ollama (local) or OpenAI to analyze video frames and describe
what's happening, rate interestingness, and detect activities.

Base class and parsing live in ``_content_parsing``; provider
implementations live in ``_content_providers``.  This module provides
factory functions and re-exports for backwards compatibility.
"""

from __future__ import annotations

import logging

from immich_memories.analysis._content_parsing import (  # noqa: F401
    CONTENT_ANALYSIS_PROMPT,
    ContentAnalysis,
    ContentAnalyzer,
    ContentParsingMixin,
)
from immich_memories.analysis._content_providers import (  # noqa: F401
    OllamaContentAnalyzer,
    OpenAIContentAnalyzer,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------


def get_content_analyzer(
    ollama_url: str = "http://localhost:11434",
    ollama_model: str = "llava",
    openai_api_key: str = "",
    openai_model: str = "gpt-4.1-nano",
    openai_base_url: str = "https://api.openai.com/v1",
    openai_image_detail: str = "low",
    max_height: int = 480,
    provider: str = "auto",
    ollama_num_ctx: int = 4096,
) -> ContentAnalyzer | None:
    """Get best available content analyzer.

    Prefers Ollama (local, privacy-friendly), falls back to OpenAI.

    Args:
        ollama_url: Ollama server URL.
        ollama_model: Ollama model name.
        openai_api_key: OpenAI API key.
        openai_model: OpenAI model name.
        openai_base_url: OpenAI API base URL (for Azure, Groq, or compatible endpoints).
        openai_image_detail: Image detail level for OpenAI ("low"=85 tokens, "high"=1889 tokens).
        max_height: Maximum frame height in pixels (default 480 for speed/cost).
        provider: Provider preference ("ollama", "openai", "auto").
        ollama_num_ctx: Context window size for Ollama (default 4096).

    Returns:
        ContentAnalyzer instance or None if no analyzer available.
    """
    # If explicit OpenAI preference
    if provider == "openai" and openai_api_key:
        openai = OpenAIContentAnalyzer(
            api_key=openai_api_key,
            model=openai_model,
            base_url=openai_base_url,
            image_detail=openai_image_detail,
            max_height=max_height,
        )
        logger.info(
            f"Using OpenAI for content analysis "
            f"(model: {openai_model}, detail: {openai_image_detail})"
        )
        return openai

    # Try Ollama first (for "ollama" or "auto")
    ollama = OllamaContentAnalyzer(
        model=ollama_model,
        base_url=ollama_url,
        max_height=max_height,
        num_ctx=ollama_num_ctx,
    )
    if ollama.is_available():
        logger.info(
            f"Using Ollama for content analysis (model: {ollama_model}, num_ctx: {ollama_num_ctx})"
        )
        return ollama

    # Fall back to OpenAI
    if openai_api_key:
        openai = OpenAIContentAnalyzer(
            api_key=openai_api_key,
            model=openai_model,
            base_url=openai_base_url,
            image_detail=openai_image_detail,
            max_height=max_height,
        )
        logger.info(
            f"Using OpenAI for content analysis "
            f"(model: {openai_model}, detail: {openai_image_detail})"
        )
        return openai

    logger.info("No content analyzer available (Ollama not running, no OpenAI key)")
    return None


def get_content_analyzer_from_config() -> ContentAnalyzer | None:
    """Get a content analyzer using settings from config.

    Uses the shared LLM settings from config.llm for provider selection.

    Returns:
        ContentAnalyzer instance or None if no analyzer available.
    """
    from immich_memories.config import get_config

    config = get_config()

    # Check if content analysis is enabled
    if not config.content_analysis.enabled:
        logger.info("Content analysis is disabled in config")
        return None

    llm = config.llm
    ca = config.content_analysis

    return get_content_analyzer(
        ollama_url=llm.ollama_url,
        ollama_model=llm.ollama_model,
        openai_api_key=llm.openai_api_key,
        openai_model=llm.openai_model,
        openai_base_url=llm.openai_base_url,
        openai_image_detail=ca.openai_image_detail,
        max_height=ca.frame_max_height,
        provider=llm.provider,
    )
