"""Compare saved reader outcomes without confusing picture changes with lost occasions.

Inventories provide known membership, not an exhaustive semantic truth. A retained
member proves coverage; an unmapped story remains a question for the contact sheets.
The returned details contain library IDs and titles and belong in private artifacts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _members(carrier: dict) -> set[str]:
    return {str(carrier["asset_id"]), *map(str, carrier.get("members") or ())}


def _occasion_members(attempt: Path, story: dict, carriers: list[dict]) -> tuple[set[str], bool]:
    members = set().union(
        *(_members(c) for c in carriers if c.get("story_episode") == story["episode"])
    )
    days = story.get("day_episodes") or []
    complete = bool(days)
    for day in days:
        path = attempt / "derived-decisions" / f"moment-inventory-{day}.private.json"
        if not path.exists():
            complete = False
            continue
        inventory = _read(path)
        offered = {str(a) for page in inventory.get("pages", ()) for a in page.get("offered", ())}
        members.update(offered)
        complete = complete and len(offered) >= inventory.get("source_units", 0)
    return members, complete


def compare_attempts(reference: Path, current: Path) -> dict[str, Any]:
    """Return cross-reader retention and known refusals from two final saved plans."""
    old, new = _read(reference / "plan.private.json"), _read(current / "plan.private.json")
    before, after = old.get("carriers", []), new.get("carriers", [])
    old_ids = [str(c["asset_id"]) for c in before]
    new_ids = [str(c["asset_id"]) for c in after]
    selected_members = set().union(*(_members(c) for c in after))
    favourites = {str(c["asset_id"]) for c in before if c.get("favourite")}
    occasions = []
    for story in (old.get("story") or {}).get("episodes", ()):
        if (
            story.get("weight") not in {"dominant", "major", "minor"}
            or story.get("granted", 0) <= 0
        ):
            continue
        members, complete = _occasion_members(reference, story, before)
        occasions.append(
            {
                "episode": story["episode"],
                "title": story.get("title", ""),
                "weight": story["weight"],
                "known_members": len(members),
                "inventory_complete": complete,
                "retained": bool(members & selected_members),
            }
        )
    verdicts = (old.get("shareability") or {}).get("verdicts") or {}
    refused = {
        str(asset) for asset, verdict in verdicts.items() if verdict.get("verdict") == "do_not_show"
    }
    old_seconds, new_seconds = old.get("content_seconds"), new.get("content_seconds")
    return {
        "reference_carriers": len(old_ids),
        "carriers": len(new_ids),
        "shared_assets": len(set(old_ids) & set(new_ids)),
        "ordered_selection_identical": old_ids == new_ids,
        "reference_favourites": len(favourites),
        "favourites_kept": len(favourites & set(new_ids)),
        "funded_reference_occasions": len(occasions),
        "known_reference_occasions_retained": sum(o["retained"] for o in occasions),
        "occasion_mapping_complete": all(o["inventory_complete"] for o in occasions),
        "occasions": occasions,
        "reference_refused_assets_selected": sorted(refused & selected_members),
        "content_delta_seconds": None
        if old_seconds is None or new_seconds is None
        else round(new_seconds - old_seconds, 2),
        "reference_has_live_motion": any(c.get("kind") == "live-motion" for c in before),
        "has_live_motion": any(c.get("kind") == "live-motion" for c in after),
    }
