"""Provider implementations for content analysis.

Contains OllamaContentAnalyzer and OpenAICompatibleContentAnalyzer. Both send
their frames through ``query_llm`` like every other LLM call in the project;
what stays here is what is genuinely theirs — how many frames the model can
hold, and how a sick server is recognised and turned off for the run.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path

import httpx

from immich_memories.analysis.llm_query import build_llm_timeout, query_llm
from immich_memories.analysis.llm_response_parser import (
    ContentAnalysis,
    ContentAnalyzer,
    build_content_analysis_prompt,
)
from immich_memories.analysis.request_heartbeat import RequestHeartbeat
from immich_memories.config_models_llm import LLMConfig

logger = logging.getLogger(__name__)

# The health probe keeps its own client. A local vision model doing real
# inference can take minutes, so `read` keeps the full user-configured budget
# (llm.timeout_seconds, can be an hour). The other phases are short on purpose:
# a server that is down now fails in seconds instead of silently borrowing the
# read budget.

# Room for the JSON answer plus whatever preamble a chatty model puts in front
# of it.
_ANSWER_MAX_TOKENS = 1024

# Small vision models answer with nothing often enough that a batch loses a
# measurable fraction of its clips without a retry. query_llm retries a null
# content field; Ollama returns an empty string rather than null and gets no
# retry there, so the near-empty check lives here and covers both dialects.
_ANSWER_ATTEMPTS = 3
_MIN_USABLE_ANSWER_CHARS = 10

# Bulk analysis is the fast tier by design, so the request is deliberately
# plain: low temperature for parseable JSON and never `thinking=True`, which is
# a measured runaway once several images are in the same call.
_ANSWER_TEMPERATURE = 0.3


def _unusable_answer() -> ContentAnalysis:
    """What a clip scores when the model kept answering with nothing."""
    return ContentAnalysis(
        description="(analysis unavailable)",
        interestingness=0.5,
        quality=0.5,
        confidence=0.2,
    )


def _ask_vision_model(
    prompt: str,
    images: list[bytes],
    config: LLMConfig,
    image_detail: str,
) -> str:
    """Ask the configured model about frames, retrying while it says nothing.

    Returns the model's answer, or an empty string once it has had its tries.
    """
    for attempt in range(_ANSWER_ATTEMPTS):
        with RequestHeartbeat(f"LLM request (model={config.model}, url={config.base_url})"):
            try:
                answer = asyncio.run(
                    query_llm(
                        prompt,
                        config,
                        temperature=_ANSWER_TEMPERATURE,
                        max_tokens=_ANSWER_MAX_TOKENS,
                        timeout_seconds=config.timeout_seconds,
                        images=images,
                        image_detail=image_detail,
                    )
                )
            except ValueError:
                # query_llm has already asked three times and been handed null
                # content each time; asking again only repeats it.
                logger.warning("Model returned null content, giving up on this segment")
                return ""
        if len(answer.strip()) >= _MIN_USABLE_ANSWER_CHARS:
            return answer
        logger.warning(
            "Model returned a near-empty response (attempt %d/%d, len: %d)",
            attempt + 1,
            _ANSWER_ATTEMPTS,
            len(answer),
        )
    return ""


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
        timeout: float = 300.0,
        circuit=None,
    ):
        """Initialize Ollama analyzer.

        Args:
            model: Ollama model name (llava, bakllava, moondream, qwen2-vl, etc.)
            base_url: Ollama API base URL
            max_height: Maximum frame height in pixels (default 480 for speed)
            num_ctx: Context window size for Ollama (default 4096)
            timeout: HTTP request timeout in seconds (default 300)
        """
        super().__init__(circuit=circuit)
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.max_height = max_height
        self.num_ctx = num_ctx
        self.timeout = timeout
        self._client: httpx.Client | None = None
        self._llm_config = LLMConfig(
            provider="ollama",
            base_url=self.base_url,
            model=model,
            timeout_seconds=int(timeout),
            # Ollama defaults to a 2048-token context, which the prompt plus
            # several frames overruns, so the window has to be asked for.
            extra_params={"options": {"num_ctx": num_ctx}},
        )

        # Check if this model needs single image mode (small context)
        model_base = model.split(":")[0].lower()
        self.single_image_mode = model_base in self.SINGLE_IMAGE_MODELS
        if self.single_image_mode:
            logger.info("Moondream detected: using single image mode to fit context window")

    @property
    def client(self) -> httpx.Client:
        """Get or create the HTTP client used by the health probe."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.Client(timeout=build_llm_timeout(self.timeout))
        return self._client

    def close(self):
        """Close the HTTP client."""
        if self._client:
            self._client.close()
            self._client = None

    def is_available(self) -> bool:
        """Check if Ollama is available."""
        return self.check_health().available

    def check_health(self):
        """Check Ollama and the configured model through its model registry."""
        from immich_memories.analysis.provider_health import ProviderHealth, ProviderState

        try:
            response = self.client.get(f"{self.base_url}/api/tags")
            if response.status_code in {401, 403}:
                health = ProviderHealth(
                    ProviderState.AUTH_FAILED,
                    "provider authentication rejected",
                )
            elif response.status_code == 404:
                health = ProviderHealth(
                    ProviderState.ROUTE_MISSING, "Ollama tags route unavailable"
                )
            elif response.status_code >= 400:
                health = ProviderHealth(
                    ProviderState.UNREACHABLE,
                    f"provider unavailable (HTTP {response.status_code})",
                )
            else:
                models = response.json().get("models", [])
                names = [item.get("name", "") for item in models]
                base_name = self.model.split(":")[0]
                found = self.model in names or any(name.startswith(base_name) for name in names)
                health = ProviderHealth(
                    ProviderState.READY if found else ProviderState.MODEL_MISSING,
                    (
                        f"configured model ready: {self.model}"
                        if found
                        else f"configured model unavailable: {self.model}"
                    ),
                )
        except httpx.HTTPError:
            health = ProviderHealth(
                ProviderState.UNREACHABLE,
                "content-analysis provider is unreachable",
            )
        self.circuit.set_health(health)
        return health

    def analyze_segment(
        self,
        video_path: Path,
        start_time: float = 0,
        end_time: float | None = None,
        num_frames: int = 3,
        transcript: str | None = None,
    ) -> ContentAnalysis:
        """Analyze a video segment using Ollama vision model.

        Args:
            video_path: Path to video file.
            start_time: Segment start time in seconds.
            end_time: Segment end time in seconds.
            num_frames: Number of frames to analyze.
            transcript: Speech heard around this moment, or None.

        Returns:
            ContentAnalysis with description and scores.
        """
        if not self.available:
            return ContentAnalysis(confidence=0.0)

        # For models with small context (Moondream: ~729 tokens/image),
        # use 2 images max: 2x729 + ~400 prompt = ~1858 tokens < 2048 limit
        actual_frames, max_images = (
            (min(2, num_frames), 2) if self.single_image_mode else (num_frames, 4)
        )

        frames = self.extract_frames(
            video_path, start_time, end_time, actual_frames, max_height=self.max_height
        )

        if not frames:
            logger.debug("No frames extracted for content analysis")
            return ContentAnalysis(confidence=0.0)

        try:
            images = [path.read_bytes() for path in frames[:max_images]]
            answer = _ask_vision_model(
                build_content_analysis_prompt(transcript), images, self._llm_config, "low"
            )
            result = self._parse_content_response(answer) if answer else _unusable_answer()
            self._log_analysis_result(result, num_images=len(images))
            return result

        except httpx.HTTPError as e:
            logger.warning(f"Ollama API error: {e}")
            return ContentAnalysis(confidence=0.0)

        finally:
            for frame in frames:
                with contextlib.suppress(OSError):
                    frame.unlink()


