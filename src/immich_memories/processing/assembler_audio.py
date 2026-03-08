"""Audio and music methods for VideoAssembler.

This mixin provides music addition with various ducking strategies:
simple mixing, 2-stem ducking, and 4-stem ducking.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


class AssemblerAudioMixin:
    """Mixin providing audio/music methods for VideoAssembler."""

    def _add_music(self, video_path: Path, output_path: Path) -> Path:
        """Add background music to video with intelligent ducking.

        Uses stem-based ducking when stems are available (from MusicGen/Demucs):
        - 4-stem mode: drums duck 50%, bass 60%, melody 75%, other 70%
        - 2-stem mode: vocals/melody ducked, accompaniment at full volume

        Falls back to simple amix when no stems available.

        Args:
            video_path: Input video path.
            output_path: Output path.

        Returns:
            Path to output video.
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
            # Use 4-stem ducking for granular control
            return self._add_music_with_4stems(video_path, output_path, drums, bass, vocals, other)

        # Check for 2-stem mode
        accompaniment = self.settings.music_accompaniment_path
        if vocals and vocals.exists() and accompaniment and accompaniment.exists():
            # Use 2-stem ducking
            return self._add_music_with_stems(video_path, output_path, vocals, accompaniment)

        # Fallback to simple mixing
        return self._add_music_simple(video_path, output_path)

    def _add_music_with_stems(
        self,
        video_path: Path,
        output_path: Path,
        vocals_path: Path,
        accompaniment_path: Path,
    ) -> Path:
        """Add music with stem-based ducking (ducks vocals during speech).

        Args:
            video_path: Input video path.
            output_path: Output path.
            vocals_path: Path to vocals/melody stem.
            accompaniment_path: Path to drums+bass stem.

        Returns:
            Path to output video.
        """
        from immich_memories.audio.mixer import (
            DuckingConfig,
            MixConfig,
            mix_audio_with_stem_ducking,
        )

        logger.info("Using stem-based audio ducking (vocals duck during speech, drums stay full)")

        # Convert volume (0.0-1.0) to dB
        # 0.3 volume ~ -10dB, 0.5 ~ -6dB, 1.0 = 0dB
        import math

        volume_db = 20 * math.log10(max(0.01, self.settings.music_volume))

        config = MixConfig(
            ducking=DuckingConfig(
                music_volume_db=volume_db,
                threshold=0.02,  # Sensitive to speech
                ratio=6.0,  # Strong ducking
                attack_ms=50.0,  # Fast attack
                release_ms=500.0,  # Smooth release
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
                duck_vocals_db=-12.0,  # Duck vocals 12dB during speech
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
        """Add music with 4-stem ducking (granular control per instrument).

        Ducking levels during speech:
        - Drums: -3dB (~50% reduction) - keeps energy
        - Bass: -6dB (~60% reduction)
        - Vocals/melody: -12dB (~75% reduction) - avoids competing with speech
        - Other: -9dB (~70% reduction)

        Args:
            video_path: Input video path.
            output_path: Output path.
            drums_path: Path to drums stem.
            bass_path: Path to bass stem.
            vocals_path: Path to vocals/melody stem.
            other_path: Path to other instruments stem.

        Returns:
            Path to output video.
        """
        from immich_memories.audio.mixer import (
            DuckingConfig,
            MixConfig,
            StemDuckingLevels,
            mix_audio_with_4stem_ducking,
        )

        logger.info("Using 4-stem audio ducking (drums 50%, bass 60%, melody 75%, other 70%)")

        # Convert volume (0.0-1.0) to dB
        import math

        volume_db = 20 * math.log10(max(0.01, self.settings.music_volume))

        config = MixConfig(
            ducking=DuckingConfig(
                music_volume_db=volume_db,
                threshold=0.02,  # Sensitive to speech
                ratio=6.0,  # Strong ducking
                attack_ms=50.0,  # Fast attack
                release_ms=500.0,  # Smooth release
            ),
            fade_in_seconds=2.0,
            fade_out_seconds=3.0,
        )

        # Custom ducking levels per stem
        ducking_levels = StemDuckingLevels(
            drums_db=-3.0,  # ~50% reduction
            bass_db=-6.0,  # ~60% reduction
            vocals_db=-12.0,  # ~75% reduction
            other_db=-9.0,  # ~70% reduction
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
        """Add music with simple volume mixing (no ducking).

        Args:
            video_path: Input video path.
            output_path: Output path.

        Returns:
            Path to output video.
        """
        if video_path == output_path:
            temp_output = output_path.with_suffix(".temp.mp4")
        else:
            temp_output = output_path

        music_vol = self.settings.music_volume

        # Mix original audio with music
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

    def _add_music_to_clip(self, clip_path: Path, output_path: Path) -> Path:
        """Add music to a single clip.

        Args:
            clip_path: Input clip path.
            output_path: Output path.

        Returns:
            Path to output video.
        """
        return self._add_music(clip_path, output_path)
