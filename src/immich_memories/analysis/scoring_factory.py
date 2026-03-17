"""Factory functions and convenience wrappers for video scoring.

Provides `create_scorer_from_config()` and standalone scoring functions
that create a SceneScorer with appropriate settings.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from immich_memories.analysis.scenes import Scene
    from immich_memories.analysis.scoring import MomentScore, SceneScorer
    from immich_memories.config_loader import Config

logger = logging.getLogger(__name__)


def score_scene(
    video_path: str | Path,
    scene: Scene,
    sample_frames: int = 10,
) -> MomentScore:
    """Convenience function to score a single scene.

    Args:
        video_path: Path to the video file.
        scene: Scene to score.
        sample_frames: Number of frames to sample.

    Returns:
        MomentScore with component scores.
    """
    from immich_memories.analysis.scoring import SceneScorer

    return SceneScorer().score_scene(video_path, scene, sample_frames)


def select_top_moments(
    video_path: str | Path,
    scenes: list[Scene],
    target_count: int = 5,
    target_duration: float = 5.0,
) -> list[MomentScore]:
    """Select the top N moments from a video.

    Args:
        video_path: Path to the video file.
        scenes: List of detected scenes.
        target_count: Number of moments to select.
        target_duration: Target duration per moment.

    Returns:
        List of top scored moments.
    """
    from immich_memories.analysis.scoring import SceneScorer

    moments = SceneScorer().find_best_moments(video_path, scenes, target_duration)
    return moments[:target_count]


def create_scorer_from_config(config: Config | None = None) -> SceneScorer:
    """Create a SceneScorer with content analysis configured from config.

    This factory function creates a SceneScorer that respects the
    content_analysis config settings, initializing the content analyzer
    if enabled.

    Args:
        config: App config. Falls back to get_config().

    Returns:
        SceneScorer instance configured from current config.
    """
    from immich_memories.analysis.scoring import SceneScorer

    if config is None:
        from immich_memories.config import get_config

        config = get_config()

    # Base weights (sum to 1.0 without content analysis)
    # Duration weight gives preference to ~5 second clips
    base_face = 0.35
    base_motion = 0.20
    base_stability = 0.15
    base_audio = 0.15
    base_duration = 0.15

    # Get duration scoring settings from config
    optimal_duration = config.analysis.optimal_clip_duration
    max_optimal_duration = config.analysis.max_optimal_duration
    target_extraction_ratio = config.analysis.target_extraction_ratio
    min_duration = config.analysis.min_segment_duration

    logger.info(
        f"Duration scoring config: base={optimal_duration:.1f}s, "
        f"max={max_optimal_duration:.1f}s, ratio={target_extraction_ratio * 100:.0f}%, "
        f"min={min_duration:.1f}s"
    )

    # Initialize content analyzer if enabled
    content_analyzer = None
    content_weight = 0.0

    if config.content_analysis.enabled:
        from immich_memories.analysis.content_analyzer import get_content_analyzer

        # Use shared LLM config
        content_analyzer = get_content_analyzer(
            provider=config.llm.provider,
            base_url=config.llm.base_url,
            model=config.llm.model,
            api_key=config.llm.api_key,
        )

        if content_analyzer:
            content_weight = config.content_analysis.weight
            logger.info(f"Content analysis enabled with weight {content_weight}")
        else:
            logger.warning("Content analysis enabled but no analyzer available")

    # Adjust other weights to account for content weight
    # When content analysis is enabled, reduce other weights proportionally
    if content_weight > 0:
        scale = 1 - content_weight
        return SceneScorer(
            face_weight=base_face * scale,
            motion_weight=base_motion * scale,
            stability_weight=base_stability * scale,
            audio_weight=base_audio * scale,
            duration_weight=base_duration * scale,
            content_weight=content_weight,
            content_analyzer=content_analyzer,
            optimal_duration=optimal_duration,
            max_optimal_duration=max_optimal_duration,
            target_extraction_ratio=target_extraction_ratio,
            min_duration=min_duration,
        )

    return SceneScorer(
        face_weight=base_face,
        motion_weight=base_motion,
        stability_weight=base_stability,
        audio_weight=base_audio,
        duration_weight=base_duration,
        optimal_duration=optimal_duration,
        max_optimal_duration=max_optimal_duration,
        target_extraction_ratio=target_extraction_ratio,
        min_duration=min_duration,
    )


def sample_video(
    video_path: str | Path,
    segment_duration: float = 3.0,
    overlap: float = 0.5,
    sample_frames: int = 5,
    use_scene_detection: bool | None = None,
) -> list[MomentScore]:
    """Convenience function to sample and score a video.

    When scene detection is enabled (default), segments are created from natural
    scene boundaries. When disabled, uses fixed-duration sliding window segments.

    If content analysis is enabled in config, the scorer will use LLM-based
    content analysis to improve scoring.

    Args:
        video_path: Path to the video file.
        segment_duration: Duration of each segment in seconds (for fixed segmentation).
        overlap: Overlap fraction between segments (0-1).
        sample_frames: Number of frames to sample per segment.
        use_scene_detection: Override config default. None = use config.

    Returns:
        List of MomentScore objects sorted by score (best first).
    """
    return create_scorer_from_config().sample_and_score_video(
        video_path,
        segment_duration=segment_duration,
        overlap=overlap,
        sample_frames=sample_frames,
        use_scene_detection=use_scene_detection,
    )
