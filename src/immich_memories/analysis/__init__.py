"""Video analysis modules."""

from immich_memories.analysis.apple_vision import (
    FaceDetection,
    VisionFaceDetector,
    create_face_detector,
    detect_faces_vision,
    is_vision_available,
)
from immich_memories.analysis.duplicates import (
    DuplicateGroup,
    ThumbnailCluster,
    cluster_thumbnails,
    compute_thumbnail_hash,
    compute_video_hash,
    deduplicate_by_thumbnails,
    find_duplicate_groups,
)
from immich_memories.analysis.pipeline import (
    ClusterManager,
    DuplicateCluster,
    VideoAnalyzer,
)
from immich_memories.analysis.progress import (
    PipelinePhase,
    PipelineProgress,
    ProgressTracker,
)
from immich_memories.analysis.scenes import (
    Scene,
    SceneDetector,
    detect_scenes,
)
from immich_memories.analysis.scoring import (
    MomentScore,
    SceneScorer,
    sample_video,
    score_scene,
)
from immich_memories.analysis.smart_pipeline import (
    PipelineConfig,
    PipelineResult,
    SmartPipeline,
    analyze_clip_for_highlight,
)
from immich_memories.analysis.unified_analyzer import (
    CutPoint,
    ScoredSegment,
    UnifiedSegmentAnalyzer,
    create_unified_analyzer_from_config,
)

__all__ = [
    # Duplicates
    "DuplicateGroup",
    "ThumbnailCluster",
    "find_duplicate_groups",
    "compute_video_hash",
    "compute_thumbnail_hash",
    "cluster_thumbnails",
    "deduplicate_by_thumbnails",
    # Pipeline
    "VideoAnalyzer",
    "ClusterManager",
    "DuplicateCluster",
    # Smart Pipeline
    "SmartPipeline",
    "PipelineConfig",
    "PipelineResult",
    "analyze_clip_for_highlight",
    # Progress
    "ProgressTracker",
    "PipelineProgress",
    "PipelinePhase",
    # Scenes
    "Scene",
    "SceneDetector",
    "detect_scenes",
    # Scoring
    "MomentScore",
    "SceneScorer",
    "score_scene",
    "sample_video",
    # Apple Vision
    "is_vision_available",
    "detect_faces_vision",
    "create_face_detector",
    "FaceDetection",
    "VisionFaceDetector",
    # Unified Analyzer
    "CutPoint",
    "ScoredSegment",
    "UnifiedSegmentAnalyzer",
    "create_unified_analyzer_from_config",
]
