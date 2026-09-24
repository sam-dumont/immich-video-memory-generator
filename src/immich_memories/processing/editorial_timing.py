"""Finished-film budgets for selection and interval-certified content."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from immich_memories.processing.assembly_config import TitleScreenSettings
from immich_memories.processing.timeline_budget import (
    TimelinePlan,
    finalize_selected_timeline,
    plan_timeline,
)

if TYPE_CHECKING:
    from immich_memories.generate import GenerationParams


_TITLE_FIELDS = (
    "enabled",
    "title_duration",
    "ending_duration",
    "month_divider_duration",
    "divider_mode",
    "show_month_dividers",
    "month_divider_threshold",
    "show_location_cards",
)
_VERSION = "editorial-timing-overlap-v2"


def _digest(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _validate_title_options(title: object) -> None:
    """Reject a changed title contract before any of its durations reserve time."""
    if title is None:
        return
    if not isinstance(title, dict) or set(title) != set(_TITLE_FIELDS):
        raise ValueError("Editorial timing title fields changed")
    for field in ("title_duration", "ending_duration", "month_divider_duration"):
        value = title[field]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
        ):
            raise ValueError("Invalid editorial title duration")


@dataclass(frozen=True)
class EditorialTimingPolicy:
    target_seconds: float
    memory_type: str | None
    title_settings_json: str
    transition: str
    transition_duration: float

    def __post_init__(self) -> None:
        for value in (self.target_seconds, self.transition_duration):
            if isinstance(value, bool) or not math.isfinite(value) or value < 0:
                raise ValueError("Editorial timing must be finite and nonnegative")
        if self.target_seconds == 0 or self.transition not in {"smart", "crossfade", "cut", "none"}:
            raise ValueError("Invalid editorial timing policy")
        _validate_title_options(json.loads(self.title_settings_json))

    def as_dict(self) -> dict:
        return {**asdict(self), "version": _VERSION}

    def selection_budget(self, assets: Mapping, *, expected_clip_duration: float = 4.0) -> float:
        """Fund story slots before picking; selected dates refine the divider reserve later."""
        options = json.loads(self.title_settings_json)
        return plan_timeline(
            list(assets.values()),
            TitleScreenSettings(**options) if options is not None else None,
            self.target_seconds,
            self.memory_type,
            expected_clip_duration=expected_clip_duration,
            transition_mode=self.transition,
            transition_duration=self.transition_duration,
        ).content_budget

    def resolve(self, carriers: list[dict], assets: Mapping) -> TimelinePlan:
        """Reserve selected title overhead and credit the configured transition overlap."""
        ids = [row["asset_id"] for row in carriers]
        if len(ids) != len(set(ids)) or any(key not in assets for key in ids):
            raise ValueError("Editorial timing escaped selected source metadata")
        selected = [assets[key] for key in ids]
        options = json.loads(self.title_settings_json)
        titles = TitleScreenSettings(**options) if options is not None else None
        preliminary = plan_timeline(
            selected,
            titles,
            self.target_seconds,
            self.memory_type,
            expected_content_clips=len(selected),
            transition_mode=self.transition,
            transition_duration=self.transition_duration,
        )
        return finalize_selected_timeline(
            preliminary,
            selected,
            selected_duration=sum(row["seconds"] for row in carriers),
            title_settings=titles,
            memory_type=self.memory_type,
            transition_mode=self.transition,
            transition_duration=self.transition_duration,
        )


def timing_policy_for_params(params: GenerationParams) -> EditorialTimingPolicy:
    """Build from actual per-run title inputs, without reusing a previously frozen plan."""
    from dataclasses import replace

    from immich_memories.generate_settings import build_title_settings

    raw = replace(params, timeline_plan=None)
    if params.target_duration_seconds is None:
        raise ValueError("Editorial timing requires the original requested duration")
    titles = build_title_settings(raw, raw.config, [])
    options = {key: getattr(titles, key) for key in _TITLE_FIELDS} if titles is not None else None
    return EditorialTimingPolicy(
        target_seconds=params.target_duration_seconds,
        memory_type=params.memory_type or "custom",
        title_settings_json=json.dumps(
            options, sort_keys=True, separators=(",", ":"), allow_nan=False
        ),
        transition=params.transition,
        transition_duration=params.transition_duration,
    )


def build_editorial_timing_policy(
    *,
    config,
    target_seconds,
    memory_type,
    date_start=None,
    date_end=None,
    person_name=None,
    memory_preset_params=None,
    transition=None,
    transition_duration=None,
) -> EditorialTimingPolicy:
    """Shared CLI, UI and matrix adapter for the ordinary production title policy."""
    from immich_memories.generate import GenerationParams

    return timing_policy_for_params(
        GenerationParams(
            clips=[],
            output_path=Path(),
            config=config,
            memory_type=memory_type,
            date_start=date_start,
            date_end=date_end,
            person_name=person_name,
            memory_preset_params=memory_preset_params or {},
            target_duration_seconds=target_seconds,
            transition=config.defaults.transition if transition is None else transition,
            transition_duration=(
                config.defaults.transition_duration
                if transition_duration is None
                else transition_duration
            ),
        )
    )


def bind_editorial_timeline(
    policy: EditorialTimingPolicy, timeline: TimelinePlan, source_ids: list[str]
) -> dict:
    value = {"policy": policy.as_dict(), "timeline": asdict(timeline), "source_ids": source_ids}
    return value | {"sha256": _digest(value)}


def read_editorial_timeline(binding: dict) -> TimelinePlan:
    """Reject changed bindings instead of normalizing a saved rendering contract."""
    if not isinstance(binding, dict) or set(binding) != {
        "policy",
        "timeline",
        "source_ids",
        "sha256",
    }:
        raise ValueError("Invalid editorial timing binding")
    if binding["sha256"] != _digest(
        {key: value for key, value in binding.items() if key != "sha256"}
    ):
        raise ValueError("Editorial timing binding changed")
    timeline = TimelinePlan(**binding["timeline"])
    if (
        not math.isfinite(timeline.transition_budget)
        or timeline.transition_budget < 0
        or not math.isclose(
            timeline.content_budget,
            max(0, timeline.target_duration - timeline.title_budget) + timeline.transition_budget,
        )
        or len(binding["source_ids"]) != len(set(binding["source_ids"]))
    ):
        raise ValueError("Invalid editorial timeline budget")
    return timeline


def prepare_certified_timeline(params: GenerationParams) -> None:
    """Before media work, reuse the saved policy or require ordinary replanning."""
    binding = params.editorial_render_timing
    certified = any(clip.editorial_live_manifest is not None for clip in params.clips)
    if binding is None:
        return  # Existing direct/legacy callers retain their current budget guards.
    timeline = read_editorial_timeline(binding)
    if binding["policy"] != timing_policy_for_params(params).as_dict():
        raise ValueError("Editorial timing settings changed; replan the selection before rendering")
    if [clip.asset.id for clip in params.clips] != binding["source_ids"]:
        raise ValueError("Editorial timing selection changed; replan before rendering")
    if (
        certified
        and params.timeline_plan is not None
        and asdict(params.timeline_plan) != asdict(timeline)
    ):
        raise ValueError("Saved editorial timeline was replaced after certification")
    params.timeline_plan = timeline
