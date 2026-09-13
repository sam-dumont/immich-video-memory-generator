"""Admit the pictures the owner ticked after seeing a cut.

The editor reads and argues as before; only afterwards do the owner's required pictures
join the cut, so no prompt byte and no digest input moves. A required picture takes the
story its moment belongs to, and the audience gate keeps its authority over it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from operator import itemgetter
from typing import Any

from immich_memories.analysis.editorial_story_replies import WEIGHT_ROLE

OWNER_WHY = "kept by the owner: ticked in the pool after the cut"


def admit_owner_required(
    carriers: list[dict],
    *,
    required: Sequence[str],
    units: Mapping[str, list[dict]],
    stories: Sequence[Mapping[str, Any]],
    episodes: Sequence[Any],
    anchor_label: Mapping[str, str],
    line_of: Callable[[str], str],
) -> tuple[list[dict], dict[str, list[str]]]:
    """Return the carriers with every required picture present, in capture order, and a record.

    `stories` are the weighed stories in the planner's order, `episodes` the period story's
    day episodes (each naming its moments). A required picture whose moment no story spans
    attaches to the story that is nearest in time.
    """
    record: dict[str, list[str]] = {"admitted": [], "already_carried": [], "not_in_material": []}
    if not required:
        return carriers, record
    carried = {c["asset_id"] for c in carriers} | {
        member for c in carriers for member in c.get("members") or ()
    }
    unit_of = _units_by_member(units)
    story_of_moment = _story_of_moment(stories, episodes)
    admitted = carriers.copy()
    for asset_id in required:
        if asset_id in carried:
            record["already_carried"].append(asset_id)
            continue
        found = unit_of.get(asset_id)
        if found is None:
            record["not_in_material"].append(asset_id)
            continue
        family, unit = found
        index, story = _story_for(unit, story_of_moment, stories, admitted)
        admitted.append(_owner_row(unit, family, story, index, anchor_label, line_of))
        carried.add(asset_id)
        record["admitted"].append(asset_id)
    admitted.sort(key=itemgetter("taken"))
    return admitted, record


def _units_by_member(units: Mapping[str, list[dict]]) -> dict[str, tuple[str, dict]]:
    by_member: dict[str, tuple[str, dict]] = {}
    for family, family_units in units.items():
        for unit in family_units:
            by_member.setdefault(unit["asset_id"], (family, unit))
            for member in unit.get("members") or ():
                by_member.setdefault(member, (family, unit))
    return by_member


def _story_of_moment(
    stories: Sequence[Mapping[str, Any]], episodes: Sequence[Any]
) -> dict[str, tuple[int, Mapping[str, Any]]]:
    moments_of_episode = {episode.key: tuple(episode.moments) for episode in episodes}
    return {
        moment: (index, story)
        for index, story in enumerate(stories, 1)
        for episode_key in story.get("episodes") or ()
        for moment in moments_of_episode.get(episode_key, ())
    }


def _story_for(
    unit: Mapping[str, Any],
    story_of_moment: Mapping[str, tuple[int, Mapping[str, Any]]],
    stories: Sequence[Mapping[str, Any]],
    carriers: Sequence[dict],
) -> tuple[int, Mapping[str, Any] | None]:
    placed = story_of_moment.get(unit.get("moment") or "")
    if placed is not None:
        return placed
    if not stories:
        return 0, None
    # No story spans this moment: borrow the story of the nearest carrier in time.
    nearest = min(carriers, key=lambda c: abs(_when(c) - _when(unit)), default=None)
    if nearest is not None:
        for index, story in enumerate(stories, 1):
            if story["key"] == nearest.get("story_episode"):
                return index, story
    return 1, stories[0]


def _when(row: Mapping[str, Any]) -> float:
    taken = str(row.get("taken") or "")
    try:
        return datetime.fromisoformat(taken).timestamp()
    except ValueError:
        return 0.0


def _owner_row(
    unit: Mapping[str, Any],
    family: str,
    story: Mapping[str, Any] | None,
    index: int,
    anchor_label: Mapping[str, str],
    line_of: Callable[[str], str],
) -> dict:
    weight = str(story["weight"]) if story else "none"
    return dict(unit) | {
        "event": family,
        "anchor": anchor_label.get(family, family),
        "chapter": index,
        "why": OWNER_WHY,
        "event_intention": "",
        "line": line_of(unit["asset_id"]),
        "story_episode": story["key"] if story else "",
        "story_role": WEIGHT_ROLE.get(weight, "incidental"),
        "story_weight": weight,
        "depicted_moment": unit.get("moment") or "",
        "standing": None,
        "owner_required": True,
    }
