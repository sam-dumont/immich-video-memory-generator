"""Photo scoring — ranks photos for inclusion in memory videos.

Photos score lower than videos by default (via score_penalty) to ensure
videos always win in a tie. Score factors: favorites, faces, camera original.
"""

from __future__ import annotations

from immich_memories.api.models import Asset
from immich_memories.config_models import PhotoConfig

# Weight distribution for scoring factors
_WEIGHT_FAVORITE = 0.35
_WEIGHT_FACES = 0.20
_WEIGHT_CAMERA = 0.10
_WEIGHT_BASE = 0.35  # Base score for all photos


def score_photo(asset: Asset, config: PhotoConfig) -> float:
    """Score a photo for selection priority. Returns 0.0-1.0."""
    raw = _WEIGHT_BASE

    if asset.is_favorite:
        raw += _WEIGHT_FAVORITE

    if asset.people:
        raw += _WEIGHT_FACES

    if asset.exif_info and asset.exif_info.make:
        raw += _WEIGHT_CAMERA

    # Clamp raw score to [0, 1]
    raw = min(1.0, max(0.0, raw))

    # Apply penalty: photos score lower than equivalent videos
    return raw * (1.0 - config.score_penalty)
