"""Versioned, path-free job contract. The scoped Immich key is an ephemeral input."""

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


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ImmichAccess(Contract):
    url: AnyHttpUrl
    api_key: SecretStr

    @field_validator("api_key")
    @classmethod
    def require_key(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("a scoped Immich key is required")
        return value


class Clip(Contract):
    asset_id: UUID
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    render_mode: Literal["motion", "still"]
    render_frame_seconds: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def forward_interval(self):
        if self.end <= self.start:
            raise ValueError("clip interval must move forward")
        if self.render_frame_seconds is not None and self.render_mode != "still":
            raise ValueError("a frame timestamp requires still mode")
        return self


class RenderPlan(Contract):
    clips: list[Clip] = Field(min_length=1, max_length=500)
    transition: Literal["cut", "crossfade", "none"] = "crossfade"
    transition_duration: float = Field(default=0.5, ge=0, le=3)

    @model_validator(mode="after")
    def bounded_cut(self):
        ids = [clip.asset_id for clip in self.clips]
        if len(set(ids)) != len(ids):
            raise ValueError("a cut cannot repeat an asset")
        if sum(clip.end - clip.start for clip in self.clips) > 3600:
            raise ValueError("a cut cannot exceed one hour of content")
        return self


class TitleSettings(Contract):
    """The title inputs the app resolved, so the worker re-derives none of them."""

    enabled: bool = False
    title: str = Field(default="", max_length=300)
    subtitle: str = Field(default="", max_length=300)
    # Every bound below mirrors TitleScreenConfig, so an accepted envelope is
    # always assignable onto the worker's own config.
    locale: Literal["en", "fr", "auto"] = "auto"
    style_mode: Literal["auto", "random"] = "auto"
    title_duration: float = Field(default=3.5, ge=1.0, le=10.0)
    ending_duration: float = Field(default=7.0, ge=2.0, le=15.0)
    month_divider_duration: float = Field(default=2.0, ge=1.0, le=5.0)
    month_divider_threshold: int = Field(default=2, ge=1, le=10)
    show_month_dividers: bool = True


class MemorySettings(Contract):
    """What the film is, which is also half of the timing policy the binding froze."""

    memory_type: str | None = Field(default=None, max_length=64)
    target_duration_seconds: float = Field(gt=0, le=3600)
    date_start: date | None = None
    date_end: date | None = None


class TimingBinding(Contract):
    """`bind_editorial_timeline` output, verbatim. Its digest is the job's identity."""

    policy: dict
    timeline: dict
    source_ids: list[str] = Field(min_length=1, max_length=500)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class OutputSettings(Contract):
    codec: Literal["h264", "h265"] = "h264"
    resolution: Literal["720p", "1080p", "4k"] = "1080p"
    orientation: Literal["landscape", "portrait"] = "landscape"
    crf: int = Field(default=23, ge=0, le=51)


class RenderRequest(Contract):
    version: Literal[1] = 1
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
    render_metrics: dict | None = None
    music_mute_windows: list[tuple[float, float]] | None = None
    degradations: tuple[str, ...] = ()
