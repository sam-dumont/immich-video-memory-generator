"""Video mood analysis using LLM vision models."""

from __future__ import annotations

import json
import logging
import subprocess
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from immich_memories.security import validate_video_path

logger = logging.getLogger(__name__)

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
        """Extract keyframes from a video using FFmpeg.

        Args:
            video_path: Path to the video
            num_frames: Number of frames to extract
            output_dir: Directory for output frames

        Returns:
            List of paths to extracted frames
        """
        # Validate video path before any subprocess calls
        validated_video = validate_video_path(video_path, must_exist=True)

        if output_dir is None:
            output_dir = Path(tempfile.mkdtemp(prefix="keyframes_"))
        else:
            output_dir.mkdir(parents=True, exist_ok=True)

        # Get video duration
        probe_cmd = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(validated_video),
        ]

        try:
            result = subprocess.run(probe_cmd, capture_output=True, text=True, check=True)
            duration = float(result.stdout.strip())
        except (subprocess.CalledProcessError, ValueError):
            logger.warning("Could not determine video duration, using 60s")
            duration = 60.0

        # Calculate frame times (evenly distributed)
        frame_times = [duration * i / (num_frames + 1) for i in range(1, num_frames + 1)]

        frames = []
        for i, time in enumerate(frame_times):
            output_path = output_dir / f"frame_{i:03d}.jpg"

            cmd = [
                "ffmpeg",
                "-y",
                "-ss",
                str(time),
                "-i",
                str(validated_video),
                "-vframes",
                "1",
                "-q:v",
                "2",
                "-vf",
                "scale=512:-1",  # Resize for faster analysis
                str(output_path),
            ]

            try:
                subprocess.run(
                    cmd,
                    capture_output=True,
                    check=True,
                )
                if output_path.exists():
                    frames.append(output_path)
            except subprocess.CalledProcessError as e:
                logger.warning(f"Failed to extract frame at {time}s: {e}")

        return frames

    def _parse_mood_response(self, response_text: str) -> VideoMood:
        """Parse and validate LLM response into VideoMood object.

        Uses whitelist validation to prevent LLM output injection attacks.
        """
        try:
            # Try to extract JSON from response
            text = response_text.strip()

            # Handle markdown code blocks
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0]
            elif "```" in text:
                text = text.split("```")[1].split("```")[0]

            data = json.loads(text)

            # Validate and constrain primary_mood against whitelist
            primary_mood = str(data.get("primary_mood", "calm")).lower().strip()
            if primary_mood not in VALID_MOODS:
                logger.warning(f"Invalid mood '{primary_mood}', defaulting to 'calm'")
                primary_mood = "calm"

            # Validate secondary_mood
            secondary_mood = data.get("secondary_mood")
            if secondary_mood is not None:
                secondary_mood = str(secondary_mood).lower().strip()
                if secondary_mood not in VALID_MOODS:
                    secondary_mood = None

            # Validate energy_level
            energy_level = str(data.get("energy_level", "medium")).lower().strip()
            if energy_level not in VALID_ENERGY_LEVELS:
                energy_level = "medium"

            # Validate tempo_suggestion
            tempo = str(data.get("tempo_suggestion", "medium")).lower().strip()
            if tempo not in VALID_TEMPOS:
                tempo = "medium"

            # Validate genre_suggestions (whitelist + limit count)
            raw_genres = data.get("genre_suggestions", [])
            if not isinstance(raw_genres, list):
                raw_genres = []
            genre_suggestions = [
                str(g).lower().strip()
                for g in raw_genres[:MAX_GENRE_COUNT]
                if str(g).lower().strip() in VALID_GENRES
            ]
            if not genre_suggestions:
                genre_suggestions = ["ambient"]

            # Validate color_palette
            palette = str(data.get("color_palette", "neutral")).lower().strip()
            if palette not in VALID_PALETTES:
                palette = "neutral"

            # Sanitize description (limit length, strip control chars)
            description = str(data.get("description", ""))[:MAX_DESCRIPTION_LENGTH]
            # Remove any control characters
            description = "".join(c for c in description if c.isprintable() or c in "\n\t")

            # Validate confidence (numeric, 0-1 range)
            try:
                confidence = float(data.get("confidence", 0.7))
                confidence = max(0.0, min(1.0, confidence))
            except (ValueError, TypeError):
                confidence = 0.7

            return VideoMood(
                primary_mood=primary_mood,
                secondary_mood=secondary_mood,
                energy_level=energy_level,
                tempo_suggestion=tempo,
                genre_suggestions=genre_suggestions,
                color_palette=palette,
                description=description,
                confidence=confidence,
            )

        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"Failed to parse mood response: {e}")
            # Return default mood
            return VideoMood(
                primary_mood="calm",
                energy_level="medium",
                tempo_suggestion="medium",
                genre_suggestions=["ambient"],
                description="Could not analyze video mood",
                confidence=0.3,
            )


# Re-export backends and factory functions for backwards compatibility
from immich_memories.audio.mood_analyzer_backends import (  # noqa: E402, F401
    OllamaMoodAnalyzer,
    OpenAIMoodAnalyzer,
    get_mood_analyzer,
    get_mood_analyzer_from_config,
)
