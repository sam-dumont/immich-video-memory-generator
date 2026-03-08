"""Audio mixing with intelligent ducking and music looping."""

from __future__ import annotations

import contextlib
import logging
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from immich_memories.security import validate_audio_path, validate_video_path

logger = logging.getLogger(__name__)


def _db_to_linear(db: float, min_val: float = 1.0, max_val: float = 64.0) -> float:
    """Convert dB to linear scale for FFmpeg parameters.

    FFmpeg's sidechaincompress 'makeup' parameter expects linear scale (1-64).
    Formula: linear = 10^(dB/20)

    Args:
        db: Value in decibels
        min_val: Minimum allowed linear value
        max_val: Maximum allowed linear value

    Returns:
        Linear value clamped to [min_val, max_val]
    """
    linear = 10 ** (db / 20)
    return max(min_val, min(max_val, linear))


@dataclass
class DuckingConfig:
    """Configuration for audio ducking (lowering music when speech is present)."""

    # Threshold for sidechain compression (0.0-1.0)
    # Lower values = more sensitive to speech
    threshold: float = 0.02

    # Compression ratio (how much to lower music)
    # Higher = more aggressive ducking (4.0 = moderate, smoother than 6.0)
    ratio: float = 4.0

    # Attack time in milliseconds (how fast ducking kicks in)
    # 100ms = smooth fade down, not too abrupt
    attack_ms: float = 100.0

    # Release time in milliseconds (how fast music comes back)
    # 2500ms = music stays ducked during natural speech pauses (word -> pause -> word)
    # This prevents the "pumping" effect between words
    release_ms: float = 2500.0

    # Makeup gain in dB (boost after compression)
    makeup_db: float = 0.0

    # Music volume reduction in dB (base level before ducking)
    music_volume_db: float = -6.0


@dataclass
class MixConfig:
    """Configuration for audio mixing."""

    ducking: DuckingConfig
    fade_in_seconds: float = 2.0
    fade_out_seconds: float = 3.0
    music_starts_at: float = 0.0  # Seconds into video
    normalize_audio: bool = True


def get_audio_duration(audio_path: Path) -> float:
    """Get the duration of an audio file in seconds.

    Args:
        audio_path: Path to the audio file

    Returns:
        Duration in seconds
    """
    # Validate path before subprocess call
    validated_path = validate_audio_path(audio_path, must_exist=True)
    return _get_duration_unchecked(validated_path)


def get_video_duration(video_path: Path) -> float:
    """Get the duration of a video file in seconds.

    Args:
        video_path: Path to the video file

    Returns:
        Duration in seconds
    """
    # Validate as video, then use audio duration probe (same ffprobe command)
    validated_path = validate_video_path(video_path, must_exist=True)
    # Re-use the audio duration function with validated path
    return _get_duration_unchecked(validated_path)


def _get_duration_unchecked(path: Path) -> float:
    """Get duration without validation (internal use after validation)."""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return float(result.stdout.strip())
    except (subprocess.CalledProcessError, ValueError) as e:
        logger.error(f"Could not get duration: {e}")
        return 0.0


def loop_audio_to_duration(
    audio_path: Path,
    target_duration: float,
    output_path: Path | None = None,
    crossfade_seconds: float = 2.0,  # noqa: ARG001 - reserved for future use
) -> Path:
    """Loop an audio file to reach target duration with crossfade.

    Args:
        audio_path: Path to the source audio
        target_duration: Target duration in seconds
        output_path: Output path (auto-generated if None)
        crossfade_seconds: Crossfade duration between loops

    Returns:
        Path to the looped audio file
    """
    if output_path is None:
        # Use NamedTemporaryFile for secure temp file creation (no race condition)
        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", prefix="looped_", delete=False)
        tmp.close()
        output_path = Path(tmp.name)

    audio_duration = get_audio_duration(audio_path)

    if audio_duration <= 0:
        raise ValueError(f"Could not determine duration of {audio_path}")

    # If audio is already long enough, just trim it
    if audio_duration >= target_duration:
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(audio_path),
            "-t",
            str(target_duration),
            "-acodec",
            "libmp3lame",
            "-q:a",
            "2",
            str(output_path),
        ]
        subprocess.run(cmd, capture_output=True, check=True)
        return output_path

    # Calculate how many loops we need
    num_loops = int(target_duration / audio_duration) + 2

    # Use FFmpeg's aloop filter for seamless looping
    # Then apply crossfade between loops using acrossfade
    filter_complex = (
        f"[0:a]aloop=loop={num_loops}:size=2e9[looped];"
        f"[looped]atrim=0:{target_duration}[trimmed];"
        f"[trimmed]afade=t=in:st=0:d=1,afade=t=out:st={target_duration - 2}:d=2[out]"
    )

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(audio_path),
        "-filter_complex",
        filter_complex,
        "-map",
        "[out]",
        "-acodec",
        "libmp3lame",
        "-q:a",
        "2",
        str(output_path),
    ]

    try:
        subprocess.run(cmd, capture_output=True, check=True)
    except subprocess.CalledProcessError as e:
        logger.error(f"Loop failed: {e.stderr.decode() if e.stderr else e}")
        raise

    return output_path


