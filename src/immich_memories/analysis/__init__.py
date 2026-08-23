"""Video analysis modules."""

import importlib as _importlib

__all__ = [
    # Duplicates — thumbnail clustering is the one that runs
    "ThumbnailCluster",
    "compute_video_hash",
    "compute_thumbnail_hash",
    "cluster_thumbnails",
    "deduplicate_by_thumbnails",
    # Pipeline
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
    "create_analyzer_from_config",
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

_SUBMODULE_MAP = {
    "FaceDetection": "immich_memories.analysis.apple_vision",
    "VisionFaceDetector": "immich_memories.analysis.apple_vision",
    "create_face_detector": "immich_memories.analysis.apple_vision",
    "detect_faces_vision": "immich_memories.analysis.apple_vision",
    "is_vision_available": "immich_memories.analysis.apple_vision",
    "ThumbnailCluster": "immich_memories.analysis.thumbnail_clustering",
    "cluster_thumbnails": "immich_memories.analysis.thumbnail_clustering",
    "compute_thumbnail_hash": "immich_memories.analysis.thumbnail_clustering",
    "compute_video_hash": "immich_memories.analysis.duplicate_hashing",
    "deduplicate_by_thumbnails": "immich_memories.analysis.thumbnail_clustering",
    "PipelinePhase": "immich_memories.analysis.progress",
    "PipelineProgress": "immich_memories.analysis.progress",
    "ProgressTracker": "immich_memories.analysis.progress",
    "Scene": "immich_memories.analysis.scenes",
    "SceneDetector": "immich_memories.analysis.scenes",
    "detect_scenes": "immich_memories.analysis.scenes",
    "MomentScore": "immich_memories.analysis.scoring",
    "create_analyzer_from_config": "immich_memories.analysis.analyzer_factory",
    "PipelineConfig": "immich_memories.analysis.smart_pipeline",
    "PipelineResult": "immich_memories.analysis.smart_pipeline",
    "SmartPipeline": "immich_memories.analysis.smart_pipeline",
    "analyze_clip_for_highlight": "immich_memories.analysis.clip_selection",
    "CutPoint": "immich_memories.analysis.unified_analyzer",
    "ScoredSegment": "immich_memories.analysis.unified_analyzer",
    "UnifiedSegmentAnalyzer": "immich_memories.analysis.unified_analyzer",
    "create_unified_analyzer_from_config": "immich_memories.analysis.unified_analyzer",
}


def __getattr__(name: str):
    if name in _SUBMODULE_MAP:
        module = _importlib.import_module(_SUBMODULE_MAP[name])
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
