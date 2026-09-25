"""How long the film runs: estimated before the render, measured after it.

The sum of the selected source holds is neither: the renderer trims the content
to the timeline's budget, the title cards add their own seconds, and the
overlapping transitions take some back. Step 3 and Step 4 show one card from one
arithmetic here, and Step 4 replaces it with the tracker's ffprobed length as
soon as the file exists.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from immich_memories.ui.i18n import tr
from immich_memories.ui.pages.step2_helpers import format_duration


def _bound_film_length(state, selected_clips) -> float:
    from immich_memories.processing.assembly_config import AssemblyClip, TitleScreenSettings
    from immich_memories.processing.timeline_preview import preview_timeline
    from immich_memories.ui.pages._step4_generate import _TRANSITION_MAP

    policy = state.editorial_render_timing["policy"]
    clips = []
    for clip in selected_clips:
        start, end = state.clip_segments.get(clip.asset.id, (0, clip.duration_seconds or 5))
        exif = clip.asset.exif_info
        clips.append(
            AssemblyClip(
                Path(),
                end - start,
                asset_id=clip.asset.id,
                date=clip.asset.file_created_at.isoformat(),
                latitude=exif.latitude if exif else None,
                longitude=exif.longitude if exif else None,
                location_name=exif.city if exif else None,
            )
        )
    titles = TitleScreenSettings(
        **(json.loads(policy.get("title_settings_json") or "null") or {}),
        memory_type=policy.get("memory_type"),
    )
    _, seconds = preview_timeline(
        clips,
        state.timeline_plan,
        titles,
        _TRANSITION_MAP.get(state.generation_options.get("transition"), policy["transition"]),
        state.config.defaults.transition_duration,
    )
    return seconds


def film_length_stat(state: Any, selected_clips: list) -> tuple[str, str]:
    """The length card's label and value: the film when a timeline exists, else what is known.

    The value is an estimate and is marked as one; smart transitions draw their
    overlap per boundary, so only the rendered file has an exact duration.
    """
    from immich_memories.processing.timeline_budget import estimate_film_duration
    from immich_memories.ui.pages._step4_generate import _TRANSITION_MAP

    # The source seconds the owner ticked, before the renderer trims them to budget.
    content = sum(
        end - start
        for clip in selected_clips
        for start, end in (state.clip_segments.get(clip.asset.id, (0, clip.duration_seconds or 5)),)
    )
    if state.timeline_plan is None or state.config is None:
        return tr("Pictures & video"), format_duration(content)
    if state.editorial_render_timing is not None:
        return tr("Film length"), f"≈{format_duration(_bound_film_length(state, selected_clips))}"
    estimate = estimate_film_duration(
        state.timeline_plan,
        content_seconds=content,
        content_clips=len(selected_clips),
        transition_mode=_TRANSITION_MAP.get(
            state.generation_options.get("transition", "Smart (mix of fades & cuts)"), "crossfade"
        ),
        transition_duration=state.config.defaults.transition_duration,
    )
    return tr("Film length"), f"≈{format_duration(estimate)}"


def measured_film_label(state: Any) -> str | None:
    """The finished file's own length, ffprobed by the run tracker; None before there is one."""
    seconds = state.output_duration_seconds
    return tr("Length: {duration}", duration=format_duration(seconds)) if seconds > 0 else None
