"""The outside hosts a run may contact, none of them by default.

A default run reaches the user's Immich server, the endpoints the user wrote
down themselves, and nothing else. Each switch here buys back one third-party
host, and every one of them learns something about where the pictures were
taken.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# Named here so the docs, the preflight rows and the code cannot drift apart.
GEOCODING_HOST = "nominatim.openstreetmap.org"
MAP_TILE_HOST = "server.arcgisonline.com"
FONT_HOST = "cdn.jsdelivr.net"


class NetworkConfig(BaseModel):
    """Third-party hosts this install is allowed to reach."""

    geocoding: bool = Field(
        default=False,
        description=(
            f"Reverse geocode through {GEOCODING_HOST}: better trip names, and place "
            "names in the film's language. Sends each trip centroid and the rounded "
            "coordinates of the places on the cut"
        ),
    )
    map_tiles: bool = Field(
        default=False,
        description=(
            f"Fetch satellite tiles from {MAP_TILE_HOST}: the trip fly-over, the static "
            "trip map and the map behind location cards. Sends tile coordinates covering "
            "the trip area and the home base"
        ),
    )
    font_downloads: bool = Field(
        default=False,
        description=(
            f"Fetch a title font from {FONT_HOST} when it is neither bundled with the "
            "app nor in ~/.immich-memories/fonts"
        ),
    )