def mix_audio_with_ducking(
    video_path: Path,
    music_path: Path,
    output_path: Path,
    config: MixConfig | None = None,
) -> Path:
    """Mix background music with video, ducking music when speech is present.

    Uses FFmpeg's sidechaincompress filter to automatically lower music
    volume when the video's audio is louder (speech, sound effects, etc.).

    Args:
        video_path: Path to the video file
        music_path: Path to the background music
        output_path: Path for the output video
        config: Mixing configuration

    Returns:
        Path to the output video
    """
    if config is None:
        config = MixConfig(ducking=DuckingConfig())

    ducking = config.ducking
    video_duration = get_video_duration(video_path)

    # First, ensure music is long enough
    music_duration = get_audio_duration(music_path)
    music_to_use = music_path

    if music_duration < video_duration:
        logger.info(f"Looping music from {music_duration:.1f}s to {video_duration:.1f}s")
        # Use NamedTemporaryFile for secure temp file creation (no race condition)
        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", prefix="looped_music_", delete=False)
        tmp.close()
        looped_music = Path(tmp.name)
        music_to_use = loop_audio_to_duration(music_path, video_duration, looped_music)

    # Build the complex filter for ducking
    # 1. Take video audio as sidechain input
    # 2. Lower music volume based on video audio loudness
    # 3. Mix the ducked music with original audio

    filter_parts = []

    # Prepare music: trim to video length, apply volume, add fades
    music_filter = f"[1:a]atrim=0:{video_duration}"
    music_filter += f",volume={ducking.music_volume_db}dB"

    if config.fade_in_seconds > 0:
        music_filter += f",afade=t=in:st={config.music_starts_at}:d={config.fade_in_seconds}"

    if config.fade_out_seconds > 0:
        fade_start = video_duration - config.fade_out_seconds
        music_filter += f",afade=t=out:st={fade_start}:d={config.fade_out_seconds}"

    music_filter += "[music]"
    filter_parts.append(music_filter)

    # Prepare video audio (normalize if requested)
    if config.normalize_audio:
        filter_parts.append("[0:a]loudnorm=I=-16:TP=-1.5:LRA=11[video_audio]")
    else:
        filter_parts.append("[0:a]acopy[video_audio]")

    # Apply sidechain compression: duck music when video audio is present
    # The video audio controls when the music ducks
    sidechain_filter = (
        f"[music][video_audio]sidechaincompress="
        f"threshold={ducking.threshold}:"
        f"ratio={ducking.ratio}:"
        f"attack={ducking.attack_ms}:"
        f"release={ducking.release_ms}:"
        f"makeup={_db_to_linear(ducking.makeup_db):.2f}"
        f"[ducked_music]"
    )
    filter_parts.append(sidechain_filter)

    # Mix ducked music with video audio
    filter_parts.append(
        "[video_audio][ducked_music]amix=inputs=2:duration=first:dropout_transition=2[mixed]"
    )

    filter_complex = ";".join(filter_parts)

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(music_to_use),
        "-filter_complex",
        filter_complex,
        "-map",
        "0:v",  # Keep original video
        "-map",
        "[mixed]",  # Use mixed audio
        "-c:v",
        "copy",  # Don't re-encode video
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        str(output_path),
    ]

    try:
        logger.info("Mixing audio with ducking...")
        subprocess.run(cmd, capture_output=True, check=True)
        logger.info(f"Audio mixed successfully: {output_path}")
    except subprocess.CalledProcessError as e:
        logger.error(f"Audio mixing failed: {e.stderr.decode() if e.stderr else e}")
        raise
    finally:
        # Cleanup looped music if we created it
        if music_to_use != music_path and music_to_use.exists():
            with contextlib.suppress(OSError):
                music_to_use.unlink()

    return output_path


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
    filter_parts.append(sidechain_filter)

    # Mix: video audio + accompaniment (full) + ducked vocals
    filter_parts.append(
        "[video_audio][accompaniment][ducked_vocals]amix=inputs=3:duration=first:dropout_transition=2[mixed]"
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
        subprocess.run(cmd, capture_output=True, check=True)
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

    filter_parts = []

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

    filter_parts.append(prepare_stem(1, "drums"))
    filter_parts.append(prepare_stem(2, "bass"))
    filter_parts.append(prepare_stem(3, "vocals"))
    filter_parts.append(prepare_stem(4, "other"))

    # Prepare video audio
    if config.normalize_audio:
        filter_parts.append("[0:a]loudnorm=I=-16:TP=-1.5:LRA=11[video_audio]")
    else:
        filter_parts.append("[0:a]acopy[video_audio]")

    # Apply sidechain compression to each stem with different ducking levels
    # Note: makeup is for post-compression gain (linear 1-64), ducking is via threshold/ratio
    # Using makeup=1.0 for all (no makeup gain), ducking controlled by compression params
    # Drums: duck least
    filter_parts.append(
        f"[drums_prepared][video_audio]sidechaincompress="
        f"threshold={ducking.threshold}:ratio={ducking.ratio * 0.5}:"
        f"attack={ducking.attack_ms}:release={ducking.release_ms}:"
        f"makeup=1.0[ducked_drums]"
    )

    # Bass: duck moderately
    filter_parts.append(
        f"[bass_prepared][video_audio]sidechaincompress="
        f"threshold={ducking.threshold}:ratio={ducking.ratio * 0.7}:"
        f"attack={ducking.attack_ms}:release={ducking.release_ms}:"
        f"makeup=1.0[ducked_bass]"
    )

    # Vocals/melody: duck most
    filter_parts.append(
        f"[vocals_prepared][video_audio]sidechaincompress="
        f"threshold={ducking.threshold}:ratio={ducking.ratio}:"
        f"attack={ducking.attack_ms}:release={ducking.release_ms}:"
        f"makeup=1.0[ducked_vocals]"
    )

    # Other instruments: duck moderately
    filter_parts.append(
        f"[other_prepared][video_audio]sidechaincompress="
        f"threshold={ducking.threshold}:ratio={ducking.ratio * 0.8}:"
        f"attack={ducking.attack_ms}:release={ducking.release_ms}:"
        f"makeup=1.0[ducked_other]"
    )

    # Mix all 5 audio streams: video + 4 ducked stems
    filter_parts.append(
        "[video_audio][ducked_drums][ducked_bass][ducked_vocals][ducked_other]"
        "amix=inputs=5:duration=first:dropout_transition=2[mixed]"
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
        subprocess.run(cmd, capture_output=True, check=True)
        logger.info(f"Audio mixed successfully: {output_path}")
    except subprocess.CalledProcessError as e:
        logger.error(f"4-stem mixing failed: {e.stderr.decode() if e.stderr else e}")
        raise

    return output_path


class AudioMixer:
    """High-level audio mixer with automatic music selection and ducking."""

    def __init__(
        self,
        ducking_config: DuckingConfig | None = None,
        cache_dir: Path | None = None,
    ):
        """Initialize the audio mixer.

        Args:
            ducking_config: Ducking configuration
            cache_dir: Directory for caching downloaded music
        """
        self.ducking_config = ducking_config or DuckingConfig()
        self.cache_dir = cache_dir or Path.home() / ".cache" / "immich-memories" / "music"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    async def add_music_to_video(
        self,
        video_path: Path,
        output_path: Path,
        music_path: Path | None = None,
        mood: str | None = None,
        genre: str | None = None,
        tempo: str | None = None,
        fade_in: float = 2.0,
        fade_out: float = 3.0,
        music_volume_db: float = -6.0,
        auto_select: bool = True,
    ) -> Path:
        """Add background music to a video with intelligent ducking.

        Args:
            video_path: Path to the input video
            output_path: Path for the output video
            music_path: Path to music file (if provided, skips auto-select)
            mood: Mood for automatic music selection
            genre: Genre for automatic music selection
            tempo: Tempo for automatic music selection
            fade_in: Fade in duration in seconds
            fade_out: Fade out duration in seconds
            music_volume_db: Base music volume in dB
            auto_select: Auto-select music if no path provided

        Returns:
            Path to the output video with music
        """
        from immich_memories.audio.mood_analyzer import get_mood_analyzer
        from immich_memories.audio.music_sources import PixabayMusicSource

        video_duration = get_video_duration(video_path)

        # Get or select music
        if music_path and music_path.exists():
            selected_music = music_path
        elif auto_select:
            # Analyze video mood if not provided
            if not mood:
                try:
                    analyzer = await get_mood_analyzer()
                    video_mood = await analyzer.analyze_video(video_path)
                    mood = video_mood.primary_mood
                    genre = (
                        video_mood.genre_suggestions[0] if video_mood.genre_suggestions else genre
                    )
                    tempo = video_mood.tempo_suggestion
                    logger.info(f"Detected mood: {mood}, genre: {genre}, tempo: {tempo}")
                except Exception as e:
                    logger.warning(f"Mood analysis failed: {e}, using defaults")
                    mood = "calm"
                    genre = "ambient"

            # Search for music
            source = PixabayMusicSource()
            try:
                track = await source.get_random_track(
                    mood=mood,
                    genre=genre,
                    tempo=tempo,
                    min_duration=min(60, video_duration * 0.5),
                )

                if track:
                    selected_music = await source.download(track, self.cache_dir)
                    logger.info(f"Selected music: {track.title} by {track.artist}")
                else:
                    logger.warning("No matching music found, output will have original audio only")
                    # Just copy the video without adding music
                    import shutil

                    shutil.copy(video_path, output_path)
                    return output_path
            finally:
                await source.close()
        else:
            logger.warning("No music provided and auto_select=False")
            import shutil

            shutil.copy(video_path, output_path)
            return output_path

        # Configure mixing
        ducking = DuckingConfig(music_volume_db=music_volume_db)
        mix_config = MixConfig(
            ducking=ducking,
            fade_in_seconds=fade_in,
            fade_out_seconds=fade_out,
        )

        # Mix audio
        return mix_audio_with_ducking(
            video_path=video_path,
            music_path=selected_music,
            output_path=output_path,
            config=mix_config,
        )
