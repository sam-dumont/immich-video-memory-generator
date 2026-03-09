"""Assembly configuration types and constants.

Contains exception classes, enums, dataclasses, and constants
used across the assembly pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from pathlib import Path

from immich_memories.processing.clips import ClipSegment

__all__ = [
    "AssemblyClip",
    "AssemblySettings",
    "CHUNK_SIZE",
    "CHUNKED_ASSEMBLY_THRESHOLD",
    "JobCancelledException",
    "MAX_FACE_CACHE_SIZE",
    "TitleScreenSettings",
    "TransitionType",
    "_get_rotation_filter",
]


class JobCancelledException(Exception):
    """Raised when a job is cancelled by user request."""

    pass


# Memory optimization: Chunked assembly thresholds
# When clip count exceeds threshold, process in batches to avoid OOM
CHUNKED_ASSEMBLY_THRESHOLD = 8  # Use chunking if > 8 clips
CHUNK_SIZE = 4  # Process 4 clips per batch (keeps FFmpeg memory ~1GB per batch at 4K)
MAX_FACE_CACHE_SIZE = 50  # Max entries in face detection cache to prevent unbounded growth


class TransitionType(StrEnum):
    """Video transition types."""

    CUT = "cut"
    CROSSFADE = "crossfade"
    SMART = "smart"  # Mix of cut and crossfade for variety
    NONE = "none"


@dataclass
class TitleScreenSettings:
    """Settings for title screens during assembly."""

    enabled: bool = True

    # Title screen content
    year: int | None = None
    month: int | None = None
    start_date: date | None = None
    end_date: date | None = None
    person_name: str | None = None
    birthday_age: int | None = None
    birthday_month: int | None = None  # Month of person's birthday (1-12) for celebration

    # Visual settings
    locale: str = "en"
    style_mode: str = "auto"  # "auto", "random", or specific style name
    mood: str | None = None  # Video mood for style selection

    # Timing
    title_duration: float = 3.5
    month_divider_duration: float = 2.0
    ending_duration: float = 7.0

    # Features
    show_month_dividers: bool = True
    month_divider_threshold: int = 2  # Minimum clips in a month to show divider
    show_ending_screen: bool = True
    use_first_name_only: bool = True  # Use only first name for titles


@dataclass
class AssemblySettings:
    """Settings for video assembly."""

    transition: TransitionType = TransitionType.CROSSFADE
    transition_duration: float = 0.5
    music_path: Path | None = None
    music_volume: float = 0.3
    # Stem-based ducking paths (from MusicGen/Demucs separation)
    # 2-stem mode (simpler):
    music_vocals_path: Path | None = None  # Vocals/melody stem (gets ducked during speech)
    music_accompaniment_path: Path | None = None  # Drums+bass stem (stays at full volume)
    # 4-stem mode (granular control):
    music_drums_path: Path | None = None  # Drums stem (duck least ~50%)
    music_bass_path: Path | None = None  # Bass stem (duck moderately)
    music_other_path: Path | None = None  # Other instruments stem
    add_date_overlay: bool = False
    date_format: str = "%B %d, %Y"
    output_format: str = "mp4"
    output_codec: str = "h264"
    output_crf: int = 18
    # HDR and quality preservation
    preserve_hdr: bool = True  # Use HEVC with HDR metadata
    preserve_framerate: bool = True  # Keep original frame rate (e.g., 60fps)
    target_framerate: int | None = None  # Force specific frame rate (None = auto)
    # Resolution settings
    auto_resolution: bool = True  # Auto-detect resolution from clips
    target_resolution: tuple[int, int] | None = None  # Override resolution (width, height)
    # Title screens
    title_screens: TitleScreenSettings | None = None
    # Pre-decided transitions (from clips.plan_transitions)
    # If provided, these override the automatic transition decisions
    # Format: list of "fade" or "cut" for each transition between clips
    predecided_transitions: list[str] | None = None
    # Audio loudness normalization (EBU R128)
    # Brings all clips to similar perceived loudness while preserving dynamics
    normalize_clip_audio: bool = True
    # Debug mode: preserve intermediate batch files for troubleshooting
    debug_preserve_intermediates: bool = False
    # Aspect ratio handling mode: "blur", "smart_zoom", "black_bars", "exclude"
    scale_mode: str = "blur"


@dataclass
class AssemblyClip:
    """A clip ready for assembly."""

    path: Path
    duration: float
    date: str | None = None
    asset_id: str = ""
    original_segment: ClipSegment | None = None
    # Rotation override: None = auto-detect, 0/90/180/270 = force rotation
    rotation_override: int | None = None
    # LLM analysis results for mood detection
    llm_emotion: str | None = None
    # Title screen flag - ensures fade transitions are used for this clip
    is_title_screen: bool = False
    # Audio analysis results for targeted ducking
    has_speech: bool = False  # Segment contains speech (from audio analysis)
    # Pre-decided outgoing transition (from clips.plan_transitions)
    # "fade" = crossfade to next clip, "cut" = hard cut to next clip
    # None = let assembler decide (title screens always use fade)
    outgoing_transition: str | None = None


def _get_rotation_filter(rotation: int) -> str:
    """Get FFmpeg filter string for rotation.

    Args:
        rotation: Rotation in degrees (0, 90, 180, 270).

    Returns:
        FFmpeg filter string (e.g., "transpose=1" for 90° clockwise).
    """
    rotation_filters = {
        90: "transpose=1",  # 90° clockwise
        180: "hflip,vflip",  # 180° rotation
        270: "transpose=2",  # 90° counter-clockwise (270° clockwise)
    }
    return rotation_filters.get(rotation, "")
