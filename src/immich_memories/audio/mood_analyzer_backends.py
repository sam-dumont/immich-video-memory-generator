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
            with open(path, "rb") as f:
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


class OpenAIMoodAnalyzer(MoodAnalyzer):
    """Mood analyzer using OpenAI GPT-4 Vision."""

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini",
        base_url: str = "https://api.openai.com/v1",
    ):
        """Initialize OpenAI analyzer.

        Args:
            api_key: OpenAI API key
            model: Model name (gpt-4o, gpt-4o-mini, gpt-4-vision-preview)
            base_url: API base URL (for Azure or compatible APIs)
        """
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=60.0,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
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
            with open(path, "rb") as f:
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
    ollama_url: str = "http://localhost:11434",
    ollama_model: str = "llava",
    openai_api_key: str | None = None,
    openai_model: str = "gpt-4.1-nano",
    openai_base_url: str = "https://api.openai.com/v1",
    prefer_local: bool = True,
) -> MoodAnalyzer:
    """Get a mood analyzer, preferring local Ollama if available.

    Args:
        ollama_url: Ollama API URL
        ollama_model: Ollama model name
        openai_api_key: OpenAI API key (fallback)
        openai_model: OpenAI model name
        openai_base_url: OpenAI API base URL (for Azure, on-prem, or compatible endpoints)
        prefer_local: Try Ollama first if True

    Returns:
        MoodAnalyzer instance

    Raises:
        RuntimeError: If no analyzer is available
    """
    if prefer_local:
        ollama = OllamaMoodAnalyzer(model=ollama_model, base_url=ollama_url)
        if await ollama.is_available():
            logger.info(f"Using Ollama ({ollama_model}) for mood analysis")
            return ollama
        else:
            logger.info("Ollama not available, checking OpenAI fallback")

    if openai_api_key:
        logger.info(f"Using OpenAI ({openai_model}) for mood analysis")
        return OpenAIMoodAnalyzer(
            api_key=openai_api_key, model=openai_model, base_url=openai_base_url
        )

    if not prefer_local:
        # Try Ollama as last resort
        ollama = OllamaMoodAnalyzer(model=ollama_model, base_url=ollama_url)
        if await ollama.is_available():
            logger.info(f"Using Ollama ({ollama_model}) for mood analysis")
            return ollama

    raise RuntimeError(
        "No mood analyzer available. Either:\n"
        "  1. Start Ollama locally with a vision model (ollama run llava)\n"
        "  2. Set OPENAI_API_KEY environment variable"
    )


async def get_mood_analyzer_from_config() -> MoodAnalyzer:
    """Get a mood analyzer using settings from config.

    Uses the shared LLM settings from config.llm for provider selection.

    Returns:
        MoodAnalyzer instance

    Raises:
        RuntimeError: If no analyzer is available
    """
    from immich_memories.config import get_config

    config = get_config()
    llm = config.llm

    # Determine preference based on provider setting
    prefer_local = llm.provider in ("ollama", "auto")

    return await get_mood_analyzer(
        ollama_url=llm.ollama_url,
        ollama_model=llm.ollama_model,
        openai_api_key=llm.openai_api_key or None,
        openai_model=llm.openai_model,
        openai_base_url=llm.openai_base_url,
        prefer_local=prefer_local,
    )
