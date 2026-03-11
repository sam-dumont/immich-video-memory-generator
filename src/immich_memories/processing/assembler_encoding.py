"""Encoding and trimming methods for VideoAssembler.

This mixin provides single-clip encoding, segment trimming (copy and
re-encode modes), and PyAV output stream configuration.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from typing import Any

from immich_memories.config import get_config
from immich_memories.processing.assembly_config import (
    AssemblyClip,
    _get_rotation_filter,
)
from immich_memories.processing.hdr_utilities import (
    _detect_hdr_type,
    _get_colorspace_filter,
    _get_gpu_encoder_args,
)
from immich_memories.security import validate_video_path

logger = logging.getLogger(__name__)


class AssemblerEncodingMixin:
    """Mixin providing encoding and trimming methods for VideoAssembler."""

    def _encode_single_clip(
        self,
        clip: AssemblyClip,
        output_path: Path,
        target_resolution: tuple[int, int] | None = None,
    ) -> None:
        """Encode a single clip to target format with A/V sync guarantee.

        Uses filter_complex with anullsrc fallback to handle clips that may
        have no audio stream or audio with different properties.

        Args:
            clip: The clip to encode.
            output_path: Output path for encoded clip.
            target_resolution: Target (width, height). If None, uses settings.
        """
        validate_video_path(clip.path, must_exist=True)

        # Determine target resolution
        if target_resolution:
            target_w, target_h = target_resolution
        elif self.settings.target_resolution:
            target_w, target_h = self.settings.target_resolution
        else:
            config = get_config()
            target_w, target_h = config.output.resolution_tuple

        # Pixel format and HDR
        pix_fmt = (
            ("p010le" if sys.platform == "darwin" else "yuv420p10le")
            if self.settings.preserve_hdr
            else "yuv420p"
        )
        target_fps = 60

        hdr_type = "hlg"
        if self.settings.preserve_hdr:
            clip_hdr = _detect_hdr_type(clip.path)
            if clip_hdr:
                hdr_type = clip_hdr
            colorspace_filter = _get_colorspace_filter(hdr_type)
        else:
            colorspace_filter = ""

        # Handle rotation
        rotation_filter = ""
        if clip.rotation_override is not None and clip.rotation_override != 0:
            rotation_filter = _get_rotation_filter(clip.rotation_override) + ","

        # Check audio
        has_audio = self._has_audio_stream(clip.path)

        # Detect source framerate for frame blending
        source_fps = self._probe_framerate(clip.path)
        if source_fps < 50:
            fps_filter = f"fps={target_fps},tmix=frames=2:weights='1 1'"
        else:
            fps_filter = f"fps={target_fps}"

        common_suffix = (
            f"{fps_filter},settb=1/{target_fps},"
            f"format={pix_fmt}{colorspace_filter},setsar=1,"
            f"trim=0:{clip.duration},setpts=PTS-STARTPTS"
        )

        # Build audio filter
        audio_format = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
        # EBU R128 loudness normalization: -16 LUFS target, preserves dynamics (LRA=11)
        # Skip for title screens — their silent audio track causes loudnorm to produce NaN
        use_loudnorm = self.settings.normalize_clip_audio and not clip.is_title_screen
        loudnorm = ",loudnorm=I=-16:TP=-1.5:LRA=11" if use_loudnorm else ""
        if has_audio:
            audio_filter = (
                f"[0:a]{audio_format},asetpts=PTS-STARTPTS{loudnorm},"
                f"apad=whole_dur={clip.duration},atrim=0:{clip.duration},asetpts=PTS-STARTPTS[aout]"
            )
        else:
            audio_filter = f"anullsrc=r=48000:cl=stereo,atrim=0:{clip.duration},{audio_format},asetpts=PTS-STARTPTS[aout]"

        # Build video filter with scale mode handling
        filter_complex = self._build_single_clip_filter(
            clip,
            target_w,
            target_h,
            rotation_filter,
            common_suffix,
            audio_filter,
        )

        video_codec_args = _get_gpu_encoder_args(
            crf=self.settings.output_crf,
            preserve_hdr=self.settings.preserve_hdr,
            hdr_type=hdr_type,
        )

        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(clip.path),
            "-filter_complex",
            filter_complex,
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            *video_codec_args,
            "-r",
            str(target_fps),
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            str(output_path),
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if result.returncode != 0:
            raise RuntimeError(f"Failed to encode clip: {result.stderr[-500:]}")

    def _trim_segment_copy(
        self,
        input_path: Path,
        output_path: Path,
        start: float,
        duration: float,
    ) -> None:
        """Trim a video segment using stream copy (instant, no re-encoding).

        Args:
            input_path: Input video path.
            output_path: Output path for trimmed segment.
            start: Start time in seconds.
            duration: Duration in seconds.
        """
        validate_video_path(input_path, must_exist=True)
        cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            str(start),
            "-i",
            str(input_path),
            "-t",
            str(duration),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(output_path),
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if result.returncode != 0:
            raise RuntimeError(f"Failed to trim segment: {result.stderr[-500:]}")

    def _trim_segment_reencode(
        self,
        input_path: Path,
        output_path: Path,
        start: float,
        duration: float,
    ) -> None:
        """Trim a video segment with re-encoding for frame-accurate boundaries.

        Uses filter_complex with anullsrc mixing to guarantee audio output,
        even when trimming causes audio/video misalignment.

        Args:
            input_path: Input video path.
            output_path: Output path for trimmed segment.
            start: Start time in seconds.
            duration: Duration in seconds.
        """
        validate_video_path(input_path, must_exist=True)

        # Get encoder args matching main encoding settings
        video_codec_args = _get_gpu_encoder_args(
            crf=self.settings.output_crf,
            preserve_hdr=self.settings.preserve_hdr,
        )

        audio_format = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
        # EBU R128 loudness normalization: -16 LUFS target, preserves dynamics (LRA=11)
        loudnorm = ",loudnorm=I=-16:TP=-1.5:LRA=11" if self.settings.normalize_clip_audio else ""

        # Use filter_complex with anullsrc mixing to guarantee audio
        filter_complex = (
            # Video: trim and reset timestamps
            f"[0:v]trim=start={start}:duration={duration},setpts=PTS-STARTPTS[vout];"
            # Generate silence for guaranteed duration
            f"anullsrc=r=48000:cl=stereo,atrim=0:{duration}[silence];"
            # Try to extract audio
            f"[0:a]atrim=start={start}:duration={duration},{audio_format},"
            f"asetpts=PTS-STARTPTS{loudnorm},apad=whole_dur={duration}[asrc];"
            # Mix: silence provides guaranteed duration, source provides content
            # Final atrim + asetpts ensures exact duration and resets timestamps
            # to prevent AAC priming issues during concat
            f"[silence][asrc]amix=inputs=2:duration=longest:weights='0.001 1',"
            f"atrim=0:{duration},asetpts=PTS-STARTPTS[aout]"
        )

        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(input_path),
            "-filter_complex",
            filter_complex,
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            *video_codec_args,
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            str(output_path),
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)

        # Fallback: if audio extraction failed, use silence only
        if result.returncode != 0:
            logger.warning(f"Trim with audio failed, using silence: {result.stderr[-200:]}")

            filter_complex_silent = (
                f"[0:v]trim=start={start}:duration={duration},setpts=PTS-STARTPTS[vout];"
                f"anullsrc=r=48000:cl=stereo,atrim=0:{duration},{audio_format},asetpts=PTS-STARTPTS[aout]"
            )

            cmd_silent = [
                "ffmpeg",
                "-y",
                "-i",
                str(input_path),
                "-filter_complex",
                filter_complex_silent,
                "-map",
                "[vout]",
                "-map",
                "[aout]",
                *video_codec_args,
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-movflags",
                "+faststart",
                str(output_path),
            ]

            result = subprocess.run(cmd_silent, capture_output=True, text=True, timeout=1800)
            if result.returncode != 0:
                raise RuntimeError(f"Failed to trim segment (reencode): {result.stderr[-500:]}")

    @staticmethod
    def _configure_pyav_output_stream(
        output_container: Any,
        target_fps: int,
        is_hdr: bool,
        width: int,
        height: int,
        crf: int,
    ) -> Any:
        """Configure a PyAV output stream based on platform and HDR settings.

        Args:
            output_container: PyAV output container.
            target_fps: Target frame rate.
            is_hdr: Whether HDR is enabled.
            width: Video width.
            height: Video height.
            crf: CRF quality value.

        Returns:
            Configured PyAV output stream.
        """
        if sys.platform == "darwin":
            output_stream = output_container.add_stream("hevc_videotoolbox", rate=target_fps)
            if is_hdr:
                output_stream.pix_fmt = "p010le"
                output_stream.options = {
                    "tag": "hvc1",
                    "colorspace": "bt2020nc",
                    "color_primaries": "bt2020",
                    "color_trc": "arib-std-b67",
                }
            else:
                output_stream.pix_fmt = "yuv420p"
                output_stream.options = {"tag": "hvc1"}
        else:
            output_stream = output_container.add_stream("libx265", rate=target_fps)
            output_stream.pix_fmt = "yuv420p10le" if is_hdr else "yuv420p"
            output_stream.options = {"crf": str(crf), "preset": "fast"}

        output_stream.width = width
        output_stream.height = height
        return output_stream

    def _build_single_clip_filter(
        self,
        clip: AssemblyClip,
        target_w: int,
        target_h: int,
        rotation_filter: str,
        common_suffix: str,
        audio_filter: str,
    ) -> str:
        """Build filter_complex for encoding a single clip with scale mode handling.

        Args:
            clip: The clip to build filters for.
            target_w: Target width.
            target_h: Target height.
            rotation_filter: Rotation filter prefix (e.g., "transpose=1,").
            common_suffix: Common video filter suffix (fps, format, trim, etc.).
            audio_filter: Audio filter string.

        Returns:
            Complete filter_complex string.
        """
        from immich_memories.processing.scaling_utilities import _get_smart_crop_filter

        use_blur = self.settings.scale_mode == "blur" and not clip.is_title_screen
        use_smart_zoom = self.settings.scale_mode == "smart_zoom" and not clip.is_title_screen

        if use_smart_zoom:
            face_center = self._get_face_center(clip.path)
            if face_center:
                clip_res = self._get_video_resolution(clip.path)
                if clip_res:
                    src_w, src_h = clip_res
                    crop_filter = _get_smart_crop_filter(
                        src_w, src_h, target_w, target_h, face_center[0], face_center[1]
                    )
                    video_filter = (
                        f"{rotation_filter}setpts=PTS-STARTPTS,{crop_filter},{common_suffix}"
                    )
                    logger.info(
                        f"Smart zoom: cropping centered on face at ({face_center[0]:.2f}, {face_center[1]:.2f})"
                    )
                    return f"[0:v]{video_filter}[vout];{audio_filter}"
                else:
                    use_blur = True
            else:
                logger.debug(f"No face detected in {clip.path.name}, using blur background")
                use_blur = True

        if use_blur:
            return (
                f"[0:v]{rotation_filter}setpts=PTS-STARTPTS,split[bg][fg];"
                f"[bg]scale={target_w}:{target_h}:force_original_aspect_ratio=increase:flags=fast_bilinear,"
                f"crop={target_w}:{target_h},boxblur=luma_radius=150:chroma_radius=150:luma_power=3:chroma_power=3[blurred];"
                f"[fg]scale={target_w}:{target_h}:force_original_aspect_ratio=decrease:flags=lanczos[scaled];"
                f"[blurred][scaled]overlay=(W-w)/2:(H-h)/2,{common_suffix}[vout];"
                f"{audio_filter}"
            )

        # Black bars (letterbox/pillarbox) - default for title screens or explicit black_bars mode
        video_filter = (
            f"{rotation_filter}setpts=PTS-STARTPTS,"
            f"scale={target_w}:{target_h}:"
            f"force_original_aspect_ratio=decrease:flags=lanczos,"
            f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:black,"
            f"{common_suffix}"
        )
        return f"[0:v]{video_filter}[vout];{audio_filter}"

    def _build_probed_audio_filters(
        self,
        batches: list[AssemblyClip],
        audio_durations: list[float],
    ) -> tuple[list[str], list[str]]:
        """Build audio prep filters using probed durations.

        Args:
            batches: List of batch clips.
            audio_durations: Probed audio durations for each batch.

        Returns:
            Tuple of (filter_parts, audio_labels).
        """
        audio_format = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
        filter_parts: list[str] = []
        audio_labels: list[str] = []
        for i, (_batch, audio_dur) in enumerate(zip(batches, audio_durations, strict=False)):
            filter_parts.append(
                f"anullsrc=r=48000:cl=stereo,atrim=0:{audio_dur}[a{i}silence];"
                f"[{i}:a]{audio_format},asetpts=PTS-STARTPTS[a{i}src];"
                f"[a{i}silence][a{i}src]amix=inputs=2:duration=first:weights='0 1'[a{i}mixed];"
                f"[a{i}mixed]atrim=0:{audio_dur},asetpts=PTS-STARTPTS[a{i}prep]"
            )
            audio_labels.append(f"[a{i}prep]")
        return filter_parts, audio_labels

    def _build_probed_xfade_chain(
        self,
        batches: list[AssemblyClip],
        video_durations: list[float],
        fade_duration: float,
        target_fps: int,
        audio_labels: list[str],
    ) -> tuple[list[str], str, str, float]:
        """Build xfade chain using probed video durations for offsets.

        Args:
            batches: List of batch clips.
            video_durations: Probed video durations.
            fade_duration: Fade duration in seconds.
            target_fps: Target frame rate.
            audio_labels: Audio labels from _build_probed_audio_filters.

        Returns:
            Tuple of (filter_parts, final_video_label, final_audio_label, total_video_offset).
        """
        filter_parts: list[str] = []
        current_video = "[v0scaled]"
        current_audio = audio_labels[0]
        video_offset = 0.0

        for i in range(len(batches) - 1):
            next_idx = i + 1
            video_label = f"[v{i}{next_idx}]"
            audio_label = f"[a{i}{next_idx}]"

            offset = video_offset + video_durations[i] - fade_duration

            filter_parts.append(
                f"{current_video}[v{next_idx}scaled]xfade=transition=fade:"
                f"duration={fade_duration}:offset={offset},settb=1/{target_fps}{video_label}"
            )
            filter_parts.append(
                f"{current_audio}{audio_labels[next_idx]}acrossfade=d={fade_duration}:c1=tri:c2=tri{audio_label}"
            )

            current_video = video_label
            current_audio = audio_label
            video_offset = offset

        return filter_parts, current_video, current_audio, video_offset
