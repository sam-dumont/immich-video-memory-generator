"""Probing, resolution detection, and framerate utilities for VideoAssembler.

This mixin provides ffprobe-based utilities for detecting video properties
including resolution, frame rate, duration, and stream presence.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

from immich_memories.processing.assembly_config import (
    AssemblyClip,
    TransitionType,
)

logger = logging.getLogger(__name__)


class AssemblerProbingMixin:
    """Mixin providing probing and detection methods for VideoAssembler."""

    def _get_video_resolution(self, video_path: Path) -> tuple[int, int] | None:
        """Get video resolution (width, height) accounting for rotation.

        iPhone videos are often stored as landscape but have rotation metadata
        that makes them portrait when displayed. This function detects rotation
        and swaps dimensions accordingly.

        Args:
            video_path: Path to video file.

        Returns:
            Tuple of (width, height) after applying rotation, or None if detection fails.
        """
        try:
            # Get width, height, and rotation in one call
            cmd = [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
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
                    stream = streams[0]
                    width = stream.get("width", 0)
                    height = stream.get("height", 0)

                    # Check for rotation in side_data_list
                    rotation = 0
                    for side_data in stream.get("side_data_list", []):
                        if "rotation" in side_data:
                            rotation = abs(int(side_data["rotation"]))
                            break

                    # Swap dimensions for 90 or 270 degree rotation (portrait videos)
                    if rotation in (90, 270):
                        width, height = height, width

                    if width and height:
                        return width, height
        except Exception as e:
            logger.debug(f"Failed to detect resolution: {e}")
        return None

    def _detect_best_resolution(self, clips: list[AssemblyClip]) -> tuple[int, int]:
        """Detect the best output resolution based on majority of clips.

        Logic:
        - Count clips at each resolution tier (4K, 1080p, 720p)
        - Detect orientation (portrait vs landscape) from majority
        - Use the resolution that >50% of clips have
        - If no majority, use the highest resolution present

        Args:
            clips: List of clips to analyze.

        Returns:
            Tuple of (width, height) for output resolution.
        """
        resolution_counts = {"4k": 0, "1080p": 0, "720p": 0, "other": 0}
        orientation_counts = {"portrait": 0, "landscape": 0}
        resolutions_found = []

        for clip in clips:
            res = self._get_video_resolution(clip.path)
            if res:
                w, h = res
                # Use the larger dimension to handle portrait/landscape
                max_dim = max(w, h)
                resolutions_found.append(max_dim)

                # Track orientation
                if h > w:
                    orientation_counts["portrait"] += 1
                else:
                    orientation_counts["landscape"] += 1

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

        # Determine orientation from majority
        is_portrait = orientation_counts["portrait"] > orientation_counts["landscape"]
        orientation_str = "portrait" if is_portrait else "landscape"
        logger.info(
            f"Orientation: {orientation_str} "
            f"({orientation_counts['portrait']} portrait, {orientation_counts['landscape']} landscape)"
        )

        # Resolution tuples based on orientation
        if is_portrait:
            res_4k = (2160, 3840)
            res_1080p = (1080, 1920)
            res_720p = (720, 1280)
        else:
            res_4k = (3840, 2160)
            res_1080p = (1920, 1080)
            res_720p = (1280, 720)

        # Check for majority (>50%)
        if resolution_counts["4k"] > total / 2:
            logger.info(
                f"Auto resolution: 4K {orientation_str} ({resolution_counts['4k']}/{total} clips are 4K)"
            )
            return res_4k
        elif resolution_counts["1080p"] > total / 2:
            logger.info(
                f"Auto resolution: 1080p {orientation_str} ({resolution_counts['1080p']}/{total} clips are 1080p)"
            )
            return res_1080p
        elif resolution_counts["720p"] > total / 2:
            logger.info(
                f"Auto resolution: 720p {orientation_str} ({resolution_counts['720p']}/{total} clips are 720p)"
            )
            return res_720p
        else:
            # No majority - use the highest resolution present
            if resolution_counts["4k"] > 0:
                logger.info(
                    f"Auto resolution: 4K {orientation_str} (highest available, {resolution_counts['4k']}/{total} clips)"
                )
                return res_4k
            elif resolution_counts["1080p"] > 0:
                logger.info(
                    f"Auto resolution: 1080p {orientation_str} (highest available, {resolution_counts['1080p']}/{total} clips)"
                )
                return res_1080p
            else:
                logger.info(f"Auto resolution: 720p {orientation_str} (default)")
                return res_720p

    def _probe_duration(self, file_path: Path, stream_type: str = "audio") -> float:
        """Probe actual duration of a specific stream using ffprobe.

        Args:
            file_path: Path to the media file.
            stream_type: Stream type to probe ("audio" or "video"). Default "audio".

        Returns:
            Duration in seconds, or 0.0 if probing fails.
        """
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

    def _probe_framerate(self, path: Path) -> float:
        """Probe the frame rate of a video file.

        Args:
            path: Path to the video file.

        Returns:
            Frame rate as a float (e.g., 30.0, 59.94, 60.0).
            Returns 60.0 as fallback if probing fails.
        """
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

    def _has_audio_stream(self, path: Path) -> bool:
        """Check if video file has an audio stream.

        Args:
            path: Path to the video file.

        Returns:
            True if the file has at least one audio stream.
        """
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

    def _has_video_stream(self, path: Path) -> bool:
        """Check if file has a video stream.

        Args:
            path: Path to the file.

        Returns:
            True if the file has at least one video stream.
        """
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

    def _probe_batch_durations(
        self,
        batches: list[AssemblyClip],
    ) -> tuple[list[float], list[float]]:
        """Probe actual audio and video durations for batch files.

        Args:
            batches: List of batch clips to probe.

        Returns:
            Tuple of (audio_durations, video_durations).
        """
        audio_durations: list[float] = []
        video_durations: list[float] = []
        for batch in batches:
            audio_dur = self._probe_duration(batch.path, stream_type="audio")
            video_dur = self._probe_duration(batch.path, stream_type="video")

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

    def _detect_framerate(self, video_path: Path) -> float | None:
        """Detect frame rate of a video file.

        Args:
            video_path: Path to video file.

        Returns:
            Frame rate in fps, or None if detection fails.
        """
        try:
            cmd = [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=r_frame_rate",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
            if result.returncode == 0 and result.stdout.strip():
                # Parse fraction like "60/1" or "30000/1001"
                fps_str = result.stdout.strip()
                if "/" in fps_str:
                    num, den = fps_str.split("/")
                    return float(num) / float(den)
                return float(fps_str)
        except Exception as e:
            logger.debug(f"Failed to detect frame rate: {e}")
        return None

    def _detect_max_framerate(self, clips: list[AssemblyClip]) -> int:
        """Detect the maximum frame rate from a list of clips.

        Args:
            clips: List of clips to analyze.

        Returns:
            Maximum frame rate (rounded to nearest common value), default 30.
        """
        max_fps = 30.0
        for clip in clips[:20]:  # Sample first 20 clips for speed
            fps = self._detect_framerate(clip.path)
            if fps and fps > max_fps:
                max_fps = fps

        # Round to nearest common frame rate
        if max_fps >= 55:
            return 60
        elif max_fps >= 45:
            return 50
        elif max_fps >= 25:
            return 30
        else:
            return 24

    def estimate_duration(self, clips: list[AssemblyClip]) -> float:
        """Estimate final video duration.

        Args:
            clips: List of clips.

        Returns:
            Estimated duration in seconds.
        """
        if not clips:
            return 0.0

        total = sum(clip.duration for clip in clips)

        # Subtract transition overlaps
        if self.settings.transition == TransitionType.CROSSFADE and len(clips) > 1:
            overlap = self.settings.transition_duration * (len(clips) - 1)
            total -= overlap

        return max(0, total)
