"""Every picture of a story as a carrier row the film could hold.

A stage that adds a shot after the draft (the model's polish refilling a seat, a close family
member's seat) fills it with a carrier row rather than a bare unit, so everything downstream of
the cut reads the added shot exactly as it reads one the draft chose.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from immich_memories.analysis.editorial_story_replies import WEIGHT_ROLE
from immich_memories.analysis.editorial_structure_material import Wall


def story_candidates(
    selection, wall: Wall, pool, units: Mapping[str, list[dict]]
) -> Callable[[str], list[dict]]:
    """`candidates_of(story_key)`: the story's pictures over every moment it holds, in order."""
    unit_by_asset = {u["asset_id"]: (family, u) for family, rows in units.items() for u in rows}
    moments_of = {episode.key: tuple(episode.moments) for episode in selection.story.episodes}
    story_of = {row["key"]: row for row in selection.story.stories}
    chapter_of = {row["episode"]: number for number, row in enumerate(selection.episodes, 1)}

    def candidates_of(story_key: str) -> list[dict]:
        story = story_of.get(story_key)
        if story is None:
            return []
        assets = [
            asset
            for episode in story.get("episodes") or ()
            for moment in moments_of.get(episode, ())
            for asset in pool.moment_assets.get(moment, ())
        ]
        return [
            _carrier(unit_by_asset[asset], story, selection, chapter_of, wall)
            for asset in dict.fromkeys(assets)
            if asset in unit_by_asset
        ]

    return candidates_of


def _carrier(entry, story, selection, chapter_of, wall: Wall) -> dict[str, Any]:
    family, unit = entry
    asset = unit["asset_id"]
    line = selection.lines.get(asset, "")
    return unit | {
        "event": family,
        "anchor": wall.anchor_label.get(family, family),
        "chapter": chapter_of.get(story["key"], 1),
        "why": f"{story['title']}: {line[:80]}",
        "event_intention": story.get("purpose") or "",
        "line": line,
        "story_episode": story["key"],
        "story_role": WEIGHT_ROLE[story["weight"]],
        "story_weight": story["weight"],
        "depicted_moment": f"source:{asset}",
        "moment_alternatives": [],
    }
