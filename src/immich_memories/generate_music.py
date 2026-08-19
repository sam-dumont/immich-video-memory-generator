"""Music generation and mixing helpers for video generation.

Extracted from generate.py — handles AI music generation (MusicGen/ACE-Step),
music resolution, and audio mixing into assembled videos.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

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

# WHY: audio proof decodes the whole supported memory, matching final video validation.
_AUDIO_DECODE_TIMEOUT_SECONDS = 15 * 60


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
    bundled_library: Path | None = None,
) -> Path | None:
    """Determine the music file to use: provided path, generated, bundled, or None."""
    if no_music:
        return None
    if music_path and music_path.exists():
        return music_path
    if not music_path and music_config_available(config):
        if report_fn:
            report_fn("music", 0.85, "Generating AI music...")
        return auto_generate_music(config, assembly_clips, run_output_dir, memory_type, report_fn)

    if music_path is not None:
        # An explicit track that is missing is a user error; substituting bundled
        # music would hide the typo.
        return None

    # WHY: with no generator configured this used to return silence, which is what
    # the Docker/NAS path gets by default.
    from immich_memories.audio.bundled_music import bundled_track_for_mood

    return bundled_track_for_mood(_mood_for_clips(assembly_clips), library=bundled_library)


def _mood_for_clips(assembly_clips: list[AssemblyClip]) -> str | None:
    """Mood to pick bundled music by, taken from the clips when they carry one."""
    for clip in assembly_clips:
        mood = getattr(clip, "mood", None)
        if mood:
            return str(mood)
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
    staged_path = music_staging_path(video_path, encoding_plan)
    try:
        _require_audio_stream(staged_path)
        return publish_validated_output(staged_path, video_path, encoding_plan)
    except Exception:
        try:
            staged_path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Music stage cleanup failed; preserving the primary phase outcome")
        raise


@contextmanager
def staged_music_output(video_path: Path, encoding_plan: EncodingPlan) -> Iterator[Path]:
    """Yield a clean container-preserving sibling and remove every leftover."""
    staged_path = music_staging_path(video_path, encoding_plan)
    staged_path.unlink(missing_ok=True)
    try:
        yield staged_path
    finally:
        try:
            staged_path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Music stage cleanup failed; preserving the primary phase outcome")


def _require_audio_stream(path: Path) -> None:
    """Fail closed unless one full audio decode reports positive frame evidence."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-count_frames",
                "-show_entries",
                "stream=codec_type,nb_read_frames",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=_AUDIO_DECODE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise InvalidOutputArtifact("music mix audio validation failed") from exc
    if result.returncode != 0 or result.stderr.strip():
        raise InvalidOutputArtifact("music mix audio decode failed")
    try:
        streams = json.loads(result.stdout).get("streams", [])
        if not streams or streams[0].get("codec_type") != "audio":
            raise InvalidOutputArtifact("music mix is missing audio stream")
        raw_frame_count = streams[0]["nb_read_frames"]
        if not isinstance(raw_frame_count, str) or not raw_frame_count.isdecimal():
            raise InvalidOutputArtifact("music mix has malformed decoded audio frame evidence")
        decoded_frames = int(raw_frame_count)
    except InvalidOutputArtifact:
        raise
    except (AttributeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise InvalidOutputArtifact("music mix is missing decoded audio frame evidence") from exc
    if decoded_frames <= 0:
        raise InvalidOutputArtifact("music mix must have positive decoded audio frames")


def music_staging_path(video_path: Path, encoding_plan: EncodingPlan) -> Path:
    """Return the plan-compatible staged music path or reject a stale base suffix."""
    expected_suffix = f".{encoding_plan.container}"
    if video_path.suffix.lower() != expected_suffix:
        raise ValueError(
            f"Music input suffix {video_path.suffix!r} does not match "
            f"encoding plan container {encoding_plan.container!r}"
        )
    return video_path.with_suffix(f".with_music.{encoding_plan.container}")


def apply_music_file(
    video_path: Path,
    music_path: Path,
    volume: float,
    encoding_plan: EncodingPlan,
) -> OutputProbe:
    """Mix a music file and publish it only when it matches the encoding plan."""
    from immich_memories.audio.mixer import DuckingConfig, MixConfig, mix_audio_with_ducking

    mix_config = MixConfig(
        ducking=DuckingConfig(
            music_volume_db=-20 + (volume * 20),
        ),
    )
    with staged_music_output(video_path, encoding_plan) as staged_path:
        mix_audio_with_ducking(
            video_path=video_path,
            music_path=music_path,
            output_path=staged_path,
            config=mix_config,
        )
        return publish_music_mix(video_path, encoding_plan)
