"""Centralized application state for NiceGUI."""

from __future__ import annotations

import shutil
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

from immich_memories.api.compatibility import ApiVersionPolicy
from immich_memories.timeperiod import DateRange
from immich_memories.tracking.models import DeliveryStatus

if TYPE_CHECKING:
    from immich_memories.analysis.editorial_planner import EditorialSelection
    from immich_memories.api.models import Person, VideoClipInfo
    from immich_memories.api.person_expression import PersonExpression
    from immich_memories.cache.thumbnail_cache import ThumbnailCache
    from immich_memories.config_loader import Config
    from immich_memories.memory_types.presets import MemoryPreset
    from immich_memories.planning.auto_duration import DurationDecision
    from immich_memories.processing.timeline_budget import TimelinePlan


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
    immich_url: str = ""
    immich_api_key: str = ""
    # What the user typed into the API key field. Separate from the stored
    # key because this one is bound to a widget, and a binding is two-way:
    # whatever it holds is sent to the browser.
    api_key_entry: str = ""
    # What the user typed into the URL field. The stored key is paired with
    # immich_url; a typed URL only replaces it through apply_connection_entry.
    immich_url_entry: str = ""
    immich_api_version: ApiVersionPolicy = ApiVersionPolicy.AUTO

    # Time period selection
    time_period_mode: str = "year"  # "year", "period", or "custom"
    selected_year: int | None = None
    year_type: str = "calendar"  # "calendar" or "birthday"
    birthday: date | None = None
    period_value: int = 1
    period_unit: str = "years"  # "months" or "years"
    custom_start: date | None = None
    custom_end: date | None = None
    # Every window the memory covers. On This Day and Holiday build one per
    # year; the rest build exactly one.
    date_ranges: list[DateRange] = field(default_factory=list)

    # Person selection
    selected_person: Person | None = None
    people: list[Person] = field(default_factory=list)
    years: list[int] = field(default_factory=list)

    # Clips
    clips: list[VideoClipInfo] = field(default_factory=list)
    pipeline_selected_clips: list[VideoClipInfo] = field(default_factory=list)
    editorial_selections: tuple[EditorialSelection, ...] = ()
    selected_clip_ids: set[str] = field(default_factory=set)
    clip_segments: dict[str, tuple[float, float]] = field(default_factory=dict)
    clip_rotations: dict[str, int | None] = field(default_factory=dict)

    # Generation options
    generation_options: dict[str, Any] = field(default_factory=dict)
    output_path: Path | None = None
    # WHY: survives a page reload (state is cookie-keyed) so Step 4 can find a run
    # that finished, or is still running, while the browser page was gone.
    active_run_id: str | None = None
    generation_warning: str | None = None
    # The artifact's own duration, as the run tracker ffprobed it. Step 4 shows
    # this instead of its pre-render estimate once the file exists.
    output_duration_seconds: float = 0.0
    delivery_status: DeliveryStatus = DeliveryStatus.NOT_REQUESTED

    # Music preview (generated in Step 3, used in Step 4)
    music_preview_result: Any | None = None  # MusicGenerationResult
    # Kept so the previous preview's stems can be removed when a new one is
    # generated: a full mix plus four stems is 50-300 MB per click.
    music_preview_dir: Path | None = None

    # Cancel support
    cancel_requested: bool = False

    # Pipeline state
    review_selected_mode: bool = False
    pipeline_running: bool = False
    pipeline_result: dict[str, Any] | None = None
    timeline_plan: TimelinePlan | None = None
    editorial_render_timing: dict[str, Any] | None = None
    # Where the last cut wrote its plan; the story page reads it from there.
    editorial_attempt_dir: Path | None = None
    # The cut the pool's ticks are shown against. A tick outside it is a picture the owner
    # wants back in; None means no cut yet, so ticks are plain exclusions.
    previous_cut_asset_ids: frozenset[str] | None = None
    # The armed cut's identity, set before its worker starts. A reload polls the
    # attempt tree under this key instead of starting a second run. Sessions that
    # cut the same brief share a key, so only attempts started after the arming
    # count as this cut's.
    active_cut_key: str | None = None
    cut_armed_at: datetime | None = None
    # The stage lines this session has watched go by, bounded. Kept here rather
    # than in the widget so a reload mid-cut rebuilds the detail panel with what
    # the session already saw, the way the phase rows rebuild from the attempt.
    cut_stage_log: list[str] = field(default_factory=list)

    # Generation settings
    duration_mode: Literal["auto", "manual"] = "auto"
    target_duration: float = 10.0  # minutes; fractional values preserve exact seconds
    # What Auto settled on once a cut had its pool, and the ask it was fitted from.
    # target_duration stays the ask, so a later cut over a fuller pool can grow back
    # to it instead of starting from the last shortened answer.
    duration_decision: DurationDecision | None = None
    duration_decided_from: float | None = None
    hdr_only: bool = False
    include_live_photos: bool = False
    include_photos: bool = False
    accept_any_provenance: bool = False
    photo_assets: list[Any] = field(default_factory=list)
    selected_photo_ids: set[str] = field(default_factory=set)
    photo_duration: float = 4.0

    # Connection
    connected_user: str | None = None

    # Memory type preset (selected in Step 1)
    memory_type: str | None = None
    # Album mode: the album is the whole pool, so no date range is involved.
    album_id: str | None = None
    album_name: str | None = None
    memory_preset_params: dict[str, Any] = field(default_factory=dict)
    person_expression_error: str | None = None

    # LLM-generated title (shown in Step 3, used in Step 4)
    title_suggestion_title: str | None = None
    title_suggestion_subtitle: str | None = None
    title_suggestion_trip_type: str | None = None
    title_suggestion_map_mode: str | None = None

    # Upload-back-to-Immich settings
    upload_enabled: bool = False
    upload_album_name: str = "Memories"

    # Demo/privacy mode: blur thumbnails + video, mute speech
    demo_mode: bool = False

    # Step 2 view mode: "list" (detailed cards) or "grid" (compact thumbnails)
    clip_view_mode: str = "list"

    # Session tracking
    last_accessed: datetime | None = None

    # One session, one cut at a time: the worker writes its result back under
    # this lock, and arming a second cut while one runs is refused under it.
    # Every tab of a browser still shares this object; per-tab state is not
    # attempted here.
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    # Caches (initialized at runtime)
    thumbnail_cache: ThumbnailCache | None = None

    @property
    def date_range(self) -> DateRange | None:
        """The whole period the memory covers — for titles, filenames and labels.

        Anything that *fetches* must use `date_ranges` instead: the span of an
        On This Day is years it wants three days out of.
        """
        if not self.date_ranges:
            return None
        if len(self.date_ranges) == 1:
            return self.date_ranges[0]
        return DateRange(
            start=min(r.start for r in self.date_ranges),
            end=max(r.end for r in self.date_ranges),
        )

    @property
    def person_ids(self) -> list[str]:
        """The people this memory is narrowed to, as the one list a fetch reads.

        ``apply_preset`` resolves a preset's person filter into
        ``memory_preset_params['person_ids']``; ``selected_person`` is the
        older single pick and still answers for the paths that set it directly.
        Every read of "who is this memory about" goes through here so the fetch
        sees the same list the CLI builds from ``--person``: a group of one is
        a filter, not an absence of one.
        """
        if self.person_expression is not None:
            return []  # Expression-aware fetches pass the resolved tree separately.
        group = self.memory_preset_params.get("person_ids") or []
        if group:
            return list(group)
        return [self.selected_person.id] if self.selected_person else []

    @property
    def person_match(self) -> str:
        """Whether several selected people are intersected or unioned."""
        return str(self.memory_preset_params.get("person_match", "and"))

    @property
    def person_expression(self) -> PersonExpression | None:
        """The exact named condition, never reconstructed from a flat leaf list."""
        from immich_memories.api.person_expression import PersonExpression

        if self.person_expression_error:
            raise ValueError(self.person_expression_error)
        value = self.memory_preset_params.get("person_expression")
        return PersonExpression.from_dict(value) if value is not None else None

    def validate_person_expression_scope(self, memory_type: str | None = None) -> None:
        """Refuse unsupported combinations before fetching or using loaded media."""
        if self.person_expression is None:
            return
        product = memory_type if memory_type is not None else self.memory_type
        if product in {"person_spotlight", "trip", "album"} or (
            self.memory_preset_params.get("use_birthday") or self.year_type == "birthday"
        ):
            raise ValueError(
                "Grouped people conditions are not supported for person spotlights, "
                "birthday memories, trips or albums. Choose a date-range memory."
            )

    def resolved_person_expression(self) -> PersonExpression | None:
        """Resolve every name to all matching face IDs, preserving AND/OR groups."""
        expression = self.person_expression
        if expression is None:
            return None
        self.validate_person_expression_scope()
        from immich_memories.analysis.editorial_source import resolve_named_expression

        return resolve_named_expression(expression, self.people)

    def clear_person_expression(self) -> None:
        """An explicit flat-picker choice replaces the previous grouped condition."""
        self.memory_preset_params.pop("person_expression", None)
        self.person_expression_error = None

    def set_person_expression(self, expression: PersonExpression) -> None:
        """Store a validated named tree and its display leaves without flattening it."""
        previous = self.memory_preset_params.get("person_expression")
        previous_error = self.person_expression_error
        self.person_expression_error = None
        self.memory_preset_params["person_expression"] = expression.to_dict()
        try:
            self.resolved_person_expression()
        except ValueError:
            if previous is None:
                self.memory_preset_params.pop("person_expression", None)
            else:
                self.memory_preset_params["person_expression"] = previous
            self.person_expression_error = previous_error
            raise
        self.memory_preset_params["person_names"] = list(expression.leaf_values)
        self.memory_preset_params["person_ids"] = []
        self.selected_person = None

    def apply_preset(self, preset: MemoryPreset) -> None:
        """Adopt everything a preset decided: its windows, its length, its people.

        The person filter travels on the preset rather than being re-derived per
        card, so a Year in Review narrowed to two people asks Immich exactly
        what ``--person Riley --person Bob`` asks it -- one rule, both surfaces
        (#666, #683). Names resolve against ``people``, the roster Immich
        returned, because a filter is written in names and fetched by id.
        """
        expression = preset.person_filter.person_expression
        if expression is not None:
            self.set_person_expression(expression)
            self.validate_person_expression_scope(preset.memory_type)
        else:
            self.clear_person_expression()
        self.date_ranges = preset.date_ranges.copy()
        self.duration_decision = None
        if preset.default_duration_seconds:
            self.target_duration = preset.default_duration_seconds / 60
            self.duration_mode = "auto"
        elif preset.date_ranges:
            # ~1 min per month, ~8 min per year, for a preset with no opinion.
            self.target_duration = max(1, min(10, round(preset.date_ranges[0].days / 45)))
        if expression is None:
            self.narrow_to_people(preset.person_filter.person_names)
        if expression is None and len(preset.person_filter.person_names) > 1:
            self.memory_preset_params["person_match"] = (
                "and" if preset.person_filter.require_co_occurrence else "or"
            )

    def narrow_to_people(self, person_names: list[str]) -> None:
        """Resolve a filter's names to the ids a fetch queries with.

        Public because "All Time" has no preset to carry a filter -- no window
        can be built from a year that was never chosen -- and it still has to
        answer the same picker.
        """
        expression = self.person_expression
        if expression is not None:
            if tuple(person_names) != expression.leaf_values:
                raise ValueError("People names disagree with the grouped condition")
            self.set_person_expression(expression)
            return
        by_name = {person.name: person for person in self.people if person.name}
        wanted = [by_name[name] for name in person_names if name in by_name]
        self.memory_preset_params["person_ids"] = [person.id for person in wanted]
        # Kept for the title and the filename, which want a name and only make
        # sense when the memory is about exactly one person.
        self.selected_person = wanted[0] if len(wanted) == 1 else None

    @property
    def scope_is_selected(self) -> bool:
        """Whether step 1 has enough to go find media with.

        Album mode is the exception to "a memory is a date range": the album is
        the pool, so it carries no range and must be checked on its own.
        """
        try:
            self.resolved_person_expression()
        except ValueError:
            return False
        if self.memory_type == "album":
            return self.album_id is not None
        return self.date_range is not None

    def choose_memory_type(self, memory_type: str) -> None:
        """Switch to a memory type, dropping what the previous card collected.

        The person is the one input that used to survive: only two cards show a
        person widget, so an Riley left behind by a Person Spotlight went on
        narrowing a Year in Review with nothing on screen saying so -- the
        wizard's own version of a filter the surface cannot explain.
        """
        self.memory_type = memory_type
        self.memory_preset_params = {}
        self.person_expression_error = None
        self.selected_person = None
        if memory_type != "album":
            # A left-over album would otherwise satisfy the step 1 scope check.
            self.album_id = None
            self.album_name = None

    def reset_clips(self) -> None:
        """Reset clip-related state when changing configuration."""
        self.clips = []
        self.photo_assets = []
        self.pipeline_selected_clips = []
        self.editorial_selections = ()
        self.selected_clip_ids = set()
        self.selected_photo_ids = set()
        self.clip_segments = {}
        self.clip_rotations = {}
        self.pipeline_result = None
        self.timeline_plan = None
        self.editorial_render_timing = None
        self.editorial_attempt_dir = None
        self.previous_cut_asset_ids = None
        self.active_cut_key = None
        self.cut_armed_at = None
        self.review_selected_mode = False
        self.title_suggestion_title = None
        self.title_suggestion_subtitle = None
        self.cancel_requested = False
        self.discard_music_preview()

    def discard_music_preview(self) -> None:
        """Delete a previously generated music preview and its stems."""
        previous, self.music_preview_dir = self.music_preview_dir, None
        self.music_preview_result = None
        if previous is not None:
            shutil.rmtree(previous, ignore_errors=True)

    def get_selected_clips(self) -> list[VideoClipInfo]:
        """Get the list of currently selected clips."""
        planned = [
            clip for clip in self.pipeline_selected_clips if clip.asset.id in self.selected_clip_ids
        ]
        planned_ids = {clip.asset.id for clip in planned}
        manual = [
            clip
            for clip in self.clips
            if clip.asset.id in self.selected_clip_ids and clip.asset.id not in planned_ids
        ]
        return [*planned, *manual]

    def auto_duration_decision(self) -> DurationDecision | None:
        """The length Auto fitted to the pool, while it still answers the current ask.

        None in Manual mode, before any cut, and once the ask has moved (another
        card, another range), so a stale fit never outlives the brief it was for.
        """
        if self.duration_mode != "auto" or self.duration_decided_from != self.target_duration:
            return None
        return self.duration_decision

    @property
    def target_duration_seconds(self) -> float:
        """Return the exact total runtime requested by Auto or Manual mode."""
        decision = self.auto_duration_decision()
        return decision.seconds if decision is not None else self.target_duration * 60.0


