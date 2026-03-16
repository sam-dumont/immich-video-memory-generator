"""Transition planning for video clip assembly."""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Transition buffer: extra footage before/after each segment for smooth fades
# This allows crossfade transitions without cutting into the main content
TRANSITION_BUFFER = 0.5  # seconds

# Tolerance for checking if we're at video boundaries (to account for float precision)
BOUNDARY_TOLERANCE = 0.1  # seconds


@dataclass
class ClipTransitionInfo:
    """Information about a clip for transition planning.

    This is used to pre-decide transitions BEFORE extraction,
    so we know where to add buffer footage.
    """

    asset_id: str
    start_time: float  # Segment start within the source video
    end_time: float  # Segment end within the source video
    video_duration: float  # Total source video duration
    is_title_screen: bool = False

    @property
    def can_buffer_start(self) -> bool:
        """Check if we have enough footage before start for a buffer."""
        return self.start_time >= TRANSITION_BUFFER - BOUNDARY_TOLERANCE

    @property
    def can_buffer_end(self) -> bool:
        """Check if we have enough footage after end for a buffer."""
        return (self.video_duration - self.end_time) >= TRANSITION_BUFFER - BOUNDARY_TOLERANCE


@dataclass
class TransitionPlan:
    """Pre-decided transitions and buffer requirements for a set of clips.

    Attributes:
        transitions: List of transition types between clips ("fade" or "cut").
                    Length is len(clips) - 1.
        buffer_start: List of booleans indicating if each clip needs start buffer.
        buffer_end: List of booleans indicating if each clip needs end buffer.
    """

    transitions: list[str]
    buffer_start: list[bool]
    buffer_end: list[bool]


def _decide_fade(
    transition_mode: str,
    consecutive_fades: int,
    consecutive_cuts: int,
) -> bool:
    """Decide whether to use a fade transition for smart mode."""
    if transition_mode == "crossfade":
        return True
    # SMART mode: 70% crossfade, 30% cut with consecutive limits
    use_fade = random.random() < 0.7
    if consecutive_fades >= 3:
        use_fade = False
    if consecutive_cuts >= 2:
        use_fade = True
    return use_fade


def _apply_title_screen_transition(
    i: int,
    clip_before: ClipTransitionInfo,
    clip_after: ClipTransitionInfo,
    transitions: list[str],
    buffer_start: list[bool],
    buffer_end: list[bool],
) -> tuple[int, int]:
    """Apply a fade transition involving a title screen. Returns updated (consecutive_fades, consecutive_cuts)."""
    transitions.append("fade")
    if not clip_before.is_title_screen:
        buffer_end[i] = clip_before.can_buffer_end
    if not clip_after.is_title_screen:
        buffer_start[i + 1] = clip_after.can_buffer_start
    return 1, 0  # consecutive_fades, consecutive_cuts reset


def _apply_buffer_transition(
    i: int,
    use_fade: bool,
    transitions: list[str],
    buffer_start: list[bool],
    buffer_end: list[bool],
    consecutive_fades: int,
    consecutive_cuts: int,
) -> tuple[int, int]:
    """Apply a buffered fade or cut transition. Returns updated (consecutive_fades, consecutive_cuts)."""
    if use_fade:
        transitions.append("fade")
        buffer_end[i] = True
        buffer_start[i + 1] = True
        return consecutive_fades + 1, 0
    else:
        transitions.append("cut")
        return 0, consecutive_cuts + 1


def plan_transitions(
    clips: list[ClipTransitionInfo],
    transition_mode: str = "smart",
    transition_duration: float = TRANSITION_BUFFER,
) -> TransitionPlan:
    """Pre-decide transitions based on buffer availability.

    This function determines which transitions should be crossfades vs cuts,
    taking into account whether there's enough footage for buffers.

    Rules:
    - Title screens always get fade transitions (both in and out)
    - If a clip starts at 0 (beginning of video), incoming transition must be CUT
    - If a clip ends at video end, outgoing transition must be CUT
    - For SMART mode: 70% crossfade / 30% cut, respecting above constraints
    - For CROSSFADE mode: all crossfades where possible, CUT where not
    - For CUT mode: all cuts (no buffers needed)

    Args:
        clips: List of clip info for transition planning.
        transition_mode: "smart", "crossfade", or "cut".
        transition_duration: Duration of crossfade transitions.

    Returns:
        TransitionPlan with transitions and buffer requirements.
    """
    num_clips = len(clips)
    if num_clips == 0:
        return TransitionPlan(transitions=[], buffer_start=[], buffer_end=[])

    if num_clips == 1:
        return TransitionPlan(transitions=[], buffer_start=[False], buffer_end=[False])

    transitions: list[str] = []
    buffer_start: list[bool] = [False] * num_clips
    buffer_end: list[bool] = [False] * num_clips

    # For CUT mode, no transitions need buffers
    if transition_mode == "cut":
        transitions = ["cut"] * (num_clips - 1)
        return TransitionPlan(
            transitions=transitions,
            buffer_start=buffer_start,
            buffer_end=buffer_end,
        )

    consecutive_fades = 0
    consecutive_cuts = 0

    for i in range(num_clips - 1):
        clip_before = clips[i]
        clip_after = clips[i + 1]

        if clip_before.is_title_screen or clip_after.is_title_screen:
            consecutive_fades, consecutive_cuts = _apply_title_screen_transition(
                i, clip_before, clip_after, transitions, buffer_start, buffer_end
            )
            continue

        if not clip_before.can_buffer_end or not clip_after.can_buffer_start:
            transitions.append("cut")
            consecutive_cuts += 1
            consecutive_fades = 0
            logger.debug(
                f"Transition {i}->{i + 1}: forced CUT (buffer unavailable: "
                f"out={clip_before.can_buffer_end}, in={clip_after.can_buffer_start})"
            )
            continue

        use_fade = _decide_fade(transition_mode, consecutive_fades, consecutive_cuts)
        consecutive_fades, consecutive_cuts = _apply_buffer_transition(
            i, use_fade, transitions, buffer_start, buffer_end, consecutive_fades, consecutive_cuts
        )

    logger.info(
        f"Transition plan ({transition_mode}): "
        f"{transitions.count('fade')} crossfades, {transitions.count('cut')} cuts"
    )

    return TransitionPlan(
        transitions=transitions,
        buffer_start=buffer_start,
        buffer_end=buffer_end,
    )
