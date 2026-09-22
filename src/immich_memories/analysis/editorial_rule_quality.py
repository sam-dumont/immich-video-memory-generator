"""Which picture of a capture group is most entitled to carry the moment it shows.

Nothing here reads a caption or a model answer. The order is the product's: the owner's own
choice, then the frame that moves, then the people in it and how well the named one is shown,
then whether the pixels warn about it, then what the head saw, and only then the clock, with
the middle of a burst ahead of its ends, because the first frame of a burst is usually the one
taken before the thing happened.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from immich_memories.analysis.editorial_structure_budget import RESIDUAL_MIN
from immich_memories.analysis.subject_framing import framing_visibility

PIXEL_WARNINGS = ("SOFT (blurry)", "DARK", "BLOWN OUT")


@dataclass(frozen=True)
class PictureFacts:
    """What a CPU-only install knows about one picture, and where it sits in its group."""

    favourite: bool
    motion: bool
    known_people: int
    subject_rung: int
    subject_share: float
    pixel_warning: bool
    people_in_frame: bool
    place: int
    of: int
    taken: str


def picture_facts(
    asset: Any, *, line: str, place: int, of: int, residual: float | None
) -> PictureFacts:
    """Read one picture's facts off the asset, its annotation line and its motion residual.

    The line is the full one, flags and people included: the head labels and the
    subject-framing observation a rules reader needs are rendered there and stripped from the
    line the story reader sees.
    """
    visibility = framing_visibility(line)
    return PictureFacts(
        favourite=bool(asset.is_favorite),
        motion=bool(asset.is_video) or (residual is not None and residual >= RESIDUAL_MIN),
        known_people=len(asset.people or ()) or len(asset.faces or ()),
        subject_rung=visibility.rung,
        subject_share=visibility.share,
        pixel_warning=any(warning in line for warning in PIXEL_WARNINGS),
        people_in_frame="people=" in line and "people=none" not in line,
        place=place,
        of=of,
        taken=asset.file_created_at.isoformat(),
    )


def representative_key(facts: PictureFacts) -> tuple:
    """Sort key, smallest first."""
    return (
        not facts.favourite,
        not facts.motion,
        -facts.known_people,
        # A named face that is a speck against the frame's edge does not show the person a
        # frame of the same moment showing him does. Pictures naming nobody all read zero.
        -facts.subject_rung,
        -facts.subject_share,
        facts.pixel_warning,
        not facts.people_in_frame,
        # Distance from the middle of the burst, doubled so it stays whole.
        abs(2 * facts.place - (facts.of - 1)),
        facts.taken,
    )


def rule_representative_rank(
    assets: Mapping[str, Any],
    lines: Mapping[str, str],
    residuals: Mapping[str, Mapping[str, Any]],
):
    """The rank a no-model reader gives one picture of a group of ``of`` pictures."""

    def rank(asset_id: str, place: int, of: int) -> tuple:
        return representative_key(
            picture_facts(
                assets[asset_id],
                line=lines.get(asset_id, ""),
                place=place,
                of=of,
                residual=(residuals.get(asset_id) or {}).get("residual"),
            )
        )

    return rank
