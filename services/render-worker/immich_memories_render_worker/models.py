"""Versioned, path-free job contract. The Immich key (the app's own) is an ephemeral input."""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)

from immich_memories.config_models_render import TitleScreenConfig
from immich_memories.processing.encoding_plan import HdrMode


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ImmichAccess(Contract):
    url: AnyHttpUrl
    api_key: SecretStr

    @field_validator("api_key")
    @classmethod
    def require_key(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("an Immich key is required")
        return value


class LiveCertificate(Contract):
    version: Literal["editorial-live-render-v1"]
    material: dict
    selected_interval: tuple[float, float]

    @model_validator(mode="after")
    def valid_material(self):
        from immich_memories.processing.live_material import LiveRenderMaterial

        material = LiveRenderMaterial.from_dict(self.material)
        material.displayed_interval(*self.selected_interval)
        for entry in material.source_entries:
            UUID(entry.still_id)
            UUID(entry.video_id)
        return self


class Clip(Contract):
    asset_id: UUID
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    render_mode: Literal["motion", "still"]
    render_frame_seconds: float | None = Field(default=None, ge=0)
    live: LiveCertificate | None = None
    rotation_override: Literal[0, 90, 180, 270] | None = None
    audio_categories: list[str] | None = Field(default=None, max_length=20)
    llm_emotion: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def forward_interval(self):
        if self.end <= self.start:
            raise ValueError("clip interval must move forward")
        if self.render_frame_seconds is not None and self.render_mode != "still":
            raise ValueError("a frame timestamp requires still mode")
        return self


class RenderPlan(Contract):
    clips: list[Clip] = Field(min_length=1, max_length=500)
    transition: Literal["cut", "crossfade", "smart", "none"] = "crossfade"
    transition_duration: float = Field(default=0.5, ge=0, le=3)

    @model_validator(mode="after")
    def bounded_cut(self):
        ids = [clip.asset_id for clip in self.clips]
        if len(set(ids)) != len(ids):
            raise ValueError("a cut cannot repeat an asset")
        if sum(clip.end - clip.start for clip in self.clips) > 3600:
            raise ValueError("a cut cannot exceed one hour of content")
        return self


class TitleSettings(TitleScreenConfig, Contract):
    """The title inputs the app resolved, so the worker re-derives none of them."""

    enabled: bool = False
    title: str = Field(default="", max_length=300)
    subtitle: str = Field(default="", max_length=300)


class MemorySettings(Contract):
    """What the film is, which is also half of the timing policy the binding froze."""

    memory_type: str | None = Field(default=None, max_length=64)
    target_duration_seconds: float = Field(gt=0, le=3600)
    date_start: date | None = None
    date_end: date | None = None
    person_name: str | None = Field(default=None, max_length=300)
    preset_params: dict = Field(default_factory=dict)


class TimingBinding(Contract):
    """`bind_editorial_timeline` output, verbatim. Its digest is the job's identity."""

    policy: dict
    timeline: dict
    source_ids: list[str] = Field(min_length=1, max_length=500)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class OutputSettings(Contract):
    codec: Literal["h264", "h265"] = "h264"
    resolution: Literal["720p", "1080p", "4k"] = "1080p"
    orientation: Literal["landscape", "portrait", "square"] = "landscape"
    crf: int = Field(default=23, ge=0, le=51)
    hdr_mode: HdrMode = HdrMode.SDR
    codec_policy: Literal["strict", "prefer_hardware"] = "strict"
    quality: Literal["high", "balanced", "fast"] = "balanced"


class RenderOptions(Contract):
    scale_mode: Literal["fit", "blur"] = "blur"
    add_date_overlay: bool = False
    add_place_overlay: bool = False
    privacy_mode: bool = False
    photo_duration: float = Field(default=4.0, ge=1.0, le=10.0)
    homebase_latitude: float = Field(default=0.0, ge=-90, le=90)
    homebase_longitude: float = Field(default=0.0, ge=-180, le=180)


class RenderRequest(Contract):
    version: Literal[1] = 1
    render_attempt: UUID | None = None
    memory_key: str = Field(min_length=1, max_length=256)
    immich: ImmichAccess
    plan: RenderPlan
    memory: MemorySettings
    titles: TitleSettings = Field(default_factory=TitleSettings)
    timing: TimingBinding
    # No default: an envelope that forgets the audience gate's trims is silently
    # wrong, so its absence has to be a refusal rather than an empty map.
    certified_content_intervals: dict[UUID, tuple[float, float]]
    output: OutputSettings = Field(default_factory=OutputSettings)
    options: RenderOptions = Field(default_factory=RenderOptions)


class JobStatus(Contract):
    model_config = ConfigDict(extra="forbid", frozen=True)
    job_id: UUID
    memory_key: str
    plan_digest: str
    worker_id: UUID
    submitted_at: datetime
    state: Literal["queued", "running", "ready", "failed", "consumed", "expired"] = "queued"
    phase: str = "queued"
    progress: float = 0
    message: str = ""
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    # What the app needs to re-run publish_validated_output on the bytes it gets
    # back, and to mix music against the sequence only the assembler ever saw.
    encoder: str | None = None
    encoding_plan: dict | None = None
    probe: dict | None = None
    # Sent as Repr-Digest with the film; with it, the app skips its own decode
    # of bytes the worker already decoded.
    output_sha256: str | None = None
    render_metrics: dict | None = None
    music_mute_windows: list[tuple[float, float]] | None = None
    clips: tuple[dict, ...] = ()
    degradations: tuple[str, ...] = ()
