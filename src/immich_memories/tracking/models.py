"""Data models for run tracking."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal


@dataclass
class SystemInfo:
    """System specifications captured at run time."""

    platform: str  # "darwin", "linux", "windows"
    platform_version: str  # "Darwin 25.1.0"
    python_version: str
    machine_arch: str  # "arm64", "x86_64"

    # Hardware
    cpu_brand: str | None = None
    cpu_cores: int = 0
    ram_gb: float = 0.0

    # GPU/Acceleration
    hw_accel_backend: str | None = None  # "apple", "nvidia", etc.
    gpu_name: str | None = None
    vram_mb: int = 0

    # Dependencies
    ffmpeg_version: str | None = None
    opencv_version: str | None = None
    taichi_available: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "platform": self.platform,
            "platform_version": self.platform_version,
            "python_version": self.python_version,
            "machine_arch": self.machine_arch,
            "cpu_brand": self.cpu_brand,
            "cpu_cores": self.cpu_cores,
            "ram_gb": self.ram_gb,
            "hw_accel_backend": self.hw_accel_backend,
            "gpu_name": self.gpu_name,
            "vram_mb": self.vram_mb,
            "ffmpeg_version": self.ffmpeg_version,
            "opencv_version": self.opencv_version,
            "taichi_available": self.taichi_available,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SystemInfo:
        """Create from dictionary."""
        return cls(
            platform=data.get("platform", "unknown"),
            platform_version=data.get("platform_version", ""),
            python_version=data.get("python_version", ""),
            machine_arch=data.get("machine_arch", ""),
            cpu_brand=data.get("cpu_brand"),
            cpu_cores=data.get("cpu_cores", 0),
            ram_gb=data.get("ram_gb", 0.0),
            hw_accel_backend=data.get("hw_accel_backend"),
            gpu_name=data.get("gpu_name"),
            vram_mb=data.get("vram_mb", 0),
            ffmpeg_version=data.get("ffmpeg_version"),
            opencv_version=data.get("opencv_version"),
            taichi_available=data.get("taichi_available", False),
        )

    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict())

    @classmethod
    def from_json(cls, json_str: str) -> SystemInfo:
        """Create from JSON string."""
        return cls.from_dict(json.loads(json_str))


@dataclass
class PhaseStats:
    """Timing statistics for a single pipeline phase."""

    phase_name: str  # "discovery", "analysis", "clip_extraction", etc.
    started_at: datetime
    completed_at: datetime | None = None
    duration_seconds: float = 0.0
    items_processed: int = 0
    items_total: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)
    extra_metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "phase_name": self.phase_name,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "duration_seconds": self.duration_seconds,
            "items_processed": self.items_processed,
            "items_total": self.items_total,
            "errors": self.errors,
            "extra_metrics": self.extra_metrics,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PhaseStats:
        """Create from dictionary."""
        return cls(
            phase_name=data["phase_name"],
            started_at=datetime.fromisoformat(data["started_at"]),
            completed_at=(
                datetime.fromisoformat(data["completed_at"]) if data.get("completed_at") else None
            ),
            duration_seconds=data.get("duration_seconds", 0.0),
            items_processed=data.get("items_processed", 0),
            items_total=data.get("items_total", 0),
            errors=data.get("errors", []),
            extra_metrics=data.get("extra_metrics", {}),
        )


@dataclass
class RunMetadata:
    """Metadata for a single pipeline run."""

    run_id: str  # Format: YYYYMMDD_HHMMSS_XXXX
    created_at: datetime
    completed_at: datetime | None = None
    status: Literal["running", "completed", "failed", "cancelled"] = "running"

    # Input parameters
    person_name: str | None = None
    person_id: str | None = None
    date_range_start: date | None = None
    date_range_end: date | None = None
    target_duration_minutes: int = 10

    # Output info
    output_path: str | None = None
    output_size_bytes: int = 0
    output_duration_seconds: float = 0.0

    # Statistics
    clips_analyzed: int = 0
    clips_selected: int = 0
    errors_count: int = 0

    # System info
    system_info: SystemInfo | None = None

    # Phase statistics (populated when loaded from DB)
    phases: list[PhaseStats] = field(default_factory=list)

    @property
    def total_duration_seconds(self) -> float:
        """Get total run duration in seconds."""
        if self.completed_at and self.created_at:
            return (self.completed_at - self.created_at).total_seconds()
        return sum(p.duration_seconds for p in self.phases)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "run_id": self.run_id,
            "created_at": self.created_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "status": self.status,
            "person_name": self.person_name,
            "person_id": self.person_id,
            "date_range_start": (
                self.date_range_start.isoformat() if self.date_range_start else None
            ),
            "date_range_end": (self.date_range_end.isoformat() if self.date_range_end else None),
            "target_duration_minutes": self.target_duration_minutes,
            "output_path": self.output_path,
            "output_size_bytes": self.output_size_bytes,
            "output_duration_seconds": self.output_duration_seconds,
            "clips_analyzed": self.clips_analyzed,
            "clips_selected": self.clips_selected,
            "errors_count": self.errors_count,
            "system_info": self.system_info.to_dict() if self.system_info else None,
            "phases": [p.to_dict() for p in self.phases],
            "total_duration_seconds": self.total_duration_seconds,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunMetadata:
        """Create from dictionary."""
        return cls(
            run_id=data["run_id"],
            created_at=datetime.fromisoformat(data["created_at"]),
            completed_at=(
                datetime.fromisoformat(data["completed_at"]) if data.get("completed_at") else None
            ),
            status=data.get("status", "running"),
            person_name=data.get("person_name"),
            person_id=data.get("person_id"),
            date_range_start=(
                date.fromisoformat(data["date_range_start"])
                if data.get("date_range_start")
                else None
            ),
            date_range_end=(
                date.fromisoformat(data["date_range_end"]) if data.get("date_range_end") else None
            ),
            target_duration_minutes=data.get("target_duration_minutes", 10),
            output_path=data.get("output_path"),
            output_size_bytes=data.get("output_size_bytes", 0),
            output_duration_seconds=data.get("output_duration_seconds", 0.0),
            clips_analyzed=data.get("clips_analyzed", 0),
            clips_selected=data.get("clips_selected", 0),
            errors_count=data.get("errors_count", 0),
            system_info=(
                SystemInfo.from_dict(data["system_info"]) if data.get("system_info") else None
            ),
            phases=[PhaseStats.from_dict(p) for p in data.get("phases", [])],
        )

    def to_json(self) -> str:
        """Convert to JSON string (for run_metadata.json file)."""
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_json(cls, json_str: str) -> RunMetadata:
        """Create from JSON string."""
        return cls.from_dict(json.loads(json_str))
