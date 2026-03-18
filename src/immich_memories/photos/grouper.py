"""Photo grouper — detects series and assigns animation modes.

Groups consecutive photos by temporal proximity, then assigns
animation modes based on content (faces, orientation, series size).
"""

from __future__ import annotations

from immich_memories.api.models import Asset
from immich_memories.config_models import PhotoConfig
from immich_memories.photos.models import AnimationMode, PhotoGroup


class PhotoGrouper:
    """Groups photos into singles and series for animation."""

    def __init__(self, config: PhotoConfig) -> None:
        self._gap = config.series_gap_seconds
        self._collage_enabled = config.enable_collage

    def group(self, photos: list[Asset]) -> list[PhotoGroup]:
        """Group photos by temporal proximity, then assign animation modes."""
        if not photos:
            return []

        clusters = self._temporal_cluster(photos)
        groups = []
        for cluster in clusters:
            # Subsample large clusters into chunks of at most 4
            if len(cluster) > 4:
                for chunk in self._subsample(cluster, 4):
                    groups.append(self._build_group(chunk))
            else:
                groups.append(self._build_group(cluster))
        return groups

    def _temporal_cluster(self, photos: list[Asset]) -> list[list[Asset]]:
        """Walk chronologically, split on gaps > threshold."""
        sorted_photos = sorted(photos, key=lambda a: a.file_created_at)
        clusters: list[list[Asset]] = [[sorted_photos[0]]]

        for photo in sorted_photos[1:]:
            prev = clusters[-1][-1]
            gap = (photo.file_created_at - prev.file_created_at).total_seconds()
            if gap <= self._gap:
                clusters[-1].append(photo)
            else:
                clusters.append([photo])

        return clusters

    def _subsample(self, photos: list[Asset], max_size: int) -> list[list[Asset]]:
        """Split a large list into chunks of at most max_size."""
        return [photos[i : i + max_size] for i in range(0, len(photos), max_size)]

    def _build_group(self, photos: list[Asset]) -> PhotoGroup:
        """Build a PhotoGroup from a list of assets."""
        asset_ids = [p.id for p in photos]
        mode = self._pick_mode(photos)
        return PhotoGroup(asset_ids=asset_ids, animation_mode=mode)

    def _pick_mode(self, photos: list[Asset]) -> AnimationMode:
        """Pick animation mode based on photo content."""
        if len(photos) >= 2 and self._collage_enabled:
            return AnimationMode.COLLAGE
        # Single photo — use AUTO (resolved later by animator)
        return AnimationMode.AUTO