def _same_server(a: str, b: str) -> bool:
    return a.strip().rstrip("/") == b.strip().rstrip("/")


def apply_connection_entry(state: AppState) -> str | None:
    """Move the typed URL and API key into the stored pair, or refuse and say why.

    An empty key field means "unchanged" rather than "clear it": the field
    always loads empty, because filling it would mean sending the stored key
    to the browser. The stored key only ever goes to the URL it was stored
    with: a different URL is taken only together with a key typed for it,
    otherwise whoever reaches the page could point the stored key at their
    own server and read it off the wire (#1212).
    """
    typed_key = state.api_key_entry.strip()
    typed_url = state.immich_url_entry.strip()
    state.api_key_entry = ""
    moved = typed_url and not _same_server(typed_url, state.immich_url)
    if moved and state.immich_api_key and not typed_key:
        return "The server URL changed: enter the API key for the new server."
    if moved or not typed_url:
        state.immich_url = typed_url
    if typed_key:
        state.immich_api_key = typed_key
    return None


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


def peek_app_state() -> AppState | None:
    """The current browser session's AppState, or None; never creates one.

    A plain HTTP route (the thumbnail route) must not mint a session for a
    request that carries no known cookie: NiceGUI would persist the fresh user
    dict to disk, one file per stray request (see ui/session_storage.py).
    """
    from nicegui import app

    session_id = app.storage.user.get("session_id")
    if not session_id or session_id not in _sessions:
        return None
    _sessions[session_id].last_accessed = datetime.now()
    return _sessions[session_id]


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
        state.immich_api_version = config.immich.api_version
        state.include_live_photos = config.analysis.include_live_photos
        state.include_photos = config.photos.enabled
