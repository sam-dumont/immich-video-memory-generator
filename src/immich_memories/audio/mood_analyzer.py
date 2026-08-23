"""Video mood analysis using LLM vision models."""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from immich_memories.security import validate_video_path

logger = logging.getLogger(__name__)

# A vision model needs more detail than a colour histogram.
_MOOD_FRAME_WIDTH = 512

# Valid values for LLM output validation (whitelist approach)
VALID_MOODS = frozenset(
    {
        "happy",
        "sad",
        "calm",
        "energetic",
        "romantic",
        "dramatic",
        "playful",
        "nostalgic",
        "mysterious",
        "inspiring",
        "peaceful",
        "melancholic",
        "uplifting",
        "tender",
        "exciting",
    }
)

VALID_GENRES = frozenset(
    {
        "acoustic",
        "electronic",
        "cinematic",
        "classical",
        "jazz",
        "pop",
        "rock",
        "ambient",
        "folk",
        "indie",
        "orchestral",
        "piano",
        "guitar",
        "upbeat",
        "relaxing",
    }
)

VALID_ENERGY_LEVELS = frozenset({"low", "medium", "high"})
VALID_TEMPOS = frozenset({"slow", "medium", "fast"})
VALID_PALETTES = frozenset({"warm", "cool", "neutral", "vibrant", "muted"})

# Maximum lengths for string fields
MAX_DESCRIPTION_LENGTH = 500
MAX_GENRE_COUNT = 5


@dataclass
class VideoMood:
    """Represents the analyzed mood/feel of a video."""

    primary_mood: str  # Main mood (happy, calm, energetic, etc.)
    secondary_mood: str | None = None
    energy_level: str = "medium"  # low, medium, high
    tempo_suggestion: str = "medium"  # slow, medium, fast
    genre_suggestions: list[str] = field(default_factory=list)
    color_palette: str = "neutral"  # warm, cool, neutral, vibrant, muted
    description: str = ""
    confidence: float = 0.8

    def to_search_params(self) -> dict:
        """Convert mood to music search parameters."""
        return {
            "mood": self.primary_mood,
            "genre": self.genre_suggestions[0] if self.genre_suggestions else None,
            "tempo": self.tempo_suggestion,
        }


MOOD_ANALYSIS_PROMPT = """Analyze these video keyframes and describe the overall mood and feel.

Consider:
1. The emotions conveyed (happy, sad, calm, energetic, romantic, dramatic, playful, nostalgic)
2. The energy level (low, medium, high)
3. Suggested music tempo (slow, medium, fast)
4. Suggested music genres that would fit (acoustic, electronic, cinematic, classical, jazz, pop, ambient)
5. Color palette feel (warm, cool, neutral, vibrant, muted)

Respond in JSON format:
{
    "primary_mood": "happy",
    "secondary_mood": "nostalgic",
    "energy_level": "medium",
    "tempo_suggestion": "medium",
    "genre_suggestions": ["acoustic", "pop"],
    "color_palette": "warm",
    "description": "A warm family gathering with happy moments",
    "confidence": 0.85
}

Only respond with valid JSON, no additional text."""


class MoodAnalyzer(ABC):
    """Abstract base class for video mood analyzers."""

    @abstractmethod
    async def analyze_video(
        self,
        video_path: Path,
        num_keyframes: int = 5,
    ) -> VideoMood:
        """Analyze a video and determine its mood.

        Args:
            video_path: Path to the video file
            num_keyframes: Number of keyframes to extract

        Returns:
            VideoMood describing the video's feel
        """
        pass

    @abstractmethod
    async def analyze_frames(
        self,
        frame_paths: list[Path],
    ) -> VideoMood:
        """Analyze a set of video frames.

        Args:
            frame_paths: Paths to frame images

        Returns:
            VideoMood describing the frames' feel
        """
        pass

    def extract_keyframes(
        self,
        video_path: Path,
        num_frames: int = 5,
        output_dir: Path | None = None,
    ) -> list[Path]:
        """Evenly spaced frames from a video, for the mood model to look at.

        Delegates to processing.frame_sampling, which is also what the title
        colour sampler uses. Both were the same loop — probe the duration,
        space the timestamps, one ffmpeg per frame — written twice and cached
        neither time, so every run re-decoded the same video.

        512px because a vision model needs more than a colour histogram does;
        that width is the only thing this caller decides.
        """
        from immich_memories.processing.frame_sampling import sample_frames

        validated_video = validate_video_path(video_path, must_exist=True)
        return sample_frames(
            validated_video, count=num_frames, width=_MOOD_FRAME_WIDTH, cache_dir=output_dir
        )

    @staticmethod
    def _extract_json_from_text(text: str) -> str:
        """Strip markdown code fences from LLM response."""
        if "```json" in text:
            return text.split("```json")[1].split("```")[0]
        if "```" in text:
            return text.split("```")[1].split("```")[0]
        return text

    @staticmethod
    def _validate_secondary_mood(data: dict) -> str | None:
        """Validate secondary_mood against whitelist, returning None if invalid."""
        secondary_mood = data.get("secondary_mood")
        if secondary_mood is None:
            return None
        val = str(secondary_mood).lower().strip()
        return val if val in VALID_MOODS else None

    @staticmethod
    def _validate_genres(data: dict) -> list[str]:
        """Validate genre_suggestions against whitelist with count limit."""
        raw = data.get("genre_suggestions", [])
        if not isinstance(raw, list):
            return ["ambient"]
        genres = [
            str(g).lower().strip()
            for g in raw[:MAX_GENRE_COUNT]
            if str(g).lower().strip() in VALID_GENRES
        ]
        return genres or ["ambient"]

    @staticmethod
    def _sanitize_confidence(data: dict) -> float:
        """Parse and clamp confidence to [0.0, 1.0]."""
        try:
            return max(0.0, min(1.0, float(data.get("confidence", 0.7))))
        except (ValueError, TypeError):
            return 0.7

    def _parse_mood_response(self, response_text: str) -> VideoMood:
        """Parse and validate LLM response into VideoMood object.

        Uses whitelist validation to prevent LLM output injection attacks.
        """
        try:
            text = self._extract_json_from_text(response_text.strip())
            data = json.loads(text)

            primary_mood = str(data.get("primary_mood", "calm")).lower().strip()
            if primary_mood not in VALID_MOODS:
                logger.warning(f"Invalid mood '{primary_mood}', defaulting to 'calm'")
                primary_mood = "calm"

            energy_level = str(data.get("energy_level", "medium")).lower().strip()
            if energy_level not in VALID_ENERGY_LEVELS:
                energy_level = "medium"

            tempo = str(data.get("tempo_suggestion", "medium")).lower().strip()
            if tempo not in VALID_TEMPOS:
                tempo = "medium"

            palette = str(data.get("color_palette", "neutral")).lower().strip()
            if palette not in VALID_PALETTES:
                palette = "neutral"

            description = str(data.get("description", ""))[:MAX_DESCRIPTION_LENGTH]
            description = "".join(c for c in description if c.isprintable() or c in "\n\t")

            return VideoMood(
                primary_mood=primary_mood,
                secondary_mood=self._validate_secondary_mood(data),
                energy_level=energy_level,
                tempo_suggestion=tempo,
                genre_suggestions=self._validate_genres(data),
                color_palette=palette,
                description=description,
                confidence=self._sanitize_confidence(data),
            )

        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"Failed to parse mood response: {e}")
            return VideoMood(
                primary_mood="calm",
                energy_level="medium",
                tempo_suggestion="medium",
                genre_suggestions=["ambient"],
                description="Could not analyze video mood",
                confidence=0.3,
            )
