"""Schedule entry and scheduler configuration models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ScheduleEntry(BaseModel):
    """A single scheduled memory generation job."""

    name: str = Field(description="Human-readable schedule name")
    memory_type: str = Field(description="Memory type preset (year_in_review, on_this_day, etc.)")
    cron: str = Field(description="Cron expression (minute hour day month weekday)")
    enabled: bool = Field(default=True, description="Whether this schedule is active")
    upload_to_immich: bool = Field(default=False, description="Upload result to Immich")
    album_name: str | None = Field(
        default=None, description="Album name template ({year}, {month})"
    )
    person_names: list[str] = Field(default_factory=list, description="Person name filters")
    duration_minutes: int | None = Field(default=None, description="Override target duration")
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="Extra params passed to preset factory (year, month, season, etc.)",
    )


class SchedulerConfig(BaseModel):
    """Scheduler configuration — defines automatic generation schedules."""

    enabled: bool = Field(default=False, description="Enable the scheduler daemon")
    timezone: str = Field(default="UTC", description="Timezone for cron evaluation")
    schedules: list[ScheduleEntry] = Field(
        default_factory=list, description="List of scheduled generation jobs"
    )
