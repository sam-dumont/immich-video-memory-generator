"""Data models for music generation.

Contains timeline, mood, stem, and result dataclasses used by
the MusicGen API client and high-level generation functions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# =============================================================================
# Seasonal Prompts
# =============================================================================

SEASONAL_MOODS = {
    1: "winter bright energetic",
    2: "late winter hopeful driving",
    3: "spring fresh bouncy uplifting",
    4: "spring bright cheerful playful",
    5: "late spring vibrant energetic fun",
    6: "early summer carefree sunny groovy",
    7: "midsummer bright joyful energetic",
    8: "summer sunny upbeat fun",
    9: "early autumn warm upbeat groovy",
    10: "autumn cozy upbeat rhythmic",
    11: "late autumn upbeat warm",
    12: "winter holiday festive bouncy fun",
}


def get_seasonal_prompt(month: int, hemisphere: str = "north") -> str:
    """Generate seasonal mood keywords for a month.

    Args:
        month: Month number (1-12)
        hemisphere: "north" or "south" (inverts seasons for southern hemisphere)

    Returns:
        Seasonal mood string for music generation prompt
    """
    if not 1 <= month <= 12:
        return ""

    # Invert seasons for southern hemisphere
    if hemisphere.lower() == "south":
        month = (month + 6 - 1) % 12 + 1

    return SEASONAL_MOODS.get(month, "")


# =============================================================================
# Data Models
# =============================================================================


@dataclass
class ClipMood:
    """Mood information for a single video clip."""

    duration: float  # Clip duration in seconds
    mood: str  # Primary mood (e.g., "happy", "nostalgic")
    has_transition_after: bool = False  # Month divider after this clip
    transition_duration: float = 2.0  # Duration of transition (if any)
    month: int | None = None  # Month number (1-12) for seasonal prompts


@dataclass
class VideoTimeline:
    """Timeline info for music duration calculation."""

    title_duration: float = 3.5  # Opening title screen
    ending_duration: float = 7.0  # Ending screen (fade to white)
    fade_buffer: float = 5.0  # Extra for smooth fade out (5s as requested)

    # Per-clip mood information
    clips: list[ClipMood] = field(default_factory=list)

    @property
    def content_duration(self) -> float:
        """Total content duration including transitions."""
        total = 0.0
        for clip in self.clips:
            total += clip.duration
            if clip.has_transition_after:
                total += clip.transition_duration
        return total

    @property
    def total_duration(self) -> float:
        """Total music duration needed."""
        return self.title_duration + self.content_duration + self.ending_duration + self.fade_buffer

    @property
    def content_start(self) -> float:
        """When main content starts (after title)."""
        return self.title_duration

    def build_scenes(self, hemisphere: str = "north") -> list[dict]:
        """Build scene list for MusicGen soundtrack API.

        Rules:
        - Title duration is added to FIRST clip
        - Transition durations are added to the clip BEFORE the transition
        - Ending duration + fade buffer are added to LAST clip
        - Seasonal keywords are combined with video mood for richer prompts

        Args:
            hemisphere: "north" or "south" for seasonal prompt generation

        Returns:
            List of {"mood": str, "duration": int} for soundtrack API
        """
        if not self.clips:
            # No clips, just return a single upbeat scene
            return [{"mood": "upbeat", "duration": int(self.total_duration)}]

        scenes = []

        for i, clip in enumerate(self.clips):
            scene_duration = clip.duration

            # First clip: add title duration
            if i == 0:
                scene_duration += self.title_duration

            # Add transition duration if there's one after this clip
            if clip.has_transition_after:
                scene_duration += clip.transition_duration

            # Last clip: add ending duration + fade buffer
            if i == len(self.clips) - 1:
                scene_duration += self.ending_duration + self.fade_buffer

            # API requires integer seconds, minimum 5s
            scene_duration = max(5, int(scene_duration))

            # Combine video mood with seasonal keywords
            mood = clip.mood

            # Transform mellow moods to be more energetic
            # Memory videos should feel upbeat and fun, not slow and sad
            mood = _transform_mood(mood)

            if clip.month:
                seasonal = get_seasonal_prompt(clip.month, hemisphere)
                if seasonal:
                    mood = f"{mood}, {seasonal}"

            scenes.append(
                {
                    "mood": mood,
                    "duration": scene_duration,
                }
            )

        return scenes

    def build_acestep_lyrics(self) -> str:
        """Build ACE-Step section-tagged lyrics from video timeline.

        Maps video scenes to proportional section tags so ACE-Step generates
        music that follows the video's emotional arc. ACE-Step doesn't support
        explicit timestamps — sections are proportional to the overall structure.

        Mood-to-section mapping:
        - First clip (short, calm) -> [Intro]
        - Energetic/happy clips -> [Chorus]
        - Medium/neutral clips -> [Verse]
        - Clips with transition_after -> [Bridge]
        - Last clip (calm/ending) -> [Outro]

        Returns:
            Lyrics string with section tags, e.g.:
            "[Intro]\\n[Instrumental]\\n\\n[Verse]\\n[Instrumental]"
        """
        if not self.clips:
            return "[Instrumental]"

        sections = []
        num_clips = len(self.clips)

        for i, clip in enumerate(self.clips):
            mood_lower = clip.mood.lower()
            is_first = i == 0
            is_last = i == num_clips - 1

            # Determine section tag
            if is_first and num_clips > 1 and clip.duration < 20:
                section = "Intro"
            elif is_last and num_clips > 1:
                section = "Outro"
            elif clip.has_transition_after:
                section = "Bridge"
            elif any(
                w in mood_lower for w in ("energetic", "upbeat", "happy", "fun", "joyful", "bright")
            ):
                section = "Chorus"
            else:
                section = "Verse"

            sections.append(f"[{section}]\n[Instrumental]")

        return "\n\n".join(sections)

    @classmethod
    def from_clips(
        cls,
        clips: list[tuple[float, str, int | None]],  # List of (duration, mood, month)
        transitions_after: list[int] | None = None,  # Indices with transitions after
        title_duration: float = 3.5,
        ending_duration: float = 4.0,
        transition_duration: float = 2.0,
        fade_buffer: float = 5.0,
    ) -> VideoTimeline:
        """Create timeline from clip list.

        Args:
            clips: List of (duration, mood, month) tuples. Month can be None.
            transitions_after: Indices of clips that have transitions after them
            title_duration: Title screen duration
            ending_duration: Ending screen duration
            transition_duration: Duration of each transition
            fade_buffer: Extra buffer for fade out

        Returns:
            VideoTimeline instance
        """
        transitions_after = transitions_after or []

        clip_moods = []
        for i, clip_data in enumerate(clips):
            # Support both (duration, mood) and (duration, mood, month) tuples
            if len(clip_data) == 2:
                duration, mood = clip_data
                month = None
            else:
                duration, mood, month = clip_data

            clip_moods.append(
                ClipMood(
                    duration=duration,
                    mood=mood,
                    has_transition_after=(i in transitions_after),
                    transition_duration=transition_duration,
                    month=month,
                )
            )

        return cls(
            title_duration=title_duration,
            ending_duration=ending_duration,
            fade_buffer=fade_buffer,
            clips=clip_moods,
        )


def _transform_mood(mood: str) -> str:
    """Transform mood to be more upbeat for memory videos.

    Memory videos should feel upbeat and fun, not slow and sad.

    Args:
        mood: Original mood string

    Returns:
        Transformed mood string
    """
    mood_lower = mood.lower()
    mellow_words = [
        "calm",
        "peaceful",
        "serene",
        "gentle",
        "soft",
        "quiet",
        "slow",
        "relaxed",
        "mellow",
        "tender",
    ]
    sad_words = [
        "sad",
        "melancholy",
        "somber",
        "nostalgic",
        "reflective",
        "wistful",
        "bittersweet",
    ]

    if any(word in mood_lower for word in mellow_words):
        # Replace mellow with energetic but warm
        return f"upbeat warm groovy {mood}"
    elif any(word in mood_lower for word in sad_words):
        # Replace sad with warm but still positive
        return "warm uplifting hopeful"
    else:
        # For all other moods, ensure they're upbeat
        if "upbeat" not in mood_lower and "energetic" not in mood_lower:
            return f"upbeat {mood}"
    return mood


@dataclass
class MusicStems:
    """Separated audio stems from Demucs.

    Supports both 2-stem (vocals/accompaniment) and 4-stem (drums/bass/vocals/other)
    separation modes. Use `has_full_stems` to check which mode was used.
    """

    vocals: Path  # Melody/vocal stem (duck most aggressively)
    accompaniment: Path | None = None  # Combined drums+bass+other (2-stem mode)

    # 4-stem mode (htdemucs) - more granular control
    drums: Path | None = None  # Drum stem (duck least)
    bass: Path | None = None  # Bass stem (duck moderately)
    other: Path | None = None  # Other instruments (duck moderately)

    @property
    def has_full_stems(self) -> bool:
        """Check if 4-stem separation was used."""
        return self.drums is not None and self.bass is not None

    def cleanup(self):
        """Remove stem files."""
        for path in [self.vocals, self.accompaniment, self.drums, self.bass, self.other]:
            if path and path.exists():
                path.unlink()


@dataclass
class GeneratedMusic:
    """A single generated music version."""

    version_id: int
    full_mix: Path
    stems: MusicStems | None = None
    duration: float = 0.0
    prompt: str = ""
    mood: str = ""

    def cleanup(self):
        """Remove all associated files."""
        if self.full_mix.exists():
            self.full_mix.unlink()
        if self.stems:
            self.stems.cleanup()


@dataclass
class MusicGenerationResult:
    """Result containing multiple versions for user selection."""

    versions: list[GeneratedMusic]
    timeline: VideoTimeline
    mood: str
    selected_version: int | None = None  # User's choice (0-indexed)

    @property
    def selected(self) -> GeneratedMusic | None:
        """Get the selected version."""
        if self.selected_version is not None and 0 <= self.selected_version < len(self.versions):
            return self.versions[self.selected_version]
        return None

    def cleanup_unselected(self):
        """Remove unselected versions to save space."""
        for i, version in enumerate(self.versions):
            if i != self.selected_version:
                version.cleanup()


@dataclass
class StemDuckingConfig:
    """Configuration for stem-aware audio ducking."""

    # During speech: keep accompaniment (drums+bass), lower vocals/melody
    duck_vocals: bool = True
    duck_amount_db: float = -12.0

    # Crossfade duration for ducking transitions
    crossfade_ms: float = 100.0

    # Fade settings
    fade_in_seconds: float = 2.0
    fade_out_seconds: float = 3.0
