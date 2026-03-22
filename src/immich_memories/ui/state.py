"""Centralized application state for NiceGUI."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

if TYPE_CHECKING:
    from immich_memories.api.models import Person, VideoClipInfo
    from immich_memories.cache.thumbnail_cache import ThumbnailCache
    from immich_memories.config_loader import Config
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
    config: Config | None = None
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

    # Cancel support
    cancel_requested: bool = False

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
    include_photos: bool = False
    photo_assets: list[Any] = field(default_factory=list)
    photo_duration: float = 4.0

    # Analysis depth (fast or thorough)
    analysis_depth: str = "fast"

    # Connection
    connected_user: str | None = None

    # Memory type preset (selected in Step 1)
    memory_type: str | None = None
    memory_preset_params: dict[str, Any] = field(default_factory=dict)

    # LLM-generated title (shown in Step 3, used in Step 4)
    title_suggestion_title: str | None = None
    title_suggestion_subtitle: str | None = None
    title_suggestion_trip_type: str | None = None
    title_suggestion_map_mode: str | None = None

    # Trip detection results (populated dynamically in Step 1 for trip preset)
    detected_trips: list[Any] = field(default_factory=list)

    # Upload-back-to-Immich settings
    upload_enabled: bool = False
    upload_album_name: str = "Memories"
    upload_result: dict[str, Any] | None = None

    # Demo/privacy mode: blur thumbnails + video, mute speech
    demo_mode: bool = False

    # Step 2 view mode: "list" (detailed cards) or "grid" (compact thumbnails)
    clip_view_mode: str = "list"

    # Duplicate tracking
    _duplicates_processed: bool = False

    # Session tracking
    last_accessed: datetime | None = None

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
        self.title_suggestion_title = None
        self.title_suggestion_subtitle = None
        self.cancel_requested = False

    def get_selected_clips(self) -> list[VideoClipInfo]:
        """Get the list of currently selected clips."""
        return [c for c in self.clips if c.asset.id in self.selected_clip_ids]


# Session store: maps session_id → AppState
_sessions: dict[str, AppState] = {}
_MAX_SESSIONS = 20
_SESSION_TIMEOUT_HOURS = 2


def get_app_state() -> AppState:
    """Get the AppState for the current browser session."""
    from nicegui import app

    session_id = app.storage.user.get("session_id")
    if session_id and session_id in _sessions:
        _sessions[session_id].last_accessed = datetime.now()
        return _sessions[session_id]

    session_id = str(uuid4())
    app.storage.user["session_id"] = session_id
    state = AppState()
    state.last_accessed = datetime.now()
    _sessions[session_id] = state
    return state


def cleanup_stale_sessions(max_age_hours: int = _SESSION_TIMEOUT_HOURS) -> None:
    """Remove sessions idle for longer than max_age_hours. Cap at _MAX_SESSIONS."""
    cutoff = datetime.now() - timedelta(hours=max_age_hours)
    stale = [sid for sid, s in _sessions.items() if s.last_accessed and s.last_accessed < cutoff]
    for sid in stale:
        del _sessions[sid]

    if len(_sessions) > _MAX_SESSIONS:
        by_age = sorted(_sessions.items(), key=lambda x: x[1].last_accessed or datetime.min)
        for sid, _ in by_age[: len(_sessions) - _MAX_SESSIONS]:
            del _sessions[sid]


def remove_session(session_id: str) -> None:
    """Remove a specific session (used by logout)."""
    _sessions.pop(session_id, None)


def reset_app_state() -> AppState:
    """Reset the current session's AppState."""
    from nicegui import app

    session_id = app.storage.user.get("session_id")
    if session_id:
        _sessions.pop(session_id, None)
    return get_app_state()


def ensure_config(state: AppState) -> None:
    """Lazy-load config into state on first access per session."""
    if state.config is None:
        from immich_memories.config_loader import get_config

        config = get_config()
        state.config = config
        state.immich_url = config.immich.url
        state.immich_api_key = config.immich.api_key
        state.include_live_photos = config.analysis.include_live_photos
