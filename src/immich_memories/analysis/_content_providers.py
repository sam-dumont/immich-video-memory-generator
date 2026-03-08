"""Provider implementations for content analysis.

Contains OllamaContentAnalyzer and OpenAIContentAnalyzer,
which use local Ollama or OpenAI-compatible APIs respectively.
"""

from __future__ import annotations

import base64
import contextlib
import logging
from pathlib import Path

import httpx

from immich_memories.analysis._content_parsing import (
    CONTENT_ANALYSIS_PROMPT,
    ContentAnalysis,
    ContentAnalyzer,
)

logger = logging.getLogger(__name__)


class OllamaContentAnalyzer(ContentAnalyzer):
    """Content analyzer using local Ollama with vision models."""

    # Models with smaller context windows that need fewer images
    # Moondream uses ~729 tokens per image, so 1 image is safe for 2048 context
    SINGLE_IMAGE_MODELS = {"moondream", "moondream2"}

    def __init__(
        self,
        model: str = "llava",
        base_url: str = "http://localhost:11434",
        max_height: int = 480,
        num_ctx: int = 4096,
    ):
        """Initialize Ollama analyzer.

        Args:
            model: Ollama model name (llava, bakllava, moondream, qwen2-vl, etc.)
            base_url: Ollama API base URL
            max_height: Maximum frame height in pixels (default 480 for speed)
            num_ctx: Context window size for Ollama (default 4096)
        """
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.max_height = max_height
        self.num_ctx = num_ctx
        self._client: httpx.Client | None = None

        # Check if this model needs single image mode (small context)
        model_base = model.split(":")[0].lower()
        self.single_image_mode = model_base in self.SINGLE_IMAGE_MODELS
        if self.single_image_mode:
            logger.info("Moondream detected: using single image mode to fit context window")

    @property
    def client(self) -> httpx.Client:
        """Get or create HTTP client."""
        if self._client is None or self._client.is_closed:
            # Increased timeout for slower vision models (was 120s, now 300s)
            self._client = httpx.Client(timeout=300.0)
        return self._client

    def close(self):
        """Close the HTTP client."""
        if self._client:
            self._client.close()
            self._client = None

    def is_available(self) -> bool:
        """Check if Ollama is available."""
        try:
            response = self.client.get(f"{self.base_url}/api/tags")
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    def analyze_segment(
        self,
        video_path: Path,
        start_time: float = 0,
        end_time: float | None = None,
        num_frames: int = 3,
    ) -> ContentAnalysis:
        """Analyze a video segment using Ollama vision model.

        Args:
            video_path: Path to video file.
            start_time: Segment start time in seconds.
            end_time: Segment end time in seconds.
            num_frames: Number of frames to analyze.

        Returns:
            ContentAnalysis with description and scores.
        """
        # For models with small context (Moondream: ~729 tokens/image),
        # use 2 images max: 2x729 + ~400 prompt = ~1858 tokens < 2048 limit
        if self.single_image_mode:
            actual_frames = min(2, num_frames)
            max_images = 2
        else:
            actual_frames = num_frames
            max_images = 4

        frames = self.extract_frames(
            video_path, start_time, end_time, actual_frames, max_height=self.max_height
        )

        if not frames:
            logger.debug("No frames extracted for content analysis")
            return ContentAnalysis(confidence=0.0)

        try:
            # Encode images to base64
            images = []
            for path in frames[:max_images]:
                with open(path, "rb") as f:
                    images.append(base64.b64encode(f.read()).decode("utf-8"))

            payload = {
                "model": self.model,
                "prompt": CONTENT_ANALYSIS_PROMPT,
                "images": images,
                "stream": False,
                "options": {
                    "temperature": 0.3,
                    "num_ctx": self.num_ctx,  # Explicitly set context size
                },
            }

            # Retry up to 2 times if model returns empty response
            max_retries = 2
            for attempt in range(max_retries + 1):
                response = self.client.post(
                    f"{self.base_url}/api/generate",
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()

                raw_response = data.get("response", "")
                prompt_tokens = data.get("prompt_eval_count", 0)
                completion_tokens = data.get("eval_count", 0)

                # Check if response is too short (model failure)
                if completion_tokens <= 5 or len(raw_response.strip()) < 10:
                    if attempt < max_retries:
                        logger.warning(
                            f"Model returned near-empty response (attempt {attempt + 1}/{max_retries + 1}), "
                            f"retrying... (tokens: {completion_tokens}, len: {len(raw_response)})"
                        )
                        continue
                    else:
                        logger.warning(
                            f"Model failed after {max_retries + 1} attempts, using fallback"
                        )
                        # Return a low-confidence fallback instead of empty
                        result = ContentAnalysis(
                            description="(analysis unavailable)",
                            interestingness=0.5,
                            quality=0.5,
                            confidence=0.2,
                        )
                        self._log_analysis_result(
                            result, prompt_tokens, completion_tokens, len(images)
                        )
                        return result

                # Parse the response
                result = self._parse_content_response(raw_response)

                # Log result with token tracking
                self._log_analysis_result(
                    result,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    num_images=len(images),
                )

                return result

            # Should never reach here, but just in case
            return ContentAnalysis(confidence=0.0)

        except httpx.HTTPError as e:
            logger.warning(f"Ollama API error: {e}")
            return ContentAnalysis(confidence=0.0)

        finally:
            # Cleanup temporary frames
            for frame in frames:
                with contextlib.suppress(OSError):
                    frame.unlink()


class OpenAIContentAnalyzer(ContentAnalyzer):
    """Content analyzer using OpenAI GPT-4 Vision (also works with Groq)."""

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini",
        base_url: str = "https://api.openai.com/v1",
        image_detail: str = "low",
        max_height: int = 480,
    ):
        """Initialize OpenAI analyzer.

        Args:
            api_key: OpenAI API key (or Groq key for Groq endpoint)
            model: Model name (gpt-4o, gpt-4o-mini, gpt-4.1-nano, llama-4-scout, etc.)
            base_url: API base URL (change to https://api.groq.com/openai/v1 for Groq)
            image_detail: Image detail level ("low"=85 tokens, "high"=1889 tokens, "auto")
            max_height: Maximum frame height in pixels (default 480 for speed/cost)
        """
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.image_detail = image_detail
        self.max_height = max_height
        self._client: httpx.Client | None = None

    @property
    def client(self) -> httpx.Client:
        """Get or create HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.Client(
                timeout=60.0,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
            )
        return self._client

    def close(self):
        """Close the HTTP client."""
        if self._client:
            self._client.close()
            self._client = None

    def is_available(self) -> bool:
        """Check if OpenAI is available."""
        return bool(self.api_key)

    def analyze_segment(
        self,
        video_path: Path,
        start_time: float = 0,
        end_time: float | None = None,
        num_frames: int = 3,
    ) -> ContentAnalysis:
        """Analyze a video segment using OpenAI vision model."""
        frames = self.extract_frames(
            video_path, start_time, end_time, num_frames, max_height=self.max_height
        )

        if not frames:
            logger.debug("No frames extracted for content analysis")
            return ContentAnalysis(confidence=0.0)

        try:
            # Build content with images
            content: list[dict] = [{"type": "text", "text": CONTENT_ANALYSIS_PROMPT}]

            for path in frames[:4]:
                with open(path, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode("utf-8")
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{b64}",
                            "detail": self.image_detail,  # "low"=85 tokens, "high"=1889 tokens
                        },
                    }
                )

            payload = {
                "model": self.model,
                "messages": [{"role": "user", "content": content}],
                "max_tokens": 500,
            }

            response = self.client.post(
                f"{self.base_url}/chat/completions",
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

            # Parse response and extract token counts
            response_text = data["choices"][0]["message"]["content"]
            result = self._parse_content_response(response_text)

            # Extract token usage from response
            usage = data.get("usage", {})
            prompt_tokens = usage.get("prompt_tokens", 0)
            completion_tokens = usage.get("completion_tokens", 0)

            # Log result with token tracking
            self._log_analysis_result(
                result,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                num_images=len(frames[:4]),
            )

            return result

        except httpx.HTTPError as e:
            logger.warning(f"OpenAI API error: {e}")
            return ContentAnalysis(confidence=0.0)

        finally:
            # Cleanup temporary frames
            for frame in frames:
                with contextlib.suppress(OSError):
                    frame.unlink()
