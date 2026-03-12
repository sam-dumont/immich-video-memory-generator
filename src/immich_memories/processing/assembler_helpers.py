"""Shared helper methods for VideoAssembler.

This mixin provides filter-building, context creation, and FFmpeg command
construction utilities used by multiple assembly strategies.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from immich_memories.config import get_config
from immich_memories.processing.assembly_config import (
    AssemblyClip,
    _get_rotation_filter,
)
from immich_memories.processing.ffmpeg_runner import (
    AssemblyContext,
    _run_ffmpeg_with_progress,
)
from immich_memories.processing.hdr_utilities import (
    _detect_color_primaries,
    _get_clip_hdr_types,
    _get_colorspace_filter,
    _get_dominant_hdr_type,
    _get_gpu_encoder_args,
    _get_hdr_conversion_filter,
)
from immich_memories.processing.scaling_utilities import (
    _get_aspect_ratio_filter,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class AssemblerHelpersMixin:
    """Mixin providing shared helper methods for VideoAssembler."""

    def _resolve_target_resolution(self, clips: list[AssemblyClip]) -> tuple[int, int]:
        """Resolve target resolution from settings, auto-detection, or config default.

        Also handles portrait swap when majority of clips are portrait orientation.

        Args:
            clips: List of clips to analyze for resolution/orientation.

        Returns:
            Tuple of (width, height) for the target resolution.
        """
        if self.settings.target_resolution:
            target_w, target_h = self.settings.target_resolution
            logger.info(f"Using specified resolution {target_w}x{target_h}")
            # Only swap for explicit resolution — auto-detect already handles orientation
            target_w, target_h = self._swap_if_portrait(clips, target_w, target_h)
        elif self.settings.auto_resolution:
            # _detect_best_resolution already handles orientation detection
            target_w, target_h = self._detect_best_resolution(clips)
        else:
            config = get_config()
            target_w, target_h = config.output.resolution_tuple
            logger.info(f"Using config resolution {target_w}x{target_h}")
            # Swap for config resolution if clips are portrait
            target_w, target_h = self._swap_if_portrait(clips, target_w, target_h)

        return target_w, target_h

    def _swap_if_portrait(
        self,
        clips: list[AssemblyClip],
        target_w: int,
        target_h: int,
    ) -> tuple[int, int]:
        """Swap width/height if majority of clips are portrait and target is landscape."""
        portrait_count = 0
        for clip in clips:
            res = self._get_video_resolution(clip.path)
            if res and res[1] > res[0]:
                portrait_count += 1
        if portrait_count > len(clips) // 2 and target_w > target_h:
            target_w, target_h = target_h, target_w
            logger.info(f"Detected portrait orientation, swapping to {target_w}x{target_h}")
        return target_w, target_h

    def _create_assembly_context(
        self,
        clips: list[AssemblyClip],
        target_w: int | None = None,
        target_h: int | None = None,
    ) -> AssemblyContext:
        """Create an AssemblyContext with resolved HDR, pixel format, and colorspace.

        Args:
            clips: List of clips to analyze for HDR type.
            target_w: Pre-resolved target width (if None, resolves from clips).
            target_h: Pre-resolved target height (if None, resolves from clips).

        Returns:
            Fully populated AssemblyContext.
        """
        if target_w is None or target_h is None:
            target_w, target_h = self._resolve_target_resolution(clips)

        # Pixel format
        if self.settings.preserve_hdr:
            pix_fmt = "p010le" if sys.platform == "darwin" else "yuv420p10le"
        else:
            pix_fmt = "yuv420p"

        target_fps = 60

        # HDR type detection
        hdr_type = _get_dominant_hdr_type(clips) if self.settings.preserve_hdr else "hlg"

        # Per-clip HDR types and color primaries
        clip_hdr_types = (
            _get_clip_hdr_types(clips) if self.settings.preserve_hdr else [None] * len(clips)
        )
        # Detect per-clip color primaries for accurate SDR→HDR conversion
        clip_primaries: list[str | None] = []
        if self.settings.preserve_hdr:
            for clip in clips:
                clip_primaries.append(_detect_color_primaries(clip.path))
        else:
            clip_primaries = [None] * len(clips)

        # Log mixed HDR content warning
        unique_types = {t for t in clip_hdr_types if t is not None}
        if len(unique_types) > 1:
            logger.warning(
                f"Mixed HDR content detected: {unique_types} - converting all to {hdr_type.upper()}"
            )

        # Colorspace filter
        colorspace_filter = _get_colorspace_filter(hdr_type) if self.settings.preserve_hdr else ""

        return AssemblyContext(
            target_w=target_w,
            target_h=target_h,
            pix_fmt=pix_fmt,
            hdr_type=hdr_type,
            clip_hdr_types=clip_hdr_types,
            clip_primaries=clip_primaries,
            colorspace_filter=colorspace_filter,
            target_fps=target_fps,
            fade_duration=self.settings.transition_duration,
        )

    def _build_clip_video_filter(
        self,
        i: int,
        clip: AssemblyClip,
        ctx: AssemblyContext,
        output_suffix: str = "scaled",
        use_aspect_ratio_handling: bool = True,
    ) -> str:
        """Build the video filter chain for a single clip.

        Handles rotation, HDR conversion, aspect ratio (blur/smart zoom),
        and standard scale+pad.

        Args:
            i: Clip index (for labeling).
            clip: The clip to build filters for.
            ctx: Assembly context with target resolution, HDR settings, etc.
            output_suffix: Suffix for the output label (default "scaled").
            use_aspect_ratio_handling: If True, apply smart zoom/blur for AR mismatch.

        Returns:
            FFmpeg filter string for this clip.
        """
        # Rotation override
        rotation_filter = ""
        if clip.rotation_override is not None and clip.rotation_override != 0:
            rotation_filter = _get_rotation_filter(clip.rotation_override) + ","
            logger.info(f"Applying {clip.rotation_override}° rotation to clip {i}")

        # HDR conversion filter (uses actual source primaries for accurate conversion)
        hdr_conversion = ""
        if self.settings.preserve_hdr and ctx.clip_hdr_types[i] != ctx.hdr_type:
            source_pri = ctx.clip_primaries[i] if i < len(ctx.clip_primaries) else None
            hdr_conversion = _get_hdr_conversion_filter(
                ctx.clip_hdr_types[i], ctx.hdr_type, source_primaries=source_pri
            )
            if hdr_conversion:
                logger.info(
                    f"Converting clip {i} from {ctx.clip_hdr_types[i]} "
                    f"(primaries={source_pri}) to {ctx.hdr_type}"
                )

        # Check for aspect ratio handling
        if use_aspect_ratio_handling and not clip.is_title_screen:
            clip_res = self._get_video_resolution(clip.path)
            if clip_res:
                src_w, src_h = clip_res
                src_ar = src_w / src_h
                target_ar = ctx.target_w / ctx.target_h
                ar_diff = abs(src_ar - target_ar) / max(src_ar, target_ar)

                if ar_diff > 0.05:
                    face_center = self._get_face_center(clip.path)
                    if face_center:
                        logger.info(
                            f"Clip {i}: Using smart crop centered on face at ({face_center[0]:.2f}, {face_center[1]:.2f})"
                        )
                    else:
                        logger.info(f"Clip {i}: Using blur background (no faces detected)")

                    return _get_aspect_ratio_filter(
                        clip_index=i,
                        src_w=src_w,
                        src_h=src_h,
                        target_w=ctx.target_w,
                        target_h=ctx.target_h,
                        face_center=face_center,
                        pix_fmt=ctx.pix_fmt,
                        target_fps=ctx.target_fps,
                        rotation_filter=rotation_filter,
                        hdr_conversion=hdr_conversion,
                        colorspace_filter=ctx.colorspace_filter,
                        output_suffix=output_suffix,
                    )

        # Standard filter: scale + pad
        return (
            f"[{i}:v]{rotation_filter}setpts=PTS-STARTPTS,"
            f"scale={ctx.target_w}:{ctx.target_h}:"
            f"force_original_aspect_ratio=decrease:flags=lanczos,"
            f"pad={ctx.target_w}:{ctx.target_h}:(ow-iw)/2:(oh-ih)/2:black,"
            f"fps={ctx.target_fps},settb=1/{ctx.target_fps},"
            f"format={ctx.pix_fmt}{hdr_conversion}{ctx.colorspace_filter},setsar=1[v{i}{output_suffix}]"
        )

    def _build_audio_prep_filters(
        self,
        clips: list[AssemblyClip],
        use_amix_fallback: bool = True,
    ) -> tuple[list[str], list[str]]:
        """Build audio preparation filter parts and labels for all clips.

        Args:
            clips: List of clips.
            use_amix_fallback: If True, use amix with anullsrc as fallback
                for regular clips. If False, use simpler aresample+apad.

        Returns:
            Tuple of (filter_parts, audio_labels) where filter_parts is a list
            of filter strings and audio_labels is ["[a0prep]", "[a1prep]", ...].
        """
        audio_format = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
        filter_parts: list[str] = []
        audio_labels: list[str] = []

        for i, clip in enumerate(clips):
            if clip.is_title_screen:
                filter_parts.append(
                    f"anullsrc=r=48000:cl=stereo,atrim=0:{clip.duration},{audio_format}[a{i}prep]"
                )
            elif use_amix_fallback:
                filter_parts.append(
                    f"anullsrc=r=48000:cl=stereo,atrim=0:{clip.duration}[a{i}silence];"
                    f"[{i}:a]{audio_format},asetpts=PTS-STARTPTS[a{i}src];"
                    f"[a{i}silence][a{i}src]amix=inputs=2:duration=first:weights='0 1'[a{i}mixed];"
                    f"[a{i}mixed]atrim=0:{clip.duration},asetpts=PTS-STARTPTS[a{i}prep]"
                )
            else:
                filter_parts.append(
                    f"[{i}:a]{audio_format},aresample=async=1,asetpts=PTS-STARTPTS,"
                    f"apad=whole_dur={clip.duration},atrim=0:{clip.duration}[a{i}prep]"
                )
            audio_labels.append(f"[a{i}prep]")

        return filter_parts, audio_labels

    def _build_xfade_chain(
        self,
        clips: list[AssemblyClip],
        ctx: AssemblyContext,
        audio_labels: list[str],
    ) -> tuple[list[str], str, str, float]:
        """Build video xfade and audio crossfade chains.

        Args:
            clips: List of clips.
            ctx: Assembly context.
            audio_labels: Audio labels from _build_audio_prep_filters.

        Returns:
            Tuple of (filter_parts, final_video_label, final_audio_label, total_duration).
        """
        filter_parts: list[str] = []
        current_video = "[v0scaled]"
        current_audio = audio_labels[0]
        total_duration = 0.0

        for i, clip in enumerate(clips[:-1]):
            next_idx = i + 1
            next_clip = clips[next_idx]
            video_label = f"[v{i}{next_idx}]"
            audio_label = f"[a{i}{next_idx}]"

            offset = total_duration + clip.duration - ctx.fade_duration

            # Video xfade
            filter_parts.append(
                f"{current_video}[v{next_idx}scaled]xfade=transition=fade:"
                f"duration={ctx.fade_duration}:offset={offset},settb=1/{ctx.target_fps}{video_label}"
            )

            # Audio crossfade with title screen handling
            if next_clip.is_title_screen:
                fast_fade = ctx.fade_duration / 2
                filter_parts.append(
                    f"{current_audio}afade=t=out:st={clip.duration - fast_fade}:d={fast_fade}[a{i}faded];"
                    f"[a{i}faded]{audio_labels[next_idx]}acrossfade=d={ctx.fade_duration}:c1=tri:c2=tri{audio_label}"
                )
            elif clip.is_title_screen:
                fast_fade = ctx.fade_duration / 2
                filter_parts.append(
                    f"{current_audio}{audio_labels[next_idx]}acrossfade=d={ctx.fade_duration}:c1=tri:c2=tri[a{i}xf];"
                    f"[a{i}xf]afade=t=in:st=0:d={fast_fade}{audio_label}"
                )
            else:
                filter_parts.append(
                    f"{current_audio}{audio_labels[next_idx]}acrossfade=d={ctx.fade_duration}:c1=tri:c2=tri{audio_label}"
                )

            current_video = video_label
            current_audio = audio_label
            total_duration = offset

        return filter_parts, current_video, current_audio, total_duration

    def _build_smart_transition_chain(
        self,
        clips: list[AssemblyClip],
        transitions: list[str],
        ctx: AssemblyContext,
        audio_labels: list[str],
    ) -> tuple[list[str], str, str]:
        """Build transition chain with a mix of xfade and concat (for smart transitions).

        Args:
            clips: List of clips.
            transitions: List of "fade" or "cut" for each transition.
            ctx: Assembly context.
            audio_labels: Audio labels from _build_audio_prep_filters.

        Returns:
            Tuple of (filter_parts, final_video_label, final_audio_label).
        """
        filter_parts: list[str] = []
        current_video = "[v0scaled]"
        current_audio = "[a0prep]"
        cumulative_duration = 0.0

        for i, (clip, transition) in enumerate(zip(clips[:-1], transitions, strict=False)):
            next_idx = i + 1
            video_label = f"[v{i}_{next_idx}]"
            audio_label = f"[a{i}_{next_idx}]"

            if transition == "fade":
                offset = cumulative_duration + clip.duration - ctx.fade_duration
                filter_parts.append(
                    f"{current_video}[v{next_idx}scaled]xfade=transition=fade:"
                    f"duration={ctx.fade_duration}:offset={offset},settb=1/{ctx.target_fps}{video_label}"
                )
                filter_parts.append(
                    f"{current_audio}[a{next_idx}prep]acrossfade=d={ctx.fade_duration},asetpts=PTS-STARTPTS{audio_label}"
                )
                cumulative_duration = offset
            else:
                filter_parts.append(
                    f"{current_video}[v{next_idx}scaled]concat=n=2:v=1:a=0,settb=1/{ctx.target_fps}{video_label}"
                )
                filter_parts.append(
                    f"{current_audio}[a{next_idx}prep]concat=n=2:v=0:a=1,asetpts=PTS-STARTPTS{audio_label}"
                )
                cumulative_duration += clip.duration

            current_video = video_label
            current_audio = audio_label

        return filter_parts, current_video, current_audio

    def _run_ffmpeg_assembly(
        self,
        inputs: list[str],
        filter_complex: str,
        video_label: str,
        audio_label: str,
        output_path: Path,
        clips: list[AssemblyClip],
        ctx: AssemblyContext,
        progress_callback: Callable[[float, str], None] | None = None,
    ) -> subprocess.CompletedProcess:
        """Build and run the FFmpeg assembly command.

        Args:
            inputs: List of input arguments (e.g., ["-i", "path1", "-i", "path2"]).
            filter_complex: The complete filter_complex string.
            video_label: Final video output label (e.g., "[vout]" or "[v23]").
            audio_label: Final audio output label.
            output_path: Output file path.
            clips: List of clips (for duration estimation).
            ctx: Assembly context.
            progress_callback: Optional progress callback.

        Returns:
            The CompletedProcess result from FFmpeg.
        """
        video_codec_args = _get_gpu_encoder_args(
            crf=self.settings.output_crf,
            preserve_hdr=self.settings.preserve_hdr,
            hdr_type=ctx.hdr_type,
        )
        if self.settings.preserve_hdr:
            logger.info(f"Using GPU-accelerated HEVC with {ctx.hdr_type.upper()} HDR preservation")
        else:
            logger.info("Using GPU-accelerated encoding")

        framerate_args = ["-r", str(ctx.target_fps)]
        logger.info(f"Output frame rate: {ctx.target_fps}fps")

        cmd = [
            "ffmpeg",
            "-y",
            *inputs,
            "-filter_complex",
            filter_complex,
            "-map",
            video_label,
            "-map",
            audio_label,
            *video_codec_args,
            *framerate_args,
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-threads",
            "4",
            "-filter_complex_threads",
            "1",
            "-max_muxing_queue_size",
            "1024",
            "-movflags",
            "+faststart",
            str(output_path),
        ]

        total_duration = self.estimate_duration(clips)
        logger.debug(f"Running assembly: {' '.join(cmd)}")
        return _run_ffmpeg_with_progress(cmd, total_duration, progress_callback)

    def _log_ffmpeg_error(self, result: subprocess.CompletedProcess) -> str:
        """Extract and return a useful error message from FFmpeg stderr.

        Args:
            result: FFmpeg CompletedProcess.

        Returns:
            Error message string (last 1000 chars or filtered error lines).
        """
        stderr_lines = result.stderr.split("\n")
        error_lines = [
            line
            for line in stderr_lines
            if "error" in line.lower() or "Error" in line or "invalid" in line.lower()
        ]
        if error_lines:
            return "\n".join(error_lines[-10:])
        return result.stderr[-2000:] if len(result.stderr) > 2000 else result.stderr
