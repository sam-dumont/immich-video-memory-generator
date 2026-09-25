"""The music mood vocabulary, and the mood a film's music is chosen by.

The mood is read from text only (``audio/text_mood.py``): the cut's thesis, its story titles
and the captions banked at ingest. Nothing here looks at a picture.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# What the reader may answer; anything else is refused (whitelist approach).
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
        "metal",
        "upbeat",
        "relaxing",
    }
)

VALID_ENERGY_LEVELS = frozenset({"low", "medium", "high"})
VALID_TEMPOS = frozenset({"slow", "medium", "fast"})


@dataclass
class VideoMood:
    """The feel a film's music is chosen by."""

    primary_mood: str
    energy_level: str = "medium"  # low, medium, high
    tempo_suggestion: str = "medium"  # slow, medium, fast
    genre_suggestions: list[str] = field(default_factory=list)
    # A free-form genre phrase when this specific event calls for a very specific
    # music style the genre whitelist cannot name ("medieval folk with lute and
    # flute", "mariachi with trumpets", "1920s swing"). None means generic is fine.
    specific_style: str | None = None
