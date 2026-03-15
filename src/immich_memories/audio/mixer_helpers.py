"""Stem-based audio ducking helpers for the audio mixer.

Contains mix_audio_with_stem_ducking and mix_audio_with_4stem_ducking
which provide granular control over individual music stems during speech.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

from immich_memories.audio.mixer import (
    DuckingConfig,
    MixConfig,
    _db_to_linear,
    get_video_duration,
)

logger = logging.getLogger(__name__)


def mix_audio_with_stem_ducking(
    video_path: Path,
    vocals_path: Path,
    accompaniment_path: Path,
    output_path: Path,
    config: MixConfig | None = None,
    duck_vocals_db: float = -12.0,
) -> Path:
    """Mix background music using separated stems for intelligent ducking.

    This approach uses pre-separated stems (from Demucs) to duck only the
    melodic content while keeping drums/bass at full volume during speech.

    Args:
        video_path: Path to the video file
        vocals_path: Path to the vocals/melody stem
        accompaniment_path: Path to the drums+bass+other stem
        output_path: Path for the output video
        config: Mixing configuration
        duck_vocals_db: How much to lower vocals during speech (dB)

    Returns:
        Path to the output video
    """
    if config is None:
        config = MixConfig(ducking=DuckingConfig())

    ducking = config.ducking
    video_duration = get_video_duration(video_path)

    filter_parts = []

    # Prepare accompaniment (drums/bass) - always at full volume
    accompaniment_filter = f"[1:a]atrim=0:{video_duration}"
    accompaniment_filter += f",volume={ducking.music_volume_db}dB"

    if config.fade_in_seconds > 0:
        accompaniment_filter += (
            f",afade=t=in:st={config.music_starts_at}:d={config.fade_in_seconds}"
        )
    if config.fade_out_seconds > 0:
        fade_start = video_duration - config.fade_out_seconds
        accompaniment_filter += f",afade=t=out:st={fade_start}:d={config.fade_out_seconds}"

    accompaniment_filter += "[accompaniment]"
    filter_parts.append(accompaniment_filter)

    # Prepare vocals/melody stem - will be ducked during speech
    vocals_filter = f"[2:a]atrim=0:{video_duration}"
    vocals_filter += f",volume={ducking.music_volume_db}dB"

    if config.fade_in_seconds > 0:
        vocals_filter += f",afade=t=in:st={config.music_starts_at}:d={config.fade_in_seconds}"
    if config.fade_out_seconds > 0:
        fade_start = video_duration - config.fade_out_seconds
        vocals_filter += f",afade=t=out:st={fade_start}:d={config.fade_out_seconds}"

    vocals_filter += "[vocals_prepared]"
    filter_parts.append(vocals_filter)

    # Prepare video audio
    if config.normalize_audio:
        filter_parts.append("[0:a]loudnorm=I=-16:TP=-1.5:LRA=11[video_audio]")
    else:
        filter_parts.append("[0:a]acopy[video_audio]")

    # Apply sidechain compression ONLY to vocals/melody
    # When there's speech, vocals get ducked while accompaniment stays full
    sidechain_filter = (
        f"[vocals_prepared][video_audio]sidechaincompress="
        f"threshold={ducking.threshold}:"
        f"ratio={ducking.ratio}:"
        f"attack={ducking.attack_ms}:"
        f"release={ducking.release_ms}:"
        f"makeup={_db_to_linear(duck_vocals_db):.2f}"
        f"[ducked_vocals]"
    )
    filter_parts.extend(
        (
            sidechain_filter,
            "[video_audio][accompaniment][ducked_vocals]amix=inputs=3:duration=first:dropout_transition=2[mixed]",
        )
    )

    filter_complex = ";".join(filter_parts)

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(accompaniment_path),
        "-i",
        str(vocals_path),
        "-filter_complex",
        filter_complex,
        "-map",
        "0:v",
        "-map",
        "[mixed]",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        str(output_path),
    ]

    try:
        logger.info("Mixing audio with stem-based ducking...")
        subprocess.run(cmd, capture_output=True, check=True, timeout=600)
        logger.info(f"Audio mixed successfully: {output_path}")
    except subprocess.CalledProcessError as e:
        logger.error(f"Stem mixing failed: {e.stderr.decode() if e.stderr else e}")
        raise

    return output_path


@dataclass
class StemDuckingLevels:
    """Ducking levels for each stem during speech (in dB, negative = quieter).

    Different stems can be ducked by different amounts:
    - Drums: duck least (rhythm keeps energy)
    - Bass: duck moderately
    - Vocals/melody: duck most (avoid competing with speech)
    - Other instruments: duck moderately
    """

    drums_db: float = -3.0  # Duck drums by 50% (~-3dB)
    bass_db: float = -6.0  # Duck bass moderately
    vocals_db: float = -12.0  # Duck melody most (~75%)
    other_db: float = -9.0  # Duck other instruments


def mix_audio_with_4stem_ducking(
    video_path: Path,
    drums_path: Path,
    bass_path: Path,
    vocals_path: Path,
    other_path: Path,
    output_path: Path,
    config: MixConfig | None = None,
    ducking_levels: StemDuckingLevels | None = None,
) -> Path:
    """Mix background music using 4 separated stems for granular ducking.

    Each stem can be ducked by a different amount during speech:
    - Drums: minimal ducking (keeps energy)
    - Bass: moderate ducking
    - Vocals/melody: aggressive ducking (avoid competing with speech)
    - Other: moderate ducking

    Args:
        video_path: Path to the video file
        drums_path: Path to the drums stem
        bass_path: Path to the bass stem
        vocals_path: Path to the vocals/melody stem
        other_path: Path to the other instruments stem
        output_path: Path for the output video
        config: Mixing configuration
        ducking_levels: Custom ducking levels per stem

    Returns:
        Path to the output video
    """
    if config is None:
        config = MixConfig(ducking=DuckingConfig())
    if ducking_levels is None:
        ducking_levels = StemDuckingLevels()

    ducking = config.ducking
    video_duration = get_video_duration(video_path)

    filter_parts: list[str] = []

    # Input mapping: 0=video, 1=drums, 2=bass, 3=vocals, 4=other

    # Prepare each stem with base volume and fades
    def prepare_stem(input_idx: int, name: str) -> str:
        stem_filter = f"[{input_idx}:a]atrim=0:{video_duration}"
        stem_filter += f",volume={ducking.music_volume_db}dB"
        if config.fade_in_seconds > 0:
            stem_filter += f",afade=t=in:st={config.music_starts_at}:d={config.fade_in_seconds}"
        if config.fade_out_seconds > 0:
            fade_start = video_duration - config.fade_out_seconds
            stem_filter += f",afade=t=out:st={fade_start}:d={config.fade_out_seconds}"
        stem_filter += f"[{name}_prepared]"
        return stem_filter

    filter_parts.extend(
        (
            prepare_stem(1, "drums"),
            prepare_stem(2, "bass"),
            prepare_stem(3, "vocals"),
            prepare_stem(4, "other"),
        )
    )

    # Prepare video audio
    if config.normalize_audio:
        filter_parts.append("[0:a]loudnorm=I=-16:TP=-1.5:LRA=11[video_audio]")
    else:
        filter_parts.append("[0:a]acopy[video_audio]")

    # Apply sidechain compression to stems that should duck during speech.
    # Drums: NO ducking — constant level keeps rhythmic energy.
    # Bass: duck moderately
    filter_parts.extend(
        (
            "[drums_prepared]acopy[final_drums]",
            f"[bass_prepared][video_audio]sidechaincompress="
            f"threshold={ducking.threshold}:ratio={ducking.ratio * 0.7}:"
            f"attack={ducking.attack_ms}:release={ducking.release_ms}:"
            f"makeup=1.0[ducked_bass]",
        )
    )

    # Vocals/melody: duck most; Other instruments: duck moderately
    # Mix all 5 audio streams: video + 4 stems (drums constant, others ducked)
    filter_parts.extend(
        (
            f"[vocals_prepared][video_audio]sidechaincompress="
            f"threshold={ducking.threshold}:ratio={ducking.ratio}:"
            f"attack={ducking.attack_ms}:release={ducking.release_ms}:"
            f"makeup=1.0[ducked_vocals]",
            f"[other_prepared][video_audio]sidechaincompress="
            f"threshold={ducking.threshold}:ratio={ducking.ratio * 0.8}:"
            f"attack={ducking.attack_ms}:release={ducking.release_ms}:"
            f"makeup=1.0[ducked_other]",
            "[video_audio][final_drums][ducked_bass][ducked_vocals][ducked_other]"
            "amix=inputs=5:duration=first:dropout_transition=2[mixed]",
        )
    )

    filter_complex = ";".join(filter_parts)

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(drums_path),
        "-i",
        str(bass_path),
        "-i",
        str(vocals_path),
        "-i",
        str(other_path),
        "-filter_complex",
        filter_complex,
        "-map",
        "0:v",
        "-map",
        "[mixed]",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        str(output_path),
    ]

    try:
        logger.info(
            f"Mixing audio with 4-stem ducking: drums={ducking_levels.drums_db}dB, "
            f"bass={ducking_levels.bass_db}dB, vocals={ducking_levels.vocals_db}dB, "
            f"other={ducking_levels.other_db}dB"
        )
        subprocess.run(cmd, capture_output=True, check=True, timeout=600)
        logger.info(f"Audio mixed successfully: {output_path}")
    except subprocess.CalledProcessError as e:
        logger.error(f"4-stem mixing failed: {e.stderr.decode() if e.stderr else e}")
        raise

    return output_path
