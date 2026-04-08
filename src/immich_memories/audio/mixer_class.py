"""High-level AudioMixer class for automatic music selection and ducking."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from immich_memories.audio.mixer import DuckingConfig

logger = logging.getLogger(__name__)


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
        from immich_memories.audio.mixer import DuckingConfig

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
        from immich_memories.audio.mixer import (
            DuckingConfig,
            MixConfig,
            get_video_duration,
            mix_audio_with_ducking,
        )

        video_duration = get_video_duration(video_path)

        # Get or select music
        if music_path and music_path.exists():
            selected_music = music_path
        elif auto_select:
            selected_music = await self._auto_select_music(
                video_path=video_path,
                output_path=output_path,
                video_duration=video_duration,
                mood=mood,
                genre=genre,
                tempo=tempo,
            )
            if selected_music is None:
                return output_path
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

    async def _auto_select_music(
        self,
        video_path: Path,
        output_path: Path,
        video_duration: float,
        mood: str | None,
        genre: str | None,
        tempo: str | None,
    ) -> Path | None:
        """Auto-select music based on mood analysis.

        Returns:
            Path to selected music, or None if no music found (video copied to output).
        """
        from immich_memories.audio.mood_analyzer_backends import get_mood_analyzer
        from immich_memories.audio.music_sources import LocalMusicSource

        # Analyze video mood if not provided
        if not mood:
            try:
                analyzer = await get_mood_analyzer()
                video_mood = await analyzer.analyze_video(video_path)
                mood = video_mood.primary_mood
                genre = video_mood.genre_suggestions[0] if video_mood.genre_suggestions else genre
                tempo = video_mood.tempo_suggestion
                logger.info(f"Detected mood: {mood}, genre: {genre}, tempo: {tempo}")
            except (RuntimeError, ImportError, OSError) as e:
                logger.warning(f"Mood analysis failed: {e}, using defaults")
                mood = "calm"
                genre = "ambient"

        # Search for music in local library
        source = LocalMusicSource(music_dir=self.cache_dir)
        track = await source.get_random_track(
            mood=mood,
            genre=genre,
            tempo=tempo,
            min_duration=min(60, video_duration * 0.5),
        )

        if track:
            selected_music = await source.download(track, self.cache_dir)
            logger.info(f"Selected music: {track.title} by {track.artist}")
            return selected_music
        else:
            logger.warning("No matching music found, output will have original audio only")
            import shutil

            shutil.copy(video_path, output_path)
            return None