class OpenAICompatibleContentAnalyzer(ContentAnalyzer):
    """Content analyzer for any OpenAI-compatible API."""

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        base_url: str = "https://api.openai.com/v1",
        api_key: str = "",
        image_detail: str = "low",
        max_height: int = 480,
        timeout: float = 300.0,
        circuit=None,
    ):
        """Initialize OpenAI-compatible analyzer.

        Args:
            model: Model name (gpt-4o, gpt-4o-mini, gpt-4.1-nano, llama-4-scout, etc.)
            base_url: API base URL (works with OpenAI, Groq, mlx-vlm, etc.)
            api_key: API key (optional — local servers don't need one)
            image_detail: Image detail level ("low"=85 tokens, "high"=1889 tokens, "auto")
            max_height: Maximum frame height in pixels (default 480 for speed/cost)
            timeout: HTTP request timeout in seconds (default 300)
        """
        super().__init__(circuit=circuit)
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.image_detail = image_detail
        self.max_height = max_height
        self.timeout = timeout
        self._client: httpx.Client | None = None
        self._llm_config = LLMConfig(
            provider="openai-compatible",
            base_url=self.base_url,
            model=model,
            api_key=api_key,
            timeout_seconds=int(timeout),
        )

    @property
    def client(self) -> httpx.Client:
        """Get or create the HTTP client used by the health probe."""
        if self._client is None or self._client.is_closed:
            headers: dict[str, str] = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            self._client = httpx.Client(timeout=build_llm_timeout(self.timeout), headers=headers)
        return self._client

    def close(self):
        """Close the HTTP client."""
        if self._client:
            self._client.close()
            self._client = None

    def is_available(self) -> bool:
        """Check if the API endpoint is available."""
        return self.check_health().available

    def check_health(self):
        """Probe the configured chat route and model once with a tiny request."""
        from immich_memories.analysis.provider_health import (
            ProviderHealth,
            ProviderState,
            classify_openai_response,
        )

        try:
            response = self.client.post(
                f"{self.base_url}/chat/completions",
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": "hi"}],
                    "max_tokens": 1,
                },
                timeout=min(self.timeout, 10.0),
            )
            try:
                body = response.json()
            except ValueError:
                body = {}
            health = classify_openai_response(response.status_code, body, self.model)
        except httpx.HTTPError:
            health = ProviderHealth(
                ProviderState.UNREACHABLE,
                "content-analysis provider is unreachable",
            )
        self.circuit.set_health(health)
        return health

    def analyze_segment(
        self,
        video_path: Path,
        start_time: float = 0,
        end_time: float | None = None,
        num_frames: int = 3,
        transcript: str | None = None,
    ) -> ContentAnalysis:
        """Analyze a video segment using OpenAI vision model."""
        if not self.available:
            return ContentAnalysis(confidence=0.0)

        frames = self.extract_frames(
            video_path, start_time, end_time, num_frames, max_height=self.max_height
        )

        if not frames:
            logger.debug("No frames extracted for content analysis")
            return ContentAnalysis(confidence=0.0)

        try:
            images = [path.read_bytes() for path in frames[:4]]
            answer = _ask_vision_model(
                build_content_analysis_prompt(transcript),
                images,
                self._llm_config,
                self.image_detail,
            )
            result = self._parse_content_response(answer) if answer else _unusable_answer()
            self._log_analysis_result(result, num_images=len(images))
            return result

        except httpx.HTTPStatusError as e:
            self._note_rejection(e.response)
            return ContentAnalysis(confidence=0.0)
        except httpx.HTTPError:
            if self.circuit.disable("content-analysis provider is unreachable"):
                logger.warning("Content analysis disabled for this run: provider unreachable")
            return ContentAnalysis(confidence=0.0)

        finally:
            # Cleanup temporary frames
            for frame in frames:
                with contextlib.suppress(OSError):
                    frame.unlink()

    def _note_rejection(self, response: httpx.Response) -> None:
        """Read a rejected response for what it says about the provider."""
        from immich_memories.analysis.provider_health import classify_openai_response

        try:
            body = response.json()
        except ValueError:
            body = {}
        health = classify_openai_response(response.status_code, body, self.model)
        if 400 <= response.status_code < 500 and self.circuit.set_health(health):
            logger.warning("Content analysis disabled for this run: %s", health.message)
