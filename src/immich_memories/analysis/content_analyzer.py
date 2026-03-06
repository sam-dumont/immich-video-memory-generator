"""Content analysis for video segments using vision LLMs.

Uses Ollama (local) or OpenAI to analyze video frames and describe
what's happening, rate interestingness, and detect activities.
"""

from __future__ import annotations

import base64
import contextlib
import json
import logging
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import httpx

logger = logging.getLogger(__name__)

# Prompt for content analysis - optimized for small vision models like Moondream
CONTENT_ANALYSIS_PROMPT = """Describe what you see in this image.

Return JSON with these fields:
- description: What is happening in this scene?
- emotion: What is the mood? (one word: happy, calm, excited, playful, joyful, peaceful)
- interestingness: How memorable is this moment? (0.0 to 1.0)
- quality: How good is the image quality? (0.0 to 1.0)

Example format: {"description": "...", "emotion": "...", "interestingness": 0.7, "quality": 0.8}

JSON:"""


@dataclass
class ContentAnalysis:
    """Analysis results for video content."""

    description: str = ""
    activities: list[str] = field(default_factory=list)
    subjects: list[str] = field(default_factory=list)
    setting: str = ""
    emotion: str = ""
    interestingness: float = 0.5
    quality: float = 0.5
    confidence: float = 0.5

    @property
    def content_score(self) -> float:
        """Combined content score for ranking."""
        return (self.interestingness * 0.7 + self.quality * 0.3) * self.confidence


