"""Concrete mood analyzer backends (Ollama, OpenAI) and factory functions."""

from __future__ import annotations

import base64
import contextlib
import logging
from pathlib import Path

import httpx

from immich_memories.audio.mood_analyzer import (
    MOOD_ANALYSIS_PROMPT,
    MoodAnalyzer,
    VideoMood,
)
from immich_memories.config_models import LLMConfig

logger = logging.getLogger(__name__)


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

    @property
    def client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=120.0)
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
        # Encode images to base64
        images = []
        for path in frame_paths[:4]:  # Limit to 4 images
            with path.open("rb") as f:
                images.append(base64.b64encode(f.read()).decode("utf-8"))

        payload = {
            "model": self.model,
            "prompt": MOOD_ANALYSIS_PROMPT,
            "images": images,
            "stream": False,
            "options": {
                "temperature": 0.3,
            },
        }

        try:
            response = await self.client.post(
                f"{self.base_url}/api/generate",
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
            return self._parse_mood_response(data.get("response", ""))

        except httpx.HTTPError as e:
            logger.error(f"Ollama API error: {e}")
            raise


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
        self._client: httpx.AsyncClient | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None or self._client.is_closed:
            headers: dict[str, str] = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            self._client = httpx.AsyncClient(
                timeout=120.0,
                headers=headers,
            )
        return self._client

    async def close(self):
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

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
        # Build content with images
        content = [{"type": "text", "text": MOOD_ANALYSIS_PROMPT}]

        for path in frame_paths[:4]:  # Limit to 4 images
            with path.open("rb") as f:
                image_data = base64.b64encode(f.read()).decode("utf-8")
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{image_data}",
                            "detail": "low",
                        },
                    }
                )

        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": content}],
            "max_tokens": 500,
            "temperature": 0.3,
        }

        try:
            response = await self.client.post(
                f"{self.base_url}/chat/completions",
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
            message = data["choices"][0]["message"]["content"]
            return self._parse_mood_response(message)

        except httpx.HTTPError as e:
            logger.error(f"OpenAI API error: {e}")
            raise


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
