"""Video analysis caching system."""

from immich_memories.cache.database import VideoAnalysisCache
from immich_memories.cache.database_models import (
    CachedSegment,
    CachedVideoAnalysis,
    SimilarVideo,
)
from immich_memories.cache.thumbnail_cache import ThumbnailCache
from immich_memories.cache.video_cache import (
    CachedVideo,
    VideoDownloadCache,
)

__all__ = [
    # Analysis cache
    "CachedSegment",
    "CachedVideoAnalysis",
    "SimilarVideo",
    "VideoAnalysisCache",
    # Video file cache
    "CachedVideo",
    "VideoDownloadCache",
    # Thumbnail cache
    "ThumbnailCache",
]
