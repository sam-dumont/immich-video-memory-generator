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

        # Check buffer availability
        can_fade_out = clip_before.can_buffer_end
        can_fade_in = clip_after.can_buffer_start

        # Title screens always get fades (they have synthetic content, so always have "buffer")
        if clip_before.is_title_screen or clip_after.is_title_screen:
            # Title screens can always fade
            transitions.append("fade")
            if not clip_before.is_title_screen:
                buffer_end[i] = can_fade_out  # Only buffer real clips
            if not clip_after.is_title_screen:
                buffer_start[i + 1] = can_fade_in
            consecutive_fades += 1
            consecutive_cuts = 0
            continue

        # If either side can't buffer, must use cut
        if not can_fade_out or not can_fade_in:
            transitions.append("cut")
            consecutive_cuts += 1
            consecutive_fades = 0
            logger.debug(
                f"Transition {i}->{i + 1}: forced CUT (buffer unavailable: "
                f"out={can_fade_out}, in={can_fade_in})"
            )
            continue

        # Both sides can buffer - decide based on mode
        if transition_mode == "crossfade":
            # Always crossfade when possible
            use_fade = True
        else:
            # SMART mode: 70% crossfade, 30% cut with consecutive limits
            use_fade = random.random() < 0.7

            # Force cut if too many consecutive fades
            if consecutive_fades >= 3:
                use_fade = False

            # Force fade if too many consecutive cuts
            if consecutive_cuts >= 2:
                use_fade = True

        if use_fade:
            transitions.append("fade")
            buffer_end[i] = True
            buffer_start[i + 1] = True
            consecutive_fades += 1
            consecutive_cuts = 0
        else:
            transitions.append("cut")
            consecutive_cuts += 1
            consecutive_fades = 0

    logger.info(
        f"Transition plan ({transition_mode}): "
        f"{transitions.count('fade')} crossfades, {transitions.count('cut')} cuts"
    )

    return TransitionPlan(
        transitions=transitions,
        buffer_start=buffer_start,
        buffer_end=buffer_end,
    )
