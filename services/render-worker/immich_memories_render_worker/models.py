"""Versioned, path-free job contract. The scoped Immich key is an ephemeral input."""

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
    title: str = Field(default="", max_length=300)
    subtitle: str = Field(default="", max_length=300)
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


class OutputSettings(Contract):
    codec: Literal["h264", "h265"] = "h264"
    resolution: Literal["720p", "1080p", "4k"] = "1080p"
    orientation: Literal["landscape", "portrait"] = "landscape"
    crf: int = Field(default=23, ge=0, le=51)


class RenderRequest(Contract):
    version: Literal[1] = 1
    request_id: UUID
    memory_key: str = Field(min_length=1, max_length=256)
    immich: ImmichAccess
    plan: RenderPlan
    output: OutputSettings = Field(default_factory=OutputSettings)


class JobStatus(Contract):
    model_config = ConfigDict(extra="forbid", frozen=True)
    job_id: UUID
    memory_key: str
    state: Literal["queued", "running", "ready", "failed", "consumed"] = "queued"
    phase: str = "queued"
    progress: float = 0
    message: str = ""
    error: str | None = None
