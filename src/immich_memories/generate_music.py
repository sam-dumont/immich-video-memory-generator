"""Music generation and mixing helpers for video generation.

Extracted from generate.py — handles AI music generation (MusicGen/ACE-Step),
music resolution, and audio mixing into assembled videos.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from immich_memories.filename_builder import build_music_output_path
from immich_memories.processing.assembly_config import AssemblyClip
from immich_memories.processing.encoding_plan import EncodingPlan, HdrTransfer, OutputCodec
from immich_memories.processing.output_contract import (
    InvalidOutputArtifact,
    OutputProbe,
    probe_output,
    publish_validated_output,
)
from immich_memories.security import configured_secret_values, sanitize_error_message

if TYPE_CHECKING:
    from immich_memories.config_loader import Config

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MusicPhaseResult:
    """Outcome of optional music work without changing artifact validity."""

    applied: bool
    warning: str | None = None


def optional_music_warning(exc: Exception, config: Config | None = None) -> str:
    """Build one user-safe optional-music warning with configured secrets removed."""
    safe_message = sanitize_error_message(str(exc))
    if config is not None:
        for secret in configured_secret_values(config):
            safe_message = safe_message.replace(secret, "***")
    return f"Optional music failed: {safe_message}"


def derive_music_validation_plan(video_path: Path) -> EncodingPlan:
    """Derive a validation-only contract from a freshly probed published base.

    This contract describes artifact identity; it is never used to choose an
    encoder or to reinterpret UI/config output preferences.
    """
    probe = probe_output(video_path)
    codecs = {
        "h264": (OutputCodec.H264, "libx264"),
        "hevc": (OutputCodec.H265, "libx265"),
        "prores": (OutputCodec.PRORES, "prores_ks"),
    }
    transfers = {
        "bt709": HdrTransfer.NONE,
        "arib-std-b67": HdrTransfer.HLG,
        "smpte2084": HdrTransfer.PQ,
    }
    supported_pixel_formats = {"yuv420p", "yuv420p10le", "yuv422p10le"}
    try:
        codec, encoder = codecs[probe.codec]
    except KeyError as exc:
        raise InvalidOutputArtifact(
            f"unsupported published codec for music validation: {probe.codec}"
        ) from exc
    if probe.container not in {"mp4", "mov"}:
        raise InvalidOutputArtifact(
            f"unsupported published container for music validation: {probe.container}"
        )
    if probe.pixel_format not in supported_pixel_formats:
        raise InvalidOutputArtifact(
            f"unsupported published pixel format for music validation: {probe.pixel_format}"
        )
    try:
        target_transfer = transfers[probe.color_transfer or ""]
    except KeyError as exc:
        raise InvalidOutputArtifact(
            "unsupported published color transfer for music validation: "
            f"{probe.color_transfer or 'missing'}"
        ) from exc
    expected_primaries = "bt2020" if target_transfer is not HdrTransfer.NONE else "bt709"
    if probe.color_primaries != expected_primaries:
        raise InvalidOutputArtifact(
            "published color primaries conflict with transfer for music validation: "
            f"{probe.color_primaries or 'missing'}"
        )
    return EncodingPlan(
        codec=codec,
        encoder=encoder,
        encoder_args=(),
        target_transfer=target_transfer,
        tone_map_to_sdr=False,
        pixel_format=probe.pixel_format,
        container=probe.container,
    )


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

    Returns the path to the generated music file, or None when no backend
    is available. Backend failures propagate to the optional phase boundary.
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

    except (RuntimeError, OSError):
        raise

    return None


def _clip_month_from_date(date_str: str | None) -> int | None:
    """Extract month from a YYYY-MM-DD date string."""
    if not date_str:
        return None
    try:
        return int(date_str.split("-")[1])
    except (IndexError, ValueError):
        return None


def publish_music_mix(
    video_path: Path,
    encoding_plan: EncodingPlan,
) -> OutputProbe:
    """Validate the staged music sibling before atomically replacing the base."""
    staged_path = build_music_output_path(video_path)
    try:
        _require_audio_stream(staged_path)
        return publish_validated_output(staged_path, video_path, encoding_plan)
    except Exception:
        staged_path.unlink(missing_ok=True)
        raise


def _require_audio_stream(path: Path) -> None:
    """Fail closed when a purported music mix contains no decodable audio stream."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=codec_type",
                "-of",
                "default=nokey=1:noprint_wrappers=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise InvalidOutputArtifact("music mix audio validation failed") from exc
    if result.returncode != 0 or result.stdout.strip() != "audio":
        raise InvalidOutputArtifact("music mix is missing audio stream")


def apply_music_file(
    video_path: Path,
    music_path: Path,
    volume: float,
    encoding_plan: EncodingPlan,
) -> OutputProbe:
    """Mix a music file and publish it only when it matches the encoding plan."""
    from immich_memories.audio.mixer import DuckingConfig, MixConfig, mix_audio_with_ducking

    final_path = build_music_output_path(video_path)
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
    return publish_music_mix(video_path, encoding_plan)
