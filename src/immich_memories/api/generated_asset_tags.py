"""Durable provenance for completed films uploaded back into the source library."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

GENERATED_MEMORY_TAG = "immich-memories/generated"


async def generated_asset_ids(request: Callable[..., Any]) -> frozenset[str]:
    """Every asset the library still holds under the provenance tag.

    Reads only: a library that was never tagged, or a key without tag scope, answers
    with an empty set rather than creating the tag. `GET /tags` lists what exists;
    `PUT /tags` would upsert, which a pool builder must never do.
    """
    tags = await request("GET", "/tags")
    matches = (
        [tag for tag in tags if isinstance(tag, dict) and tag.get("value") == GENERATED_MEMORY_TAG]
        if isinstance(tags, list)
        else []
    )
    if not matches or not isinstance(matches[0].get("id"), str):
        return frozenset()
    found: set[str] = set()
    page: int | None = 1
    while page:
        result = await request(
            "POST",
            "/search/metadata",
            json={"tagIds": [matches[0]["id"]], "size": 250, "page": page},
        )
        assets = (result or {}).get("assets", {})
        found.update(
            row["id"] for row in assets.get("items", ()) if isinstance(row, dict) and row.get("id")
        )
        page = assets.get("nextPage")
    return frozenset(found)
