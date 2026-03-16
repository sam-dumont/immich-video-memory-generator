"""Base classes and parsing for content analysis.

Contains the ``ContentAnalysis`` dataclass and the ``ContentAnalyzer``
base class that concrete providers inherit from.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import cv2

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


# ---------------------------------------------------------------------------
# Base class with integrated parsing
# ---------------------------------------------------------------------------


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
                f"Tokens: {cls.total_prompt_tokens} input + "
                f"{cls.total_completion_tokens} output = "
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
        ContentAnalyzer.total_prompt_tokens += prompt_tokens
        ContentAnalyzer.total_completion_tokens += completion_tokens
        ContentAnalyzer.total_images_analyzed += num_images

        desc = (
            result.description[:80] + "..." if len(result.description) > 80 else result.description
        )

        logger.info(
            f"LLM Analysis: {desc} | "
            f"emotion={result.emotion}, interest={result.interestingness:.2f}, "
            f"quality={result.quality:.2f} | "
            f"tokens: {prompt_tokens}+{completion_tokens}="
            f"{prompt_tokens + completion_tokens}"
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

        segment_duration = end_time - start_time
        if segment_duration <= 0:
            cap.release()
            return []

        frame_times = [
            start_time + (segment_duration * i / (num_frames - 1 if num_frames > 1 else 1))
            for i in range(num_frames)
        ]

        frames: list[Path] = []
        temp_dir = Path(tempfile.gettempdir()) / "immich_memories" / "content_frames"
        temp_dir.mkdir(parents=True, exist_ok=True)

        for i, time_pos in enumerate(frame_times):
            frame_num = int(time_pos * fps)
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_num)
            ret, frame = cap.read()

            if ret:
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

    # =========================================================================
    # LLM response parsing (from ContentParsingMixin)
    # =========================================================================

    @staticmethod
    def _numeric_emotion_to_str(value: float) -> str:
        """Convert a numeric emotion value to a descriptive word.

        Args:
            value: Emotion value between 0.0 and 1.0.

        Returns:
            Descriptive emotion string.
        """
        if value >= 0.8:
            return "joyful"
        if value >= 0.6:
            return "happy"
        if value >= 0.4:
            return "calm"
        if value >= 0.2:
            return "neutral"
        return "subdued"

    @staticmethod
    def _extract_json_text(text: str) -> str:
        """Strip markdown code fences and whitespace from LLM output.

        Args:
            text: Raw LLM response text.

        Returns:
            Cleaned text ready for JSON parsing.
        """
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        elif "```" in text:
            text = text.split("```")[1].split("```")[0]
        return text.strip()

    @staticmethod
    def _parse_json_array_text(text: str) -> dict:
        """Parse JSON text that starts with '[', returning the first element."""
        try:
            arr = json.loads(text)
            if isinstance(arr, list) and len(arr) > 0:
                return arr[0]
            raise ValueError("Empty array response")
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.find("}")
            if start >= 0 and end > start:
                return json.loads(text[start : end + 1])
            raise

    @staticmethod
    def _parse_json_object_text(text: str) -> dict:
        """Parse JSON text that starts with '{', fixing truncation if needed."""
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            fixed = text.rstrip().rstrip(",").rstrip()
            if not fixed.endswith("}"):
                fixed += "}"
            data = json.loads(fixed)
            logger.debug("Fixed truncated JSON by adding closing brace")
            return data

    @staticmethod
    def _parse_json_object(text: str) -> dict:
        """Parse a JSON object from text that may be malformed.

        Handles arrays, truncated JSON, and JSON embedded in prose.

        Args:
            text: Cleaned text potentially containing JSON.

        Returns:
            Parsed dictionary.

        Raises:
            json.JSONDecodeError: If no valid JSON can be extracted.
            ValueError: If text contains no JSON-like content.
        """
        if text.startswith("["):
            return ContentAnalyzer._parse_json_array_text(text)
        if text.startswith("{"):
            return ContentAnalyzer._parse_json_object_text(text)

        # Try to find JSON object anywhere in text
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(text[start:end])

        raise ValueError(f"No JSON found in response: {text[:100]}")

    @staticmethod
    def _build_content_analysis(data: dict, emotion: str) -> ContentAnalysis:
        """Build a ContentAnalysis from a parsed JSON dict and resolved emotion.

        Validates and truncates fields to prevent injection and bound resource usage.

        Args:
            data: Parsed JSON dictionary.
            emotion: Already-resolved emotion string.

        Returns:
            ContentAnalysis instance.
        """
        MAX_STR = 500
        MAX_LIST = 10
        description = str(data.get("description", ""))[:MAX_STR]
        activities = [str(a)[:MAX_STR] for a in data.get("activities", [])[:MAX_LIST]]
        subjects = [str(s)[:MAX_STR] for s in data.get("subjects", [])[:MAX_LIST]]
        setting = str(data.get("setting", ""))[:MAX_STR]
        emotion = emotion[:MAX_STR]

        raw_interest = float(data.get("interestingness", 0.5))
        raw_quality = float(data.get("quality", 0.5))
        interestingness = max(0.0, min(1.0, raw_interest))
        quality = max(0.0, min(1.0, raw_quality))

        return ContentAnalysis(
            description=description,
            activities=activities,
            subjects=subjects,
            setting=setting,
            emotion=emotion,
            interestingness=interestingness,
            quality=quality,
            confidence=0.8,
        )

    @staticmethod
    def _strip_thinking_blocks(text: str) -> str:
        """Strip chain-of-thought blocks from models like Qwen3.5.

        Removes ``<think>...</think>`` tags and common preamble patterns
        like "The user wants..." that appear before the actual JSON output.
        """
        # Remove <think>...</think> blocks (Qwen3.5, DeepSeek, etc.)
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
        # Remove common preamble before JSON (e.g., "The user wants...\n{")
        json_start = text.find("{")
        if json_start > 0:
            text = text[json_start:]
        return text.strip()

    def _parse_content_response(self, response_text: str) -> ContentAnalysis:
        """Parse LLM response into ContentAnalysis."""
        try:
            text = response_text.strip()
            logger.debug(f"Raw LLM response: {text[:500]}...")

            text = self._strip_thinking_blocks(text)
            text = self._extract_json_text(text)
            data = self._parse_json_object(text)

            # Handle emotion field - LLM sometimes returns number instead of string
            emotion_raw = data.get("emotion", "")
            if isinstance(emotion_raw, (int, float)):
                emotion = self._numeric_emotion_to_str(emotion_raw)
            else:
                emotion = str(emotion_raw) if emotion_raw else ""

            return self._build_content_analysis(data, emotion)

        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning(f"Failed to parse LLM response: {e}")
            # Try to extract partial data using regex as fallback
            return self._extract_partial_data(response_text)

    @staticmethod
    def _extract_emotion_from_text(text: str) -> str:
        """Extract emotion field from raw LLM text (string or numeric fallback)."""
        m = re.search(r'"emotion"\s*:\s*"([^"]+)"', text)
        if m:
            return m.group(1)
        m = re.search(r'"emotion"\s*:\s*([\d.]+)', text)
        if m:
            with contextlib.suppress(ValueError):
                return ContentAnalyzer._numeric_emotion_to_str(float(m.group(1)))
        return ""

    @staticmethod
    def _extract_float_field(text: str, field: str) -> float | None:
        """Extract a numeric float field from raw LLM text."""
        m = re.search(rf'"{field}"\s*:\s*([\d.]+)', text)
        if m:
            with contextlib.suppress(ValueError):
                return float(m.group(1))
        return None

    def _extract_partial_data(self, text: str) -> ContentAnalysis:
        """Extract partial data from malformed JSON using regex.

        This is a fallback when JSON parsing fails but the response
        contains useful information in a JSON-like format.
        """
        result = ContentAnalysis(confidence=0.4)

        desc_match = re.search(r'"description"\s*:\s*"([^"]*(?:\\.[^"]*)*)"', text)
        if desc_match:
            result.description = desc_match.group(1).replace('\\"', '"').replace("\\n", " ")
            logger.debug(f"Extracted description: {result.description[:80]}...")

        result.emotion = self._extract_emotion_from_text(text)
        if result.emotion:
            logger.debug(f"Extracted emotion: {result.emotion}")

        if (val := self._extract_float_field(text, "interestingness")) is not None:
            result.interestingness = val
            logger.debug(f"Extracted interestingness: {val}")

        if (val := self._extract_float_field(text, "quality")) is not None:
            result.quality = val
            logger.debug(f"Extracted quality: {val}")

        setting_match = re.search(r'"setting"\s*:\s*"([^"]+)"', text)
        if setting_match:
            result.setting = setting_match.group(1)

        if result.description or result.emotion:
            desc_preview = result.description[:50] if result.description else "(none)"
            logger.info(
                f"Partial extraction successful: desc='{desc_preview}...', emotion={result.emotion}"
            )
            result.confidence = 0.6
        else:
            logger.warning(f"Partial extraction found nothing in: {text[:150]}...")

        return result
