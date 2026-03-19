"""Filter chain construction for FFmpeg video assembly.

Builds FFmpeg filter_complex strings for scaling, HDR conversion,
audio normalization, xfade chains, and smart transition chains.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from immich_memories.processing.assembly_config import (
    AssemblyClip,
    AssemblySettings,
    _get_rotation_filter,
)
from immich_memories.processing.ffmpeg_prober import FFmpegProber
from immich_memories.processing.ffmpeg_runner import AssemblyContext
from immich_memories.processing.hdr_utilities import (
    _get_hdr_conversion_filter,
)
from immich_memories.processing.scaling_utilities import (
    _get_aspect_ratio_filter,
)

logger = logging.getLogger(__name__)


class FilterBuilder:
    """Builds FFmpeg filter chains for video assembly."""

    def __init__(
        self,
        settings: AssemblySettings,
        prober: FFmpegProber,
        face_center_fn: Callable[[Path], tuple[float, float] | None],
    ) -> None:
        self.settings = settings
        self.prober = prober
        self.face_center_fn = face_center_fn

    def _build_rotation_prefix(self, i: int, clip: AssemblyClip) -> str:
        """Build rotation and privacy blur filter prefix."""
        rotation_filter = ""
        if clip.rotation_override is not None and clip.rotation_override != 0:
            rotation_filter = _get_rotation_filter(clip.rotation_override) + ","
            logger.info(f"Applying {clip.rotation_override} rotation to clip {i}")
        if self.settings.privacy_mode and not clip.is_title_screen:
            rotation_filter += "gblur=sigma=30,"
        return rotation_filter

    def _build_hdr_conversion(self, i: int, ctx: AssemblyContext) -> str:
        """Build HDR conversion filter for a clip if needed."""
        if not self.settings.preserve_hdr or ctx.clip_hdr_types[i] == ctx.hdr_type:
            return ""
        source_pri = ctx.clip_primaries[i] if i < len(ctx.clip_primaries) else None
        hdr_conversion = _get_hdr_conversion_filter(
            ctx.clip_hdr_types[i], ctx.hdr_type, source_primaries=source_pri
        )
        if hdr_conversion:
            logger.info(
                f"Converting clip {i} from {ctx.clip_hdr_types[i]} "
                f"(primaries={source_pri}) to {ctx.hdr_type}"
            )
        return hdr_conversion

    def _try_aspect_ratio_filter(
        self,
        i: int,
        clip: AssemblyClip,
        ctx: AssemblyContext,
        rotation_filter: str,
        hdr_conversion: str,
        output_suffix: str,
    ) -> str | None:
        """Try to build an aspect-ratio-aware filter; returns None if not needed."""
        clip_res = self.prober.get_video_resolution(clip.path)
        if not clip_res:
            return None
        src_w, src_h = clip_res
        ar_diff = abs(src_w / src_h - ctx.target_w / ctx.target_h) / max(
            src_w / src_h, ctx.target_w / ctx.target_h
        )
        if ar_diff <= 0.05:
            return None
        face_center = self.face_center_fn(clip.path)
        if face_center:
            logger.info(f"Clip {i}: Smart crop at ({face_center[0]:.2f}, {face_center[1]:.2f})")
        else:
            logger.info(f"Clip {i}: Blur background (no faces detected)")
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

    def build_clip_video_filter(
        self,
        i: int,
        clip: AssemblyClip,
        ctx: AssemblyContext,
        output_suffix: str = "scaled",
        use_aspect_ratio_handling: bool = True,
    ) -> str:
        """Build the video filter chain for a single clip."""
        rotation_filter = self._build_rotation_prefix(i, clip)
        hdr_conversion = self._build_hdr_conversion(i, ctx)

        if use_aspect_ratio_handling and not clip.is_title_screen:
            ar_filter = self._try_aspect_ratio_filter(
                i, clip, ctx, rotation_filter, hdr_conversion, output_suffix
            )
            if ar_filter is not None:
                return ar_filter

        return (
            f"[{i}:v]{rotation_filter}setpts=PTS-STARTPTS,"
            f"scale={ctx.target_w}:{ctx.target_h}:"
            f"force_original_aspect_ratio=decrease:flags=lanczos,"
            f"pad={ctx.target_w}:{ctx.target_h}:(ow-iw)/2:(oh-ih)/2:black,"
            f"fps={ctx.target_fps},settb=1/{ctx.target_fps},"
            f"format={ctx.pix_fmt}{hdr_conversion}{ctx.colorspace_filter},"
            f"setsar=1[v{i}{output_suffix}]"
        )

    def build_audio_prep_filters(
        self,
        clips: list[AssemblyClip],
        use_amix_fallback: bool = True,
    ) -> tuple[list[str], list[str]]:
        """Build audio preparation filter parts and labels for all clips."""
        audio_format = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
        loudnorm = ",loudnorm=I=-16:TP=-1.5:LRA=11" if self.settings.normalize_clip_audio else ""
        filter_parts: list[str] = []
        audio_labels: list[str] = []

        for i, clip in enumerate(clips):
            clip_loudnorm = loudnorm if not clip.is_title_screen else ""
            if clip.is_title_screen or (self.settings.privacy_mode and clip.has_speech):
                filter_parts.append(
                    f"anullsrc=r=48000:cl=stereo,atrim=0:{clip.duration},{audio_format}[a{i}prep]"
                )
            elif use_amix_fallback:
                filter_parts.append(
                    f"anullsrc=r=48000:cl=stereo,atrim=0:{clip.duration}[a{i}silence];"
                    f"[{i}:a]{audio_format},asetpts=PTS-STARTPTS{clip_loudnorm}[a{i}src];"
                    f"[a{i}silence][a{i}src]amix=inputs=2:duration=first:weights='0 1'[a{i}mixed];"
                    f"[a{i}mixed]atrim=0:{clip.duration},asetpts=PTS-STARTPTS[a{i}prep]"
                )
            else:
                filter_parts.append(
                    f"[{i}:a]{audio_format},aresample=async=1,asetpts=PTS-STARTPTS{clip_loudnorm},"
                    f"apad=whole_dur={clip.duration},atrim=0:{clip.duration}[a{i}prep]"
                )
            audio_labels.append(f"[a{i}prep]")

        return filter_parts, audio_labels

    def build_xfade_chain(
        self,
        clips: list[AssemblyClip],
        ctx: AssemblyContext,
        audio_labels: list[str],
    ) -> tuple[list[str], str, str, float]:
        """Build video xfade and audio crossfade chains."""
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

            filter_parts.append(
                f"{current_video}[v{next_idx}scaled]xfade=transition=fade:"
                f"duration={ctx.fade_duration}:offset={offset},settb=1/{ctx.target_fps}{video_label}"
            )

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

    def build_smart_transition_chain(
        self,
        clips: list[AssemblyClip],
        transitions: list[str],
        ctx: AssemblyContext,
        audio_labels: list[str],
    ) -> tuple[list[str], str, str]:
        """Build transition chain with a mix of xfade and concat."""
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
                filter_parts.extend(
                    (
                        f"{current_video}[v{next_idx}scaled]xfade=transition=fade:"
                        f"duration={ctx.fade_duration}:offset={offset},settb=1/{ctx.target_fps}{video_label}",
                        f"{current_audio}[a{next_idx}prep]acrossfade=d={ctx.fade_duration},asetpts=PTS-STARTPTS{audio_label}",
                    )
                )
                cumulative_duration = offset
            else:
                filter_parts.extend(
                    (
                        f"{current_video}[v{next_idx}scaled]concat=n=2:v=1:a=0,settb=1/{ctx.target_fps}{video_label}",
                        f"{current_audio}[a{next_idx}prep]concat=n=2:v=0:a=1,asetpts=PTS-STARTPTS{audio_label}",
                    )
                )
                cumulative_duration += clip.duration

            current_video = video_label
            current_audio = audio_label

        return filter_parts, current_video, current_audio

    def build_probed_audio_filters(
        self,
        batches: list[AssemblyClip],
        audio_durations: list[float],
    ) -> tuple[list[str], list[str]]:
        """Build audio prep filters using probed durations."""
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

    def build_probed_xfade_chain(
        self,
        batches: list[AssemblyClip],
        video_durations: list[float],
        fade_duration: float,
        target_fps: int,
        audio_labels: list[str],
    ) -> tuple[list[str], str, str, float]:
        """Build xfade chain using probed video durations for offsets."""
        filter_parts: list[str] = []
        current_video = "[v0scaled]"
        current_audio = audio_labels[0]
        video_offset = 0.0

        for i in range(len(batches) - 1):
            next_idx = i + 1
            video_label = f"[v{i}{next_idx}]"
            audio_label = f"[a{i}{next_idx}]"

            offset = video_offset + video_durations[i] - fade_duration

            filter_parts.extend(
                (
                    f"{current_video}[v{next_idx}scaled]xfade=transition=fade:"
                    f"duration={fade_duration}:offset={offset},settb=1/{target_fps}{video_label}",
                    f"{current_audio}{audio_labels[next_idx]}acrossfade=d={fade_duration}:c1=tri:c2=tri{audio_label}",
                )
            )

            current_video = video_label
            current_audio = audio_label
            video_offset = offset

        return filter_parts, current_video, current_audio, video_offset

    def get_clip_hdr_conversion(self, i: int, ctx: AssemblyContext) -> str:
        """Return HDR conversion filter string for clip i, or empty string."""
        if not self.settings.preserve_hdr or ctx.clip_hdr_types[i] == ctx.hdr_type:
            return ""
        source_pri = ctx.clip_primaries[i] if i < len(ctx.clip_primaries) else None
        hdr_conversion = _get_hdr_conversion_filter(
            ctx.clip_hdr_types[i], ctx.hdr_type, source_primaries=source_pri
        )
        if hdr_conversion:
            logger.info(
                f"Converting clip {i} from {ctx.clip_hdr_types[i]} "
                f"(primaries={source_pri}) to {ctx.hdr_type}"
            )
        return hdr_conversion
