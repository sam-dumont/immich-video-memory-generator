"""Video analysis caching system."""

from immich_memories.cache.database import VideoAnalysisCache
from immich_memories.cache.database_models import (
    CachedSegment,
    CachedVideoAnalysis,
    SimilarVideo,
)
from immich_memories.cache.thumbnail_cache import ThumbnailCache
from immich_memories.cache.video_cache import (
    CacheBatch,
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
    "CacheBatch",
    "CachedVideo",
    "VideoDownloadCache",
    # Thumbnail cache
    "ThumbnailCache",
]
