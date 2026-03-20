"""Music generation and mixing helpers for video generation.

Extracted from generate.py — handles AI music generation (MusicGen/ACE-Step),
music resolution, and audio mixing into assembled videos.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from immich_memories.processing.assembly_config import AssemblyClip

if TYPE_CHECKING:
    from immich_memories.config_loader import Config

logger = logging.getLogger(__name__)


def music_config_available(config: Config) -> bool:
    """Check if any AI music generation backend is configured and enabled."""
    ace = getattr(config, "ace_step", None)
    mg = getattr(config, "musicgen", None)
    return bool((ace and ace.enabled) or (mg and mg.enabled))


def resolve_music_file(
    config: Config,
    music_path: Path | None,
    no_music: bool,
    assembly_clips: list[AssemblyClip],
    run_output_dir: Path,
    memory_type: str | None,
    report_fn: Callable[[str, float, str], None] | None = None,
) -> Path | None:
    """Determine the music file to use: provided path, auto-generated, or None."""
    if no_music:
        return None
    if music_path and music_path.exists():
        return music_path
    if not music_path and music_config_available(config):
        if report_fn:
            report_fn("music", 0.85, "Generating AI music...")
        return auto_generate_music(config, assembly_clips, run_output_dir, memory_type, report_fn)
    return None


def auto_generate_music(
    config: Config,
    assembly_clips: list[AssemblyClip],
    run_output_dir: Path,
    memory_type: str | None,
    report_fn: Callable[[str, float, str], None] | None = None,
) -> Path | None:
    """Auto-generate music using configured AI backends.

    Returns the path to the generated music file, or None if generation
    fails or no backend is available.
    """
    if not music_config_available(config):
        return None

    try:
        from immich_memories.audio.music_generator import generate_music_for_video
        from immich_memories.audio.music_generator_client import MusicGenClientConfig
        from immich_memories.audio.music_generator_models import VideoTimeline

        clip_data: list[tuple[float, str, int | None]] = [
            (
                clip.duration,
                clip.llm_emotion or "calm",
                _clip_month_from_date(clip.date),
            )
            for clip in assembly_clips
        ]

        timeline = VideoTimeline.from_clips(
            clips=clip_data,
            title_duration=(
                config.title_screens.title_duration if config.title_screens.enabled else 0
            ),
            ending_duration=(
                config.title_screens.ending_duration if config.title_screens.enabled else 0
            ),
        )

        musicgen_config = MusicGenClientConfig.from_app_config(config.musicgen)
        musicgen_config.num_versions = 1  # CLI: just generate one, accept it

        music_dir = run_output_dir / "music"
        music_dir.mkdir(parents=True, exist_ok=True)

        def music_progress(version_idx: int, status: str, progress: float, detail: object) -> None:
            if report_fn:
                report_fn("music", 0.85 + (progress / 100.0) * 0.05, f"Music: {status}")

        result = asyncio.run(
            generate_music_for_video(
                timeline=timeline,
                output_dir=music_dir,
                config=musicgen_config,
                progress_callback=music_progress,
                app_config=config,
                memory_type=memory_type,
            )
        )

        if result and result.versions:
            result.selected_version = 0
            selected = result.selected
            if selected and selected.full_mix and selected.full_mix.exists():
                logger.info(f"Auto-generated music: {selected.full_mix}")
                return selected.full_mix

    except Exception:
        logger.warning("Auto music generation failed, continuing without music", exc_info=True)

    return None


def _clip_month_from_date(date_str: str | None) -> int | None:
    """Extract month from a YYYY-MM-DD date string."""
    if not date_str:
        return None
    try:
        return int(date_str.split("-")[1])
    except (IndexError, ValueError):
        return None


def apply_music_file(video_path: Path, music_path: Path, volume: float) -> None:
    """Mix a music file into the assembled video."""
    from immich_memories.audio.mixer import DuckingConfig, MixConfig, mix_audio_with_ducking

    final_path = video_path.with_suffix(".with_music.mp4")
    mix_config = MixConfig(
        ducking=DuckingConfig(
            music_volume_db=-20 + (volume * 20),
        ),
    )
    mix_audio_with_ducking(
        video_path=video_path,
        music_path=music_path,
        output_path=final_path,
        config=mix_config,
    )
    # WHY: replace() is atomic on POSIX — no window where video_path is missing
    final_path.replace(video_path)
