"""Audio content analysis data models and constants.

Contains data classes, event categories, and helper functions
used by the audio content analysis system.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


# Audio event categories and their weights for scoring
# Higher weight = more interesting for memory videos
AUDIO_EVENT_WEIGHTS = {
    # Laughter (highly desirable)
    "Laughter": 1.0,
    "Giggle": 0.95,
    "Chuckle, chortle": 0.9,
    "Baby laughter": 1.0,
    "Child speech, kid speaking": 0.85,
    # Positive sounds
    "Cheering": 0.8,
    "Clapping": 0.7,
    "Applause": 0.7,
    "Crowd": 0.5,
    "Whoop": 0.75,
    # Baby sounds
    "Baby cry, infant cry": 0.6,  # Can be memorable but not always positive
    "Babbling": 0.7,
    # Speech (moderate - indicates conversation)
    "Speech": 0.4,
    "Conversation": 0.5,
    "Narration, monologue": 0.3,
    "Children shouting": 0.6,
    # Singing (separate from speech — often a highlight)
    "Singing": 0.8,
    "Choir": 0.7,
    # Music (can be good)
    "Music": 0.4,
    "Musical instrument": 0.5,
    # Engine / vehicles (action-packed moments)
    "Engine": 0.5,
    "Motor vehicle (road)": 0.4,
    "Car": 0.4,
    "Motorcycle": 0.5,
    "Race car, racing car": 0.7,
    "Aircraft": 0.4,
    "Boat, Water vehicle": 0.4,
    # Nature sounds (atmospheric)
    "Bird": 0.3,
    "Ocean": 0.3,
    "Wind": 0.2,
    "Water": 0.3,
    "Rain": 0.3,
    "Thunder": 0.4,
    # Animals (can be fun)
    "Dog": 0.4,
    "Cat": 0.3,
    "Animal": 0.3,
    # Negative/neutral (lower priority)
    "Silence": 0.0,
    "White noise": 0.0,
    "Static": 0.0,
    "Noise": 0.1,
}

# Map AudioSet class names to high-level categories for detection flags.
# Keys are substrings matched case-insensitively against the class name.
# Ordered list — first match wins. More specific categories come first
# so "Baby laughter" matches "baby" before "laughter".
AUDIO_CATEGORY_KEYWORDS: list[tuple[str, list[str]]] = [
    ("baby", ["baby", "infant"]),
    ("laughter", ["laugh", "giggle", "chuckle", "chortle"]),
    ("singing", ["singing", "choir", "vocal music"]),
    ("speech", ["speech", "talk", "conversation", "narration", "monologue"]),
    ("crowd", ["cheering", "applause", "crowd", "clapping", "whoop"]),
    ("engine", ["engine", "motor vehicle", "race car", "motorcycle", "aircraft"]),
    ("music", ["music", "instrument", "guitar", "piano", "drum", "flute", "violin"]),
    ("nature", ["bird", "ocean", "wind", "water", "rain", "thunder", "stream"]),
    ("animals", ["dog", "cat", "bark", "meow", "purr", "animal"]),
]


def classify_audio_event(class_name: str) -> str | None:
    """Map an AudioSet class name to a high-level category.

    Args:
        class_name: AudioSet class label (e.g. "Laughter", "Motor vehicle (road)").

    Returns:
        Category string or None if no match.
    """
    lower = class_name.lower()
    for category, keywords in AUDIO_CATEGORY_KEYWORDS:
        if any(kw in lower for kw in keywords):
            return category
    return None


# Events that shouldn't be cut during (provide smooth boundaries)
PROTECTED_EVENTS = {
    "Laughter",
    "Giggle",
    "Chuckle, chortle",
    "Baby laughter",
    "Speech",
    "Singing",
    "Cheering",
    "Applause",
}


@dataclass
class AudioEvent:
    """A detected audio event in a video segment."""

    event_class: str
    start_time: float
    end_time: float
    confidence: float

    @property
    def duration(self) -> float:
        return self.end_time - self.start_time

    @property
    def weight(self) -> float:
        """Get the interest weight for this event type."""
        return AUDIO_EVENT_WEIGHTS.get(self.event_class, 0.2)

    @property
    def is_protected(self) -> bool:
        """Check if this event should not be cut during."""
        return self.event_class in PROTECTED_EVENTS


@dataclass
class AudioAnalysisResult:
    """Result of audio content analysis for a video segment."""

    events: list[AudioEvent] = field(default_factory=list)
    audio_score: float = 0.0  # Overall score (0-1)
    has_laughter: bool = False
    has_speech: bool = False
    has_music: bool = False
    detected_categories: set[str] = field(default_factory=set)  # e.g. {"laughter", "engine"}
    energy_profile: list[float] = field(default_factory=list)  # Energy over time
    protected_ranges: list[tuple[float, float]] = field(
        default_factory=list
    )  # Ranges to avoid cutting

    def get_safe_cut_points(self, min_gap: float = 0.3, _max_gap: float = 2.0) -> list[float]:
        """Get time points that are safe for cutting (not during protected events).

        Args:
            min_gap: Minimum gap between cut points.
            _max_gap: Maximum gap between cut points (unused).

        Returns:
            List of safe cut point times.
        """
        if not self.protected_ranges:
            return []

        safe_points = []
        last_point = 0.0

        for start, end in sorted(self.protected_ranges):
            # Add cut point just before the protected range starts
            if start - last_point >= min_gap:
                safe_points.append(start - 0.1)

            # Add cut point just after the protected range ends
            if end > last_point:
                safe_points.append(end + 0.1)
                last_point = end

        return safe_points


def adjust_boundaries_for_audio(
    start: float,
    end: float,
    audio_result: AudioAnalysisResult,
    max_adjustment: float = 0.5,
) -> tuple[float, float]:
    """Adjust segment boundaries to avoid cutting during protected audio events.

    Args:
        start: Original start time.
        end: Original end time.
        audio_result: Audio analysis result with protected ranges.
        max_adjustment: Maximum adjustment per boundary in seconds.

    Returns:
        Tuple of (adjusted_start, adjusted_end).
    """
    if not audio_result.protected_ranges:
        return start, end

    new_start = start
    new_end = end

    for range_start, range_end in audio_result.protected_ranges:
        # Check if start is inside a protected range
        if range_start < start < range_end:
            # Move start to after the protected range
            if range_end - start <= max_adjustment:
                new_start = range_end + 0.1
            # Or move start to before the protected range
            elif start - range_start <= max_adjustment:
                new_start = range_start - 0.1

        # Check if end is inside a protected range
        if range_start < end < range_end:
            # Move end to after the protected range
            if range_end - end <= max_adjustment:
                new_end = range_end + 0.1
            # Or move end to before the protected range
            elif end - range_start <= max_adjustment:
                new_end = range_start - 0.1

    # Ensure valid range
    if new_end <= new_start:
        return start, end  # Revert if adjustment made range invalid

    return max(0, new_start), new_end


def get_audio_content_score(video_path: Path) -> float:
    """Quick function to get audio content score for a video.

    Args:
        video_path: Path to video file.

    Returns:
        Audio score between 0 and 1.
    """
    # Import here to avoid circular dependency
    from immich_memories.audio.content_analyzer import AudioContentAnalyzer

    analyzer = AudioContentAnalyzer(use_panns=True)
    result = analyzer.analyze(video_path)
    return result.audio_score
