"""Concrete mood analyzer backends (Ollama, OpenAI) and factory functions."""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path

import httpx

from immich_memories.analysis.llm_query import build_llm_timeout, query_llm
from immich_memories.audio.mood_analyzer import (
    MOOD_ANALYSIS_PROMPT,
    MoodAnalyzer,
    VideoMood,
)
from immich_memories.config_models_llm import LLMConfig

logger = logging.getLogger(__name__)

# The prompt asks for a short JSON object, and more keyframes than this stop
# telling the model anything new about how a video feels.
_MAX_MOOD_FRAMES = 4
_MOOD_ANSWER_TOKENS = 500


async def _ask_for_mood(frame_paths: list[Path], config: LLMConfig) -> str:
    """Send the keyframes to the configured model and return what it says.

    Never asks for thinking: mood is a cheap read of a handful of stills, and
    reasoning over several images at once is a measured runaway.
    """
    return await query_llm(
        MOOD_ANALYSIS_PROMPT,
        config,
        temperature=0.3,
        max_tokens=_MOOD_ANSWER_TOKENS,
        timeout_seconds=config.timeout_seconds,
        images=[path.read_bytes() for path in frame_paths[:_MAX_MOOD_FRAMES]],
    )


class OllamaMoodAnalyzer(MoodAnalyzer):
    """Mood analyzer using local Ollama with vision models."""

    def __init__(
        self,
        model: str = "llava",
        base_url: str = "http://localhost:11434",
    ):
        """Initialize Ollama analyzer.

        Args:
            model: Ollama model name (llava, bakllava, llava-llama3, etc.)
            base_url: Ollama API base URL
        """
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None
        self._llm_config = LLMConfig(provider="ollama", base_url=self.base_url, model=model)

    @property
    def client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client used by the availability probe."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=build_llm_timeout(float(self._llm_config.timeout_seconds))
            )
        return self._client

    async def close(self):
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def is_available(self) -> bool:
        """Check if Ollama is available."""
        try:
            response = await self.client.get(f"{self.base_url}/api/tags")
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    async def analyze_video(
        self,
        video_path: Path,
        num_keyframes: int = 5,
    ) -> VideoMood:
        """Analyze video by extracting and analyzing keyframes."""
        frames = self.extract_keyframes(video_path, num_keyframes)

        if not frames:
            logger.warning("No keyframes extracted, using default mood")
            return VideoMood(
                primary_mood="calm",
                genre_suggestions=["ambient"],
                confidence=0.3,
            )

        try:
            return await self.analyze_frames(frames)
        finally:
            # Cleanup temporary frames
            for frame in frames:
                with contextlib.suppress(OSError):
                    frame.unlink()

    async def analyze_frames(
        self,
        frame_paths: list[Path],
    ) -> VideoMood:
        """Analyze frames using Ollama vision model."""
        return self._parse_mood_response(await _ask_for_mood(frame_paths, self._llm_config))


class OpenAICompatibleMoodAnalyzer(MoodAnalyzer):
    """Mood analyzer using any OpenAI-compatible API (OpenAI, local servers, etc.)."""

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        base_url: str = "https://api.openai.com/v1",
        api_key: str = "",
    ):
        """Initialize OpenAI-compatible analyzer.

        Args:
            model: Model name
            base_url: API base URL (OpenAI, Azure, local server, etc.)
            api_key: API key (optional for local servers)
        """
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._llm_config = LLMConfig(
            provider="openai-compatible",
            base_url=self.base_url,
            model=model,
            api_key=api_key,
        )

    async def analyze_video(
        self,
        video_path: Path,
        num_keyframes: int = 5,
    ) -> VideoMood:
        """Analyze video by extracting and analyzing keyframes."""
        frames = self.extract_keyframes(video_path, num_keyframes)

        if not frames:
            logger.warning("No keyframes extracted, using default mood")
            return VideoMood(
                primary_mood="calm",
                genre_suggestions=["ambient"],
                confidence=0.3,
            )

        try:
            return await self.analyze_frames(frames)
        finally:
            # Cleanup temporary frames
            for frame in frames:
                with contextlib.suppress(OSError):
                    frame.unlink()

    async def analyze_frames(
        self,
        frame_paths: list[Path],
    ) -> VideoMood:
        """Analyze frames using OpenAI Vision."""
        return self._parse_mood_response(await _ask_for_mood(frame_paths, self._llm_config))


async def get_mood_analyzer(
    provider: str = "openai-compatible",
    base_url: str = "http://localhost:8080/v1",
    model: str = "",
    api_key: str = "",
) -> MoodAnalyzer:
    """Get a mood analyzer for the given provider.

    Args:
        provider: LLM provider ("ollama" or "openai-compatible")
        base_url: API base URL
        model: Model name
        api_key: API key (optional for local servers)

    Returns:
        MoodAnalyzer instance

    Raises:
        RuntimeError: If provider is unknown or unavailable
    """
    if provider == "ollama":
        ollama = OllamaMoodAnalyzer(model=model, base_url=base_url)
        if await ollama.is_available():
            logger.info(f"Using Ollama ({model}) for mood analysis")
            return ollama
        raise RuntimeError(f"Ollama not available at {base_url}")

    if provider == "openai-compatible":
        logger.info(f"Using OpenAI-compatible ({model}) for mood analysis")
        return OpenAICompatibleMoodAnalyzer(model=model, base_url=base_url, api_key=api_key)

    raise RuntimeError(f"Unknown LLM provider: {provider}")


async def get_mood_analyzer_from_config(llm_config: LLMConfig) -> MoodAnalyzer:
    """Get a mood analyzer using settings from config.

    Uses the shared LLM settings for provider selection.

    Args:
        llm_config: LLM configuration with provider, base_url, model, api_key.

    Returns:
        MoodAnalyzer instance

    Raises:
        RuntimeError: If no analyzer is available
    """
    return await get_mood_analyzer(
        provider=llm_config.provider,
        base_url=llm_config.base_url,
        model=llm_config.model,
        api_key=llm_config.api_key,
    )
