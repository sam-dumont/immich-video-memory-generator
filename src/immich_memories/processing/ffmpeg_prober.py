"""FFmpeg-based video probing service."""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

from immich_memories.processing.assembly_config import (
    AssemblyClip,
    AssemblySettings,
    TransitionType,
)

logger = logging.getLogger(__name__)


class FFmpegProber:
    """FFmpeg-based implementation of video probing.

    All methods are stateless probes that shell out to ffprobe, except
    ``estimate_duration`` which uses ``self.settings`` for transition config.
    """

    def __init__(self, settings: AssemblySettings) -> None:
        self.settings = settings

    def parse_resolution_from_stream(self, stream: dict) -> tuple[int, int] | None:
        """Swaps width/height when rotation is 90 or 270 degrees."""
        width = stream.get("width", 0)
        height = stream.get("height", 0)
        rotation = 0
        for side_data in stream.get("side_data_list", []):
            if "rotation" in side_data:
                rotation = abs(int(side_data["rotation"]))
                break
        if rotation in (90, 270):
            width, height = height, width
        if width and height:
            return width, height
        return None

    def get_video_resolution(self, video_path: Path) -> tuple[int, int] | None:
        """Get resolution accounting for rotation (iPhones store portrait as rotated landscape)."""
        try:
            cmd = [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v",
                "-show_entries",
                "stream=width,height:stream_side_data=rotation",
                "-of",
                "json",
                str(video_path),
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
            if result.returncode == 0 and result.stdout.strip():
                data = json.loads(result.stdout)
                streams = data.get("streams", [])
                if streams:
                    stream = max(streams, key=lambda s: s.get("width", 0) * s.get("height", 0))
                    return self.parse_resolution_from_stream(stream)
        except Exception as e:
            logger.debug(f"Failed to detect resolution: {e}")
        return None

    def pick_resolution_tier(
        self,
        resolution_counts: dict[str, int],
        total: int,
        orientation_str: str,
        res_4k: tuple[int, int],
        res_1080p: tuple[int, int],
        res_720p: tuple[int, int],
    ) -> tuple[int, int]:
        if resolution_counts["4k"] > total / 2:
            logger.info(
                f"Auto resolution: 4K {orientation_str} ({resolution_counts['4k']}/{total} clips are 4K)"
            )
            return res_4k
        if resolution_counts["1080p"] > total / 2:
            logger.info(
                f"Auto resolution: 1080p {orientation_str} ({resolution_counts['1080p']}/{total} clips are 1080p)"
            )
            return res_1080p
        if resolution_counts["720p"] > total / 2:
            logger.info(
                f"Auto resolution: 720p {orientation_str} ({resolution_counts['720p']}/{total} clips are 720p)"
            )
            return res_720p
        # No majority — use highest present
        if resolution_counts["4k"] > 0:
            logger.info(
                f"Auto resolution: 4K {orientation_str} (highest available, {resolution_counts['4k']}/{total} clips)"
            )
            return res_4k
        if resolution_counts["1080p"] > 0:
            logger.info(
                f"Auto resolution: 1080p {orientation_str} (highest available, {resolution_counts['1080p']}/{total} clips)"
            )
            return res_1080p
        logger.info(f"Auto resolution: 720p {orientation_str} (default)")
        return res_720p

    def detect_best_resolution(self, clips: list[AssemblyClip]) -> tuple[int, int]:
        resolution_counts: dict[str, int] = {"4k": 0, "1080p": 0, "720p": 0, "other": 0}
        orientation_counts: dict[str, int] = {"portrait": 0, "landscape": 0}

        for clip in clips:
            res = self.get_video_resolution(clip.path)
            if not res:
                continue
            w, h = res
            max_dim = max(w, h)
            orientation_counts["portrait" if h > w else "landscape"] += 1
            if max_dim >= 2160:
                resolution_counts["4k"] += 1
            elif max_dim >= 1080:
                resolution_counts["1080p"] += 1
            elif max_dim >= 720:
                resolution_counts["720p"] += 1
            else:
                resolution_counts["other"] += 1

        total = len(clips)
        if total == 0:
            logger.info("No clips to analyze, defaulting to 1080p landscape")
            return (1920, 1080)

        is_portrait = orientation_counts["portrait"] > orientation_counts["landscape"]
        orientation_str = "portrait" if is_portrait else "landscape"
        logger.info(
            f"Orientation: {orientation_str} "
            f"({orientation_counts['portrait']} portrait, {orientation_counts['landscape']} landscape)"
        )

        if is_portrait:
            res_4k, res_1080p, res_720p = (2160, 3840), (1080, 1920), (720, 1280)
        else:
            res_4k, res_1080p, res_720p = (3840, 2160), (1920, 1080), (1280, 720)

        return self.pick_resolution_tier(
            resolution_counts, total, orientation_str, res_4k, res_1080p, res_720p
        )

    def probe_duration(self, file_path: Path, stream_type: str = "audio") -> float:
        """Falls back to format duration if stream duration unavailable."""
        try:
            # Probe the specific stream's duration, not format duration
            # This is important because audio and video durations can differ
            stream_select = "a:0" if stream_type == "audio" else "v:0"
            result = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "quiet",
                    "-select_streams",
                    stream_select,
                    "-show_entries",
                    "stream=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(file_path),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            duration = result.stdout.strip()
            if duration and duration != "N/A":
                return float(duration)

            # Fallback to format duration if stream duration unavailable
            result = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "quiet",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(file_path),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            return float(result.stdout.strip())
        except (ValueError, subprocess.SubprocessError, subprocess.TimeoutExpired) as e:
            logger.warning(f"Failed to probe {stream_type} duration of {file_path}: {e}")
            return 0.0

    def probe_framerate(self, path: Path) -> float:
        try:
            result = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=r_frame_rate",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(path),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0 and result.stdout.strip():
                fps_str = result.stdout.strip()
                # Parse fraction like "30/1" or "60000/1001"
                if "/" in fps_str:
                    num, den = fps_str.split("/")
                    return float(num) / float(den)
                return float(fps_str)
        except (ValueError, subprocess.SubprocessError, subprocess.TimeoutExpired) as e:
            logger.warning(f"Failed to probe framerate of {path}: {e}")
        return 60.0  # Default fallback

    def has_audio_stream(self, path: Path) -> bool:
        try:
            cmd = [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a",
                "-show_entries",
                "stream=index,codec_name,sample_rate,channels",
                "-of",
                "json",
                str(path),
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                logger.warning(f"Failed to probe audio for {path.name}: {result.stderr[:200]}")
                return False

            data = json.loads(result.stdout)
            streams = data.get("streams", [])

            if not streams:
                logger.debug(f"No audio stream in {path.name}")
                return False

            # Log audio stream info for debugging
            for stream in streams:
                logger.debug(
                    f"Audio stream in {path.name}: "
                    f"codec={stream.get('codec_name')}, "
                    f"rate={stream.get('sample_rate')}, "
                    f"channels={stream.get('channels')}"
                )
            return True
        except (subprocess.SubprocessError, subprocess.TimeoutExpired, json.JSONDecodeError) as e:
            logger.warning(f"Error checking audio stream for {path.name}: {e}")
            return False

    def has_video_stream(self, path: Path) -> bool:
        try:
            cmd = [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v",
                "-show_entries",
                "stream=index",
                "-of",
                "csv=p=0",
                str(path),
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            return bool(result.stdout.strip())
        except (subprocess.SubprocessError, subprocess.TimeoutExpired) as e:
            logger.warning(f"Error checking video stream for {path.name}: {e}")
            return False

    def probe_batch_durations(
        self,
        batches: list[AssemblyClip],
    ) -> tuple[list[float], list[float]]:
        audio_durations: list[float] = []
        video_durations: list[float] = []
        for batch in batches:
            audio_dur = self.probe_duration(batch.path, stream_type="audio")
            video_dur = self.probe_duration(batch.path, stream_type="video")

            if audio_dur <= 0:
                logger.warning(
                    f"Could not probe audio duration of {batch.path}, using declared {batch.duration}"
                )
                audio_dur = batch.duration
            if video_dur <= 0:
                logger.warning(
                    f"Could not probe video duration of {batch.path}, using declared {batch.duration}"
                )
                video_dur = batch.duration

            if abs(audio_dur - video_dur) > 0.05:
                logger.warning(
                    f"A/V duration mismatch in {batch.path.name}: audio={audio_dur:.3f}s, video={video_dur:.3f}s"
                )
            if abs(audio_dur - batch.duration) > 0.1:
                logger.info(
                    f"Audio duration mismatch for {batch.path.name}: declared={batch.duration:.3f}s, actual={audio_dur:.3f}s"
                )

            audio_durations.append(audio_dur)
            video_durations.append(video_dur)

        return audio_durations, video_durations

    @staticmethod
    def parse_fps_str(fps_str: str) -> float | None:
        """Parse an FFmpeg frame rate fraction string like '60/1' or '60000/1001'."""
        if "/" in fps_str:
            num, den = fps_str.split("/")
            if float(den) > 0:
                return float(num) / float(den)
        elif fps_str:
            return float(fps_str)
        return None

    def detect_framerate(self, video_path: Path) -> float | None:
        try:
            cmd = [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v",
                "-show_entries",
                "stream=r_frame_rate,width,height",
                "-of",
                "json",
                str(video_path),
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
            if result.returncode == 0 and result.stdout.strip():
                data = json.loads(result.stdout)
                streams = data.get("streams", [])
                if streams:
                    best = max(streams, key=lambda s: s.get("width", 0) * s.get("height", 0))
                    return self.parse_fps_str(best.get("r_frame_rate", ""))
        except Exception as e:
            logger.debug(f"Failed to detect frame rate: {e}")
        return None

    def detect_max_framerate(self, clips: list[AssemblyClip]) -> int:
        """Max frame rate across clips, rounded to nearest common value (24/30/50/60)."""
        max_fps = 30.0
        for clip in clips[:20]:  # Sample first 20 clips for speed
            fps = self.detect_framerate(clip.path)
            if fps and fps > max_fps:
                max_fps = fps

        # Round to nearest common frame rate
        if max_fps >= 55:
            return 60
        elif max_fps >= 45:
            return 50
        elif max_fps >= 25:
            return 30
        return 24

    def estimate_duration(self, clips: list[AssemblyClip]) -> float:
        """Estimate final duration, accounting for transition overlaps."""
        if not clips:
            return 0.0

        total = sum(clip.duration for clip in clips)

        # Subtract transition overlaps
        if self.settings.transition == TransitionType.CROSSFADE and len(clips) > 1:
            overlap = (self.settings.transition_duration or 0.5) * (len(clips) - 1)
            total -= overlap

        return max(0, total)
