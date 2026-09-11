"""Canonical Live source lineage and the positive intervals that actually play."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, replace
from typing import Any

VERSION = "live-render-material-v1"


@dataclass(frozen=True)
class LiveSourceEntry:
    """One still's source association, including an explicitly empty shutter slice."""

    still_id: str
    video_id: str
    shutter_timestamp: float
    start: float
    end: float

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, str) or not value.strip()
            for value in (self.still_id, self.video_id)
        ):
            raise ValueError("Live material requires real nonblank source IDs")
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            for value in (self.shutter_timestamp, self.start, self.end)
        ):
            raise ValueError("Live material timing must be finite")
        if self.start < 0 or self.end < self.start:
            raise ValueError("Live material intervals cannot be negative or reversed")


@dataclass(frozen=True)
class LiveRenderMaterial:
    """Keep every still alias; empty slices have lineage but no displayed segment."""

    source_entries: tuple[LiveSourceEntry, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.source_entries, tuple)
            or not self.source_entries
            or any(not isinstance(entry, LiveSourceEntry) for entry in self.source_entries)
        ):
            raise ValueError("Live material needs immutable source entries")
        stills = self.still_ids
        order = tuple((entry.shutter_timestamp, entry.still_id) for entry in self.source_entries)
        if len(set(stills)) != len(stills) or order != tuple(sorted(order)):
            raise ValueError("Live material still aliases must be unique and chronological")
        if not self.segments:
            raise ValueError("Live material has no positive interval")
        if len(set(self.video_ids)) != len(self.video_ids):
            raise ValueError("Live material has conflicting positive repeated video segments")
        shutters: dict[str, float] = {}
        for entry in self.source_entries:
            if entry.video_id in shutters and shutters[entry.video_id] != entry.shutter_timestamp:
                raise ValueError("Live companion aliases disagree on source shutter time")
            shutters[entry.video_id] = entry.shutter_timestamp

    @property
    def segments(self) -> tuple[LiveSourceEntry, ...]:
        return tuple(entry for entry in self.source_entries if entry.end > entry.start)

    @property
    def still_ids(self) -> tuple[str, ...]:
        return tuple(entry.still_id for entry in self.source_entries)

    @property
    def video_ids(self) -> tuple[str, ...]:
        return tuple(entry.video_id for entry in self.segments)

    @property
    def trim_points(self) -> tuple[tuple[float, float], ...]:
        return tuple((entry.start, entry.end) for entry in self.segments)

    @property
    def shutter_timestamps(self) -> tuple[float, ...]:
        return tuple(entry.shutter_timestamp for entry in self.segments)

    @property
    def duration_seconds(self) -> float:
        return sum(entry.end - entry.start for entry in self.segments)

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": VERSION,
            "source_entries": [asdict(entry) for entry in self.source_entries],
        }

    def identity(self) -> str:
        return hashlib.sha256(
            json.dumps(
                self.as_dict(),
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode()
        ).hexdigest()

    @classmethod
    def from_dict(cls, value: Any) -> LiveRenderMaterial:
        if (
            not isinstance(value, dict)
            or set(value) != {"version", "source_entries"}
            or (value["version"] != VERSION or not isinstance(value["source_entries"], list))
        ):
            raise ValueError("Invalid Live material manifest")
        fields = {"still_id", "video_id", "shutter_timestamp", "start", "end"}
        if any(not isinstance(row, dict) or set(row) != fields for row in value["source_entries"]):
            raise ValueError("Invalid Live material source entry")
        return cls(tuple(LiveSourceEntry(**row) for row in value["source_entries"]))

    def assert_arrays(self, *, still_ids, video_ids, trim_points, shutter_timestamps) -> None:
        if (
            tuple(still_ids or ()) != self.still_ids
            or tuple(video_ids or ()) != self.video_ids
            or tuple(tuple(pair) for pair in (trim_points or ())) != self.trim_points
            or tuple(shutter_timestamps or ()) != self.shutter_timestamps
        ):
            raise ValueError("Live material arrays disagree with the canonical manifest")

    def displayed_interval(self, start: float, end: float) -> tuple[LiveSourceEntry, ...]:
        """Project a selected interval of the concatenation into real source intervals."""
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            for value in (start, end)
        ) or (start < 0 or end <= start or end > self.duration_seconds):
            raise ValueError("Selected Live interval exceeds canonical material")
        position = 0.0
        shown = []
        durations = []
        for entry in self.segments:
            durations.append(entry.end - entry.start)
            # Match duration_seconds' summation, including its final endpoint.
            # Reassociating source offsets drifts outside exact source bounds.
            stop = sum(durations)
            left, right = max(start, position), min(end, stop)
            if right > left:
                shown.append(
                    replace(
                        entry,
                        start=entry.start
                        if left == position
                        else min(entry.end, max(entry.start, entry.start + (left - position))),
                        end=entry.end
                        if right == stop
                        else min(entry.end, max(entry.start, entry.start + (right - position))),
                    )
                )
            position = stop
        return tuple(shown)

    def selected_interval(
        self,
        seconds: float,
        *,
        start: float = 0.0,
        end: float | None = None,
        raw_seconds: float | None = None,
    ) -> tuple[float, float]:
        """Use the projector's sole permitted centisecond-to-source adjustment."""
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            for value in (seconds, start)
        ) or (seconds <= 0 or start < 0):
            raise ValueError("Selected Live timing must be finite and positive")
        end = start + seconds if end is None else end
        if isinstance(end, bool) or not isinstance(end, (int, float)) or not math.isfinite(end):
            raise ValueError("Selected Live end must be finite")
        if abs(end - start - seconds) > 1e-6:
            raise ValueError("Selected Live interval disagrees with its duration")
        if end > self.duration_seconds:
            if (
                start == 0
                and end == round(self.duration_seconds, 2)
                and (
                    not isinstance(raw_seconds, bool)
                    and raw_seconds == round(self.duration_seconds, 2)
                )
            ):
                end = self.duration_seconds
            else:
                raise ValueError("Selected Live interval exceeds canonical material")
        self.displayed_interval(start, end)
        return start, end
