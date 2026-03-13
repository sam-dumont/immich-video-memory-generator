"""Centralized application state for NiceGUI."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from immich_memories.api.models import Person, VideoClipInfo
    from immich_memories.cache.thumbnail_cache import ThumbnailCache
    from immich_memories.timeperiod import DateRange


@dataclass
class AppState:
    """Application state shared across all pages.

    This replaces Streamlit's session_state with a proper dataclass
    that can be bound to NiceGUI components.
    """

    # Current wizard step (1-4)
    step: int = 1

    # Configuration
    config_saved: bool = False
    immich_url: str = ""
    immich_api_key: str = ""

    # Time period selection
    time_period_mode: str = "year"  # "year", "period", or "custom"
    selected_year: int | None = None
    year_type: str = "calendar"  # "calendar" or "birthday"
    birthday: date | None = None
    pending_birthday: date | None = None
    period_value: int = 1
    period_unit: str = "years"  # "months" or "years"
    custom_start: date | None = None
    custom_end: date | None = None
    date_range: DateRange | None = None

    # Person selection
    selected_person: Person | None = None
    people: list[Person] = field(default_factory=list)
    years: list[int] = field(default_factory=list)

    # Clips
    clips: list[VideoClipInfo] = field(default_factory=list)
    selected_clip_ids: set[str] = field(default_factory=set)
    clip_segments: dict[str, tuple[float, float]] = field(default_factory=dict)
    clip_rotations: dict[str, int | None] = field(default_factory=dict)

    # Generation options
    generation_options: dict[str, Any] = field(default_factory=dict)
    processing: bool = False
    output_path: Path | None = None

    # Music preview (generated in Step 3, used in Step 4)
    music_preview_result: Any | None = None  # MusicGenerationResult
    music_generating: bool = False

    # Pipeline state
    auto_analyze_pending: bool = False
    review_selected_mode: bool = False
    pipeline_running: bool = False
    pipeline_result: dict[str, Any] | None = None
    pipeline_config: dict[str, Any] = field(default_factory=dict)

    # Generation settings
    target_duration: int = 10  # minutes
    avg_clip_duration: int = 5  # seconds per clip
    hdr_only: bool = False
    prioritize_favorites: bool = True
    analyze_all: bool = False
    max_non_favorite_pct: int = 25
    max_non_favorite_ratio: float = 0.25
    include_live_photos: bool = False

    # Connection
    connected_user: str | None = None

    # Memory type preset (selected in Step 1)
    memory_type: str | None = None
    memory_preset_params: dict[str, Any] = field(default_factory=dict)

    # Trip detection results (populated dynamically in Step 1 for trip preset)
    detected_trips: list[Any] = field(default_factory=list)

    # Upload-back-to-Immich settings
    upload_enabled: bool = False
    upload_album_name: str = "Memories"
    upload_result: dict[str, Any] | None = None

    # Duplicate tracking
    _duplicates_processed: bool = False

    # Caches (initialized at runtime)
    thumbnail_cache: ThumbnailCache | None = None
    analysis_cache: Any = None  # AnalysisCache

    def reset_clips(self) -> None:
        """Reset clip-related state when changing configuration."""
        self.clips = []
        self.selected_clip_ids = set()
        self.clip_segments = {}
        self.clip_rotations = {}
        self.pipeline_result = None
        self.review_selected_mode = False
        self._duplicates_processed = False

    def get_selected_clips(self) -> list[VideoClipInfo]:
        """Get the list of currently selected clips."""
        return [c for c in self.clips if c.asset.id in self.selected_clip_ids]


# Global state instance - shared across all pages
# This is created once when the app starts
_app_state: AppState | None = None


def get_app_state() -> AppState:
    """Get the global application state instance."""
    global _app_state
    if _app_state is None:
        _app_state = AppState()
    return _app_state


def reset_app_state() -> AppState:
    """Reset the global application state to defaults."""
    global _app_state
    _app_state = AppState()
    return _app_state
