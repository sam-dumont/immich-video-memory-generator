"""Audio mixing service for adding background music to videos.

Supports simple mixing, 2-stem ducking, and 4-stem ducking strategies.
"""

from __future__ import annotations

import logging
import math
import subprocess
from pathlib import Path

from immich_memories.processing.assembly_config import AssemblySettings

logger = logging.getLogger(__name__)


class AudioMixerService:
    """Add background music to videos with intelligent ducking."""

    def __init__(self, settings: AssemblySettings) -> None:
        self.settings = settings

    def add_music(self, video_path: Path, output_path: Path) -> Path:
        """Add background music with the best available ducking strategy.

        Tries 4-stem, then 2-stem, then simple mixing based on available stems.
        """
        # Check for 4-stem mode first (more granular control)
        drums = self.settings.music_drums_path
        bass = self.settings.music_bass_path
        vocals = self.settings.music_vocals_path
        other = self.settings.music_other_path

        if (
            drums
            and drums.exists()
            and bass
            and bass.exists()
            and vocals
            and vocals.exists()
            and other
            and other.exists()
        ):
            return self._add_music_with_4stems(video_path, output_path, drums, bass, vocals, other)

        # Check for 2-stem mode
        accompaniment = self.settings.music_accompaniment_path
        if vocals and vocals.exists() and accompaniment and accompaniment.exists():
            return self._add_music_with_stems(video_path, output_path, vocals, accompaniment)

        # Fallback to simple mixing
        return self._add_music_simple(video_path, output_path)

    def add_music_to_clip(self, clip_path: Path, output_path: Path) -> Path:
        """Add music to a single clip."""
        return self.add_music(clip_path, output_path)

    def _volume_db(self) -> float:
        """Convert volume (0.0-1.0) to dB."""
        return 20 * math.log10(max(0.01, self.settings.music_volume))

    def _add_music_with_stems(
        self,
        video_path: Path,
        output_path: Path,
        vocals_path: Path,
        accompaniment_path: Path,
    ) -> Path:
        """Add music with 2-stem ducking (ducks vocals during speech)."""
        from immich_memories.audio.mixer import (
            DuckingConfig,
            MixConfig,
            mix_audio_with_stem_ducking,
        )

        logger.info("Using stem-based audio ducking (vocals duck during speech, drums stay full)")

        config = MixConfig(
            ducking=DuckingConfig(
                music_volume_db=self._volume_db(),
                threshold=0.02,
                ratio=6.0,
                attack_ms=50.0,
                release_ms=500.0,
            ),
            fade_in_seconds=2.0,
            fade_out_seconds=3.0,
        )

        try:
            return mix_audio_with_stem_ducking(
                video_path=video_path,
                vocals_path=vocals_path,
                accompaniment_path=accompaniment_path,
                output_path=output_path,
                config=config,
                duck_vocals_db=-12.0,
            )
        except Exception as e:
            logger.warning(f"Stem-based mixing failed, falling back to simple mix: {e}")
            return self._add_music_simple(video_path, output_path)

    def _add_music_with_4stems(
        self,
        video_path: Path,
        output_path: Path,
        drums_path: Path,
        bass_path: Path,
        vocals_path: Path,
        other_path: Path,
    ) -> Path:
        """Add music with 4-stem ducking.

        Ducking levels: drums -3dB, bass -6dB, vocals -12dB, other -9dB.
        """
        from immich_memories.audio.mixer import (
            DuckingConfig,
            MixConfig,
            StemDuckingLevels,
            mix_audio_with_4stem_ducking,
        )

        logger.info("Using 4-stem audio ducking (drums 50%%, bass 60%%, melody 75%%, other 70%%)")

        config = MixConfig(
            ducking=DuckingConfig(
                music_volume_db=self._volume_db(),
                threshold=0.02,
                ratio=6.0,
                attack_ms=50.0,
                release_ms=500.0,
            ),
            fade_in_seconds=2.0,
            fade_out_seconds=3.0,
        )

        ducking_levels = StemDuckingLevels(
            drums_db=-3.0,
            bass_db=-6.0,
            vocals_db=-12.0,
            other_db=-9.0,
        )

        try:
            return mix_audio_with_4stem_ducking(
                video_path=video_path,
                drums_path=drums_path,
                bass_path=bass_path,
                vocals_path=vocals_path,
                other_path=other_path,
                output_path=output_path,
                config=config,
                ducking_levels=ducking_levels,
            )
        except Exception as e:
            logger.warning(f"4-stem mixing failed, falling back to simple mix: {e}")
            return self._add_music_simple(video_path, output_path)

    def _add_music_simple(self, video_path: Path, output_path: Path) -> Path:
        """Add music with simple volume mixing (no ducking)."""
        if video_path == output_path:
            temp_output = output_path.with_suffix(".temp.mp4")
        else:
            temp_output = output_path

        music_vol = self.settings.music_volume

        filter_complex = (
            f"[1:a]volume={music_vol}[music];[0:a][music]amix=inputs=2:duration=first[aout]"
        )

        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-i",
            str(self.settings.music_path),
            "-filter_complex",
            filter_complex,
            "-map",
            "0:v",
            "-map",
            "[aout]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            str(temp_output),
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)

        if result.returncode != 0:
            logger.warning(f"Failed to add music: {result.stderr}")
            return video_path

        if temp_output != output_path:
            import shutil

            shutil.move(temp_output, output_path)

        return output_path