class ContentAnalyzer:
    """Base class for content analysis."""

    # Class-level counters for session token tracking
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_images_analyzed: int = 0

    @classmethod
    def reset_session_stats(cls) -> None:
        """Reset session-level token counters."""
        cls.total_prompt_tokens = 0
        cls.total_completion_tokens = 0
        cls.total_images_analyzed = 0

    @classmethod
    def log_session_summary(cls) -> None:
        """Log cumulative token usage for cost estimation."""
        if cls.total_images_analyzed > 0:
            logger.info(
                f"LLM Session Summary: {cls.total_images_analyzed} images analyzed | "
                f"Tokens: {cls.total_prompt_tokens} input + {cls.total_completion_tokens} output = "
                f"{cls.total_prompt_tokens + cls.total_completion_tokens} total"
            )

    def _log_analysis_result(
        self,
        result: ContentAnalysis,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        num_images: int = 1,
    ) -> None:
        """Log analysis result with token tracking."""
        # Update class-level counters
        ContentAnalyzer.total_prompt_tokens += prompt_tokens
        ContentAnalyzer.total_completion_tokens += completion_tokens
        ContentAnalyzer.total_images_analyzed += num_images

        # Truncate description for logging
        desc = result.description[:80] + "..." if len(result.description) > 80 else result.description

        logger.info(
            f"LLM Analysis: {desc} | "
            f"emotion={result.emotion}, interest={result.interestingness:.2f}, "
            f"quality={result.quality:.2f} | "
            f"tokens: {prompt_tokens}+{completion_tokens}={prompt_tokens + completion_tokens}"
        )

    def extract_frames(
        self,
        video_path: Path,
        start_time: float = 0,
        end_time: float | None = None,
        num_frames: int = 3,
        max_height: int = 480,
    ) -> list[Path]:
        """Extract frames from a video segment.

        Args:
            video_path: Path to video file.
            start_time: Start time in seconds.
            end_time: End time in seconds (None = end of video).
            num_frames: Number of frames to extract.
            max_height: Maximum frame height in pixels (default 480 for speed/cost).

        Returns:
            List of paths to extracted frame images.
        """
        cap = cv2.VideoCapture(str(video_path))
        fps = cap.get(cv2.CAP_PROP_FPS) or 30
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        duration = total_frames / fps

        if end_time is None:
            end_time = duration

        # Calculate frame positions
        segment_duration = end_time - start_time
        if segment_duration <= 0:
            cap.release()
            return []

        frame_times = [
            start_time + (segment_duration * i / (num_frames - 1 if num_frames > 1 else 1))
            for i in range(num_frames)
        ]

        frames = []
        temp_dir = Path(tempfile.gettempdir()) / "immich_memories" / "content_frames"
        temp_dir.mkdir(parents=True, exist_ok=True)

        for i, time_pos in enumerate(frame_times):
            frame_num = int(time_pos * fps)
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_num)
            ret, frame = cap.read()

            if ret:
                # Resize for efficiency (configurable, default 480p for speed/cost)
                h, w = frame.shape[:2]
                if h > max_height:
                    scale = max_height / h
                    frame = cv2.resize(frame, (int(w * scale), max_height))

                frame_path = temp_dir / f"frame_{video_path.stem}_{i}.jpg"
                cv2.imwrite(str(frame_path), frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
                frames.append(frame_path)

        cap.release()
        return frames

    def analyze_segment(
        self,
        video_path: Path,
        start_time: float = 0,
        end_time: float | None = None,
        num_frames: int = 3,
    ) -> ContentAnalysis:
        """Analyze a video segment using vision model.

        Args:
            video_path: Path to video file.
            start_time: Start time in seconds.
            end_time: End time in seconds (None = end of video).
            num_frames: Number of frames to extract and analyze.

        Returns:
            ContentAnalysis with description, scores, etc.
        """
        raise NotImplementedError("Subclasses must implement analyze_segment")

    def _parse_content_response(self, response_text: str) -> ContentAnalysis:
        """Parse LLM response into ContentAnalysis."""
        try:
            # Try to extract JSON from response
            text = response_text.strip()

            # Log raw response for debugging (truncated)
            logger.debug(f"Raw LLM response: {text[:500]}...")

            # Handle markdown code blocks
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0]
            elif "```" in text:
                text = text.split("```")[1].split("```")[0]

            text = text.strip()

            # Handle JSON array (LLM sometimes returns [{...}] instead of {...})
            if text.startswith("["):
                # Find matching bracket for array
                try:
                    arr = json.loads(text)
                    if isinstance(arr, list) and len(arr) > 0:
                        data = arr[0]  # Take first element
                    else:
                        raise ValueError("Empty array response")
                except json.JSONDecodeError:
                    # Array might be incomplete, try to extract first object
                    start = text.find("{")
                    end = text.find("}")
                    if start >= 0 and end > start:
                        text = text[start:end + 1]
                        data = json.loads(text)
                    else:
                        raise
            elif text.startswith("{"):
                try:
                    data = json.loads(text)
                except json.JSONDecodeError:
                    # Try to fix truncated JSON by adding missing closing brace
                    fixed = text.rstrip().rstrip(",").rstrip()
                    if not fixed.endswith("}"):
                        fixed += "}"
                    try:
                        data = json.loads(fixed)
                        logger.debug("Fixed truncated JSON by adding closing brace")
                    except json.JSONDecodeError:
                        # Still failing, let the outer handler deal with it
                        raise
            else:
                # Try to find JSON object anywhere in text
                start = text.find("{")
                end = text.rfind("}") + 1
                if start >= 0 and end > start:
                    text = text[start:end]
                    data = json.loads(text)
                else:
                    raise ValueError(f"No JSON found in response: {text[:100]}")

            # Handle emotion field - LLM sometimes returns number instead of string
            emotion_raw = data.get("emotion", "")
            if isinstance(emotion_raw, (int, float)):
                # Convert numeric emotion to descriptive word based on value
                if emotion_raw >= 0.8:
                    emotion = "joyful"
                elif emotion_raw >= 0.6:
                    emotion = "happy"
                elif emotion_raw >= 0.4:
                    emotion = "calm"
                elif emotion_raw >= 0.2:
                    emotion = "neutral"
                else:
                    emotion = "subdued"
            else:
                emotion = str(emotion_raw) if emotion_raw else ""

            return ContentAnalysis(
                description=data.get("description", ""),
                activities=data.get("activities", []),
                subjects=data.get("subjects", []),
                setting=data.get("setting", ""),
                emotion=emotion,
                interestingness=float(data.get("interestingness", 0.5)),
                quality=float(data.get("quality", 0.5)),
                confidence=0.8,
            )

        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning(f"Failed to parse LLM response: {e}. Raw text: {response_text[:200]}...")
            # Try to extract partial data using regex as fallback
            return self._extract_partial_data(response_text)

    def _extract_partial_data(self, text: str) -> ContentAnalysis:
        """Extract partial data from malformed JSON using regex.

        This is a fallback when JSON parsing fails but the response
        contains useful information in a JSON-like format.
        """
        import re

        result = ContentAnalysis(confidence=0.4)  # Lower confidence for partial extraction

        # Try to extract description (handle escaped quotes and newlines)
        desc_match = re.search(r'"description"\s*:\s*"([^"]*(?:\\.[^"]*)*)"', text)
        if desc_match:
            result.description = desc_match.group(1).replace('\\"', '"').replace('\\n', ' ')
            logger.debug(f"Extracted description: {result.description[:80]}...")

        # Try to extract emotion (can be string or number)
        emotion_match = re.search(r'"emotion"\s*:\s*"([^"]+)"', text)
        if emotion_match:
            result.emotion = emotion_match.group(1)
            logger.debug(f"Extracted emotion: {result.emotion}")
        else:
            # Try numeric emotion
            emotion_num_match = re.search(r'"emotion"\s*:\s*([\d.]+)', text)
            if emotion_num_match:
                try:
                    emotion_val = float(emotion_num_match.group(1))
                    if emotion_val >= 0.8:
                        result.emotion = "joyful"
                    elif emotion_val >= 0.6:
                        result.emotion = "happy"
                    elif emotion_val >= 0.4:
                        result.emotion = "calm"
                    elif emotion_val >= 0.2:
                        result.emotion = "neutral"
                    else:
                        result.emotion = "subdued"
                    logger.debug(f"Extracted numeric emotion {emotion_val} -> {result.emotion}")
                except ValueError:
                    pass

        # Try to extract interestingness
        interest_match = re.search(r'"interestingness"\s*:\s*([\d.]+)', text)
        if interest_match:
            try:
                result.interestingness = float(interest_match.group(1))
                logger.debug(f"Extracted interestingness: {result.interestingness}")
            except ValueError:
                pass

        # Try to extract quality
        quality_match = re.search(r'"quality"\s*:\s*([\d.]+)', text)
        if quality_match:
            try:
                result.quality = float(quality_match.group(1))
                logger.debug(f"Extracted quality: {result.quality}")
            except ValueError:
                pass

        # Try to extract setting
        setting_match = re.search(r'"setting"\s*:\s*"([^"]+)"', text)
        if setting_match:
            result.setting = setting_match.group(1)

        if result.description or result.emotion:
            desc_preview = result.description[:50] if result.description else "(none)"
            logger.info(f"Partial extraction successful: desc='{desc_preview}...', emotion={result.emotion}")
            result.confidence = 0.6  # Upgrade confidence if we got something useful
        else:
            logger.warning(f"Partial extraction found nothing in: {text[:150]}...")

        return result


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
        # use 2 images max: 2×729 + ~400 prompt = ~1858 tokens < 2048 limit
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
                        self._log_analysis_result(result, prompt_tokens, completion_tokens, len(images))
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
                content.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{b64}",
                        "detail": self.image_detail,  # "low"=85 tokens, "high"=1889 tokens
                    },
                })

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
        logger.info(f"Using OpenAI for content analysis (model: {openai_model}, detail: {openai_image_detail})")
        return openai

    # Try Ollama first (for "ollama" or "auto")
    ollama = OllamaContentAnalyzer(
        model=ollama_model,
        base_url=ollama_url,
        max_height=max_height,
        num_ctx=ollama_num_ctx,
    )
    if ollama.is_available():
        logger.info(f"Using Ollama for content analysis (model: {ollama_model}, num_ctx: {ollama_num_ctx})")
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
        logger.info(f"Using OpenAI for content analysis (model: {openai_model}, detail: {openai_image_detail})")
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
