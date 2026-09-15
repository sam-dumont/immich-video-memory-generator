"""How long the film runs: estimated before the render, measured after it.

The sum of the selected source holds is neither: the renderer trims the content
to the timeline's budget, the title cards add their own seconds, and the
overlapping transitions take some back. Step 3 and Step 4 show one card from one
arithmetic here, and Step 4 replaces it with the tracker's ffprobed length as
soon as the file exists.
"""

from __future__ import annotations

from typing import Any

from immich_memories.ui.pages.step2_helpers import format_duration


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
        return "Pictures & video", format_duration(content)
    estimate = estimate_film_duration(
        state.timeline_plan,
        content_seconds=content,
        content_clips=len(selected_clips),
        transition_mode=_TRANSITION_MAP.get(
            state.generation_options.get("transition", "Smart (mix of fades & cuts)"), "crossfade"
        ),
        transition_duration=state.config.defaults.transition_duration,
    )
    return "Film length", f"≈{format_duration(estimate)}"


def measured_film_label(state: Any) -> str | None:
    """The finished file's own length, ffprobed by the run tracker; None before there is one."""
    seconds = state.output_duration_seconds
    return f"Length: {format_duration(seconds)}" if seconds > 0 else None
