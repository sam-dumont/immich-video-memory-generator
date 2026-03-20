"""Core assembly engine for multi-clip video assembly.

Orchestrates scalable, strategy-based, and chunked assembly pipelines.
Includes assembly context building (resolution, HDR, colorspace resolution).
Concat/xfade/batch operations are in ffmpeg_filter_graph.py.
"""

from __future__ import annotations

import logging
import math
import shutil
import sys
from collections.abc import Callable
from pathlib import Path

from immich_memories.processing.assembly_config import (
    CHUNK_SIZE,
    CHUNKED_ASSEMBLY_THRESHOLD,
    AssemblyClip,
    AssemblySettings,
    TransitionType,
)
from immich_memories.processing.clip_encoder import ClipEncoder
from immich_memories.processing.ffmpeg_filter_graph import ConcatService
from immich_memories.processing.ffmpeg_prober import FFmpegProber
from immich_memories.processing.ffmpeg_runner import AssemblyContext
from immich_memories.processing.filter_builder import FilterBuilder
from immich_memories.processing.hdr_utilities import (
    _detect_color_primaries,
    _get_clip_hdr_types,
    _get_colorspace_filter,
    _get_dominant_hdr_type,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Assembly context building
# ---------------------------------------------------------------------------


def resolve_target_resolution(
    settings: AssemblySettings,
    prober: FFmpegProber,
    clips: list[AssemblyClip],
) -> tuple[int, int]:
    """Resolve target resolution from settings, auto-detection, or config default."""
    if settings.target_resolution:
        target_w, target_h = settings.target_resolution
        logger.info(f"Using specified resolution {target_w}x{target_h}")
        target_w, target_h = _swap_if_portrait(prober, clips, target_w, target_h)
    elif settings.auto_resolution:
        target_w, target_h = prober.detect_best_resolution(clips)
    elif settings.default_resolution:
        target_w, target_h = settings.default_resolution
        logger.info(f"Using config resolution {target_w}x{target_h}")
        target_w, target_h = _swap_if_portrait(prober, clips, target_w, target_h)
    else:
        raise ValueError(
            "No resolution configured: set target_resolution, enable auto_resolution, "
            "or provide default_resolution on AssemblySettings"
        )
    return target_w, target_h


def _swap_if_portrait(
    prober: FFmpegProber,
    clips: list[AssemblyClip],
    target_w: int,
    target_h: int,
) -> tuple[int, int]:
    """Swap width/height if majority of clips are portrait."""
    portrait = sum(bool((r := prober.get_video_resolution(c.path)) and r[1] > r[0]) for c in clips)
    if portrait > len(clips) // 2 and target_w > target_h:
        target_w, target_h = target_h, target_w
        logger.info(f"Detected portrait orientation, swapping to {target_w}x{target_h}")
    return target_w, target_h


def create_assembly_context(
    settings: AssemblySettings,
    prober: FFmpegProber,
    clips: list[AssemblyClip],
    target_w: int | None = None,
    target_h: int | None = None,
) -> AssemblyContext:
    """Create an AssemblyContext with resolved HDR, pixel format, and colorspace."""
    if target_w is None or target_h is None:
        target_w, target_h = resolve_target_resolution(settings, prober, clips)

    pix_fmt = (
        ("p010le" if sys.platform == "darwin" else "yuv420p10le")
        if settings.preserve_hdr
        else "yuv420p"
    )
    target_fps = prober.detect_max_framerate(clips)
    hdr_type = _get_dominant_hdr_type(clips) if settings.preserve_hdr else "hlg"

    clip_hdr_types = _get_clip_hdr_types(clips) if settings.preserve_hdr else [None] * len(clips)
    clip_primaries: list[str | None] = []
    if settings.preserve_hdr:
        for clip in clips:
            clip_primaries.append(_detect_color_primaries(clip.path))
    else:
        clip_primaries = [None] * len(clips)

    unique_types = {t for t in clip_hdr_types if t is not None}
    if len(unique_types) > 1:
        logger.warning(
            f"Mixed HDR content detected: {unique_types} - converting all to {hdr_type.upper()}"
        )

    colorspace_filter = _get_colorspace_filter(hdr_type) if settings.preserve_hdr else ""

    return AssemblyContext(
        target_w=target_w,
        target_h=target_h,
        pix_fmt=pix_fmt,
        hdr_type=hdr_type,
        clip_hdr_types=clip_hdr_types,
        clip_primaries=clip_primaries,
        colorspace_filter=colorspace_filter,
        target_fps=target_fps,
        fade_duration=settings.transition_duration or 0.5,
    )


# ---------------------------------------------------------------------------
# Assembly engine
# ---------------------------------------------------------------------------


def _pick_transition(
    clip_before: AssemblyClip,
    clip_after: AssemblyClip,
    consecutive_fades: int,
    consecutive_cuts: int,
) -> tuple[str, int, int]:
    """Pick a single transition type for one clip boundary."""
    import random

    if clip_before.is_title_screen or clip_after.is_title_screen:
        return "fade", consecutive_fades + 1, 0
    if clip_before.outgoing_transition is not None:
        t = clip_before.outgoing_transition
        if t == "fade":
            return t, consecutive_fades + 1, 0
        return t, 0, consecutive_cuts + 1
    use_fade = random.random() < 0.7
    if consecutive_fades >= 3:
        use_fade = False
    if consecutive_cuts >= 2:
        use_fade = True
    if use_fade:
        return "fade", consecutive_fades + 1, 0
    return "cut", 0, consecutive_cuts + 1


class AssemblyEngine:
    """Orchestrates multi-clip video assembly with transitions."""

    def __init__(
        self,
        settings: AssemblySettings,
        prober: FFmpegProber,
        encoder: ClipEncoder,
        filter_builder: FilterBuilder,
        check_cancelled_fn: Callable[[], None],
    ) -> None:
        self.settings = settings
        self.prober = prober
        self.encoder = encoder
        self.filter_builder = filter_builder
        self.check_cancelled_fn = check_cancelled_fn
        self.concat = ConcatService(settings, prober, encoder, filter_builder)

    def assemble_scalable(
        self,
        clips: list[AssemblyClip],
        output_path: Path,
        progress_callback: Callable[[float, str], None] | None = None,
    ) -> Path:
        """Assemble clips using single-encode filter graph from source files.

        Builds one FFmpeg command that reads source clips, normalizes them
        (scale, fps, HDR, aspect ratio) and applies transitions — all in a
        single encode pass.  For large clip counts (> CHUNKED_ASSEMBLY_THRESHOLD),
        falls back to chunked assembly where each chunk is still single-encode.
        """
        if len(clips) < 2:
            if len(clips) == 1:
                return self._assemble_single_clip(clips[0], output_path)
            raise ValueError("No clips to assemble")

        # Resolve target resolution ONCE for all clips — prevents each chunk
        # from auto-detecting a different resolution/orientation
        target_w, target_h = resolve_target_resolution(self.settings, self.prober, clips)
        saved_res = self.settings.target_resolution
        saved_auto = self.settings.auto_resolution
        self.settings.target_resolution = (target_w, target_h)
        self.settings.auto_resolution = False
        try:
            return self._assemble_scalable_inner(
                clips, output_path, progress_callback, target_w, target_h
            )
        finally:
            self.settings.target_resolution = saved_res
            self.settings.auto_resolution = saved_auto

    def _assemble_scalable_inner(
        self,
        clips: list[AssemblyClip],
        output_path: Path,
        progress_callback: Callable[[float, str], None] | None,
        target_w: int,
        target_h: int,
    ) -> Path:
        if len(clips) > CHUNKED_ASSEMBLY_THRESHOLD:
            return self.assemble_chunked(clips, output_path, progress_callback)

        transitions = self.get_transition_types(clips)
        transitions = self._validate_fade_transitions(
            transitions, [c.duration for c in clips], self.settings.transition_duration or 0.5
        )

        ctx = create_assembly_context(self.settings, self.prober, clips, target_w, target_h)
        logger.info(f"Single-encode assembly: {len(clips)} clips at {target_w}x{target_h}")

        inputs: list[str] = []
        for clip in clips:
            inputs.extend(["-i", str(clip.path)])

        filter_parts: list[str] = [
            self.filter_builder.build_clip_video_filter(i, clip, ctx)
            for i, clip in enumerate(clips)
        ]

        audio_filter_parts, audio_labels = self.filter_builder.build_audio_prep_filters(
            clips, use_amix_fallback=False
        )
        filter_parts.extend(audio_filter_parts)

        transition_parts, final_video, final_audio = (
            self.filter_builder.build_smart_transition_chain(clips, transitions, ctx, audio_labels)
        )
        filter_parts.extend(transition_parts)

        filter_complex = ";".join(filter_parts)

        if progress_callback:
            progress_callback(0.1, "Assembling video...")

        result = self.encoder.run_ffmpeg_assembly(
            inputs,
            filter_complex,
            final_video,
            final_audio,
            output_path,
            clips,
            ctx,
            progress_callback,
        )

        if result.returncode != 0:
            logger.warning(
                f"Single-encode assembly failed (code {result.returncode}), "
                "falling back to chunked assembly."
            )
            return self.assemble_chunked(clips, output_path, progress_callback)

        return output_path

    def _assemble_single_clip(self, clip: AssemblyClip, output_path: Path) -> Path:
        """Handle single clip: encode through FFmpeg if filters needed, else copy."""
        needs_encoding = (
            self.settings.privacy_mode
            or (not self.settings.auto_resolution and self.settings.target_resolution)
            or (clip.rotation_override is not None and clip.rotation_override != 0)
        )
        if needs_encoding:
            target_resolution = resolve_target_resolution(self.settings, self.prober, [clip])
            self.encoder.encode_single_clip(clip, output_path, target_resolution=target_resolution)
            return output_path
        shutil.copy2(clip.path, output_path)
        return output_path

    def assemble_with_cuts(
        self,
        clips: list[AssemblyClip],
        output_path: Path,
        progress_callback: Callable[[float, str], None] | None = None,
    ) -> Path:
        """Assemble clips with hard cuts."""
        if not clips:
            raise ValueError("No clips to assemble")
        target_w, target_h = resolve_target_resolution(self.settings, self.prober, clips)
        ctx = create_assembly_context(self.settings, self.prober, clips, target_w, target_h)
        out_fps = self.settings.target_framerate or self.prober.detect_max_framerate(clips)
        logger.info(f"Cuts assembly: {len(clips)} clips, {target_w}x{target_h} @ {out_fps}fps")
        input_args: list[str] = []
        for clip in clips:
            input_args.extend(["-i", str(clip.path)])
        filter_parts = []
        for i in range(len(clips)):
            hdr_conversion = self.filter_builder.get_clip_hdr_conversion(i, ctx)
            filter_parts.append(
                f"[{i}:v]setpts=PTS-STARTPTS,"
                f"scale={target_w}:{target_h}:"
                f"force_original_aspect_ratio=decrease:flags=lanczos,"
                f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:black,"
                f"fps={out_fps},settb=1/{out_fps},"
                f"format={ctx.pix_fmt}{hdr_conversion}{ctx.colorspace_filter},"
                f"setsar=1[v{i}]"
            )
        video_inputs = "".join(f"[v{i}]" for i in range(len(clips)))
        audio_inputs = "".join(f"[{i}:a]" for i in range(len(clips)))
        filter_parts.extend(
            (
                f"{video_inputs}concat=n={len(clips)}:v=1:a=0[vout]",
                f"{audio_inputs}concat=n={len(clips)}:v=0:a=1[aout]",
            )
        )
        filter_complex = ";".join(filter_parts)
        result = self.encoder.run_ffmpeg_assembly(
            input_args,
            filter_complex,
            "[vout]",
            "[aout]",
            output_path,
            clips,
            ctx,
            progress_callback,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to assemble video with cuts: {result.stderr}")
        return output_path

    def assemble_with_crossfade(
        self,
        clips: list[AssemblyClip],
        output_path: Path,
        progress_callback: Callable[[float, str], None] | None = None,
    ) -> Path:
        """Assemble clips with crossfade transitions."""
        if len(clips) < 2:
            raise ValueError("Need at least 2 clips for crossfade")
        if len(clips) > CHUNKED_ASSEMBLY_THRESHOLD:
            return self.assemble_chunked(clips, output_path, progress_callback)
        target_w, target_h = resolve_target_resolution(self.settings, self.prober, clips)
        ctx = create_assembly_context(self.settings, self.prober, clips, target_w, target_h)
        inputs: list[str] = []
        for clip in clips:
            inputs.extend(["-i", str(clip.path)])
        filter_parts = [
            self.filter_builder.build_clip_video_filter(i, clip, ctx)
            for i, clip in enumerate(clips)
        ]
        audio_filter_parts, audio_labels = self.filter_builder.build_audio_prep_filters(clips)
        filter_parts.extend(audio_filter_parts)
        xfade_parts, final_video, final_audio, _ = self.filter_builder.build_xfade_chain(
            clips,
            ctx,
            audio_labels,
        )
        filter_parts.extend(xfade_parts)
        filter_complex = ";".join(filter_parts)
        result = self.encoder.run_ffmpeg_assembly(
            inputs,
            filter_complex,
            final_video,
            final_audio,
            output_path,
            clips,
            ctx,
            progress_callback,
        )
        if result.returncode != 0:
            logger.warning(f"Crossfade failed (code {result.returncode}), falling back to cuts.")
            return self.assemble_with_cuts(clips, output_path, progress_callback)
        return output_path

    def assemble_with_smart_transitions(
        self,
        clips: list[AssemblyClip],
        output_path: Path,
        progress_callback: Callable[[float, str], None] | None = None,
    ) -> Path:
        """Assemble clips with a mix of crossfades and cuts."""
        if len(clips) < 2:
            raise ValueError("Need at least 2 clips for smart transitions")
        if len(clips) > CHUNKED_ASSEMBLY_THRESHOLD:
            return self.assemble_chunked(clips, output_path, progress_callback)
        transitions = self.decide_transitions(clips)
        target_w, target_h = resolve_target_resolution(self.settings, self.prober, clips)
        ctx = create_assembly_context(self.settings, self.prober, clips, target_w, target_h)
        inputs: list[str] = []
        for clip in clips:
            inputs.extend(["-i", str(clip.path)])
        filter_parts = [
            self.filter_builder.build_clip_video_filter(
                i, clip, ctx, use_aspect_ratio_handling=False
            )
            for i, clip in enumerate(clips)
        ]
        audio_filter_parts, audio_labels = self.filter_builder.build_audio_prep_filters(
            clips,
            use_amix_fallback=False,
        )
        filter_parts.extend(audio_filter_parts)
        transition_parts, final_video, final_audio = (
            self.filter_builder.build_smart_transition_chain(
                clips,
                transitions,
                ctx,
                audio_labels,
            )
        )
        filter_parts.extend(transition_parts)
        filter_complex = ";".join(filter_parts)
        result = self.encoder.run_ffmpeg_assembly(
            inputs,
            filter_complex,
            final_video,
            final_audio,
            output_path,
            clips,
            ctx,
            progress_callback,
        )
        if result.returncode != 0:
            logger.warning(
                f"Smart transitions failed (code {result.returncode}), falling back to crossfade."
            )
            return self.assemble_with_crossfade(clips, output_path, progress_callback)
        return output_path

    def decide_transitions(self, clips: list[AssemblyClip]) -> list[str]:
        """Decide which transition type to use between each pair of clips."""
        if len(clips) < 2:
            return []
        transitions = []
        consecutive_fades = 0
        consecutive_cuts = 0
        predecided_used = 0
        for i in range(len(clips) - 1):
            t, consecutive_fades, consecutive_cuts = _pick_transition(
                clips[i], clips[i + 1], consecutive_fades, consecutive_cuts
            )
            transitions.append(t)
            if clips[i].outgoing_transition is not None and not clips[i].is_title_screen:
                predecided_used += 1
        logger.info(
            f"Smart transitions: {transitions.count('fade')} crossfades, "
            f"{transitions.count('cut')} cuts"
            + (f" ({predecided_used} pre-decided)" if predecided_used > 0 else "")
        )
        return transitions

    def get_transition_types(self, clips: list[AssemblyClip]) -> list[str]:
        """Get the transition type for each clip boundary."""
        if self.settings.predecided_transitions:
            return self.settings.predecided_transitions
        transitions = []
        for i in range(len(clips) - 1):
            clip, next_clip = clips[i], clips[i + 1]
            if (
                clip.is_title_screen
                or next_clip.is_title_screen
                or self.settings.transition == TransitionType.CROSSFADE
            ):
                transitions.append("fade")
            elif self.settings.transition == TransitionType.CUT:
                transitions.append("cut")
            elif self.settings.transition == TransitionType.SMART:
                transitions.append(getattr(clip, "outgoing_transition", None) or "fade")
            else:
                transitions.append("cut")
        return transitions

    def _validate_fade_transitions(
        self,
        transitions: list[str],
        clip_durations: list[float],
        fade: float,
    ) -> list[str]:
        """Validate and downgrade fade transitions where clips are too short."""
        min_dur = fade * 2
        for i in range(len(transitions)):
            if transitions[i] == "fade":
                dur_a = clip_durations[i]
                dur_b = clip_durations[i + 1] if i + 1 < len(clip_durations) else 0
                if dur_a < min_dur or dur_b < min_dur:
                    logger.warning(
                        f"Transition {i}: Downgrading fade to cut - "
                        f"durations too short ({dur_a:.2f}s, {dur_b:.2f}s)"
                    )
                    transitions[i] = "cut"
        fades = sum(1 for t in transitions if t == "fade")
        cuts = sum(1 for t in transitions if t == "cut")
        logger.info(f"Transitions: {fades} fades, {cuts} cuts")
        return transitions

    def _make_batch_progress_cb(
        self,
        base: float,
        range_: float,
        idx: int,
        total: int,
        progress_callback: Callable[[float, str], None],
    ) -> Callable[[float, str], None]:
        def batch_cb(pct: float, msg: str) -> None:
            progress_callback(base + pct * range_, f"Batch {idx + 1}/{total}: {msg}")

        return batch_cb

    def _process_single_batch(
        self,
        batch: list[AssemblyClip],
        batch_idx: int,
        num_batches: int,
        intermediates_dir: Path,
        progress_callback: Callable[[float, str], None] | None,
    ) -> AssemblyClip:
        """Process one batch: encode or copy, return an AssemblyClip."""
        intermediate_path = intermediates_dir / f"batch_{batch_idx:03d}.mp4"
        if len(batch) == 1:
            shutil.copy2(batch[0].path, intermediate_path)
            batch_duration = batch[0].duration
        else:
            base = (batch_idx / num_batches) * 0.8
            range_ = (1 / num_batches) * 0.8
            cb = (
                self._make_batch_progress_cb(
                    base, range_, batch_idx, num_batches, progress_callback
                )
                if progress_callback
                else None
            )
            self.concat.assemble_batch_direct(batch, intermediate_path, cb)
            batch_duration = sum(c.duration for c in batch)
            batch_duration -= (self.settings.transition_duration or 0.5) * (len(batch) - 1)
        return AssemblyClip(
            path=intermediate_path,
            duration=batch_duration,
            date=None,
            asset_id=f"batch_{batch_idx}",
            is_title_screen=batch[-1].is_title_screen if batch else False,
        )

    def assemble_chunked(
        self,
        clips: list[AssemblyClip],
        output_path: Path,
        progress_callback: Callable[[float, str], None] | None = None,
    ) -> Path:
        """Assemble many clips using chunked processing.

        Skips failed batches instead of aborting — a single bad clip
        won't kill the whole video.
        """
        num_clips = len(clips)
        num_batches = math.ceil(num_clips / CHUNK_SIZE)
        logger.info(f"Chunked assembly: {num_clips} clips -> {num_batches} batches")
        intermediates_dir = output_path.parent / ".intermediates"
        intermediates_dir.mkdir(parents=True, exist_ok=True)
        intermediate_clips: list[AssemblyClip] = []
        failed_batches = 0
        for batch_idx in range(num_batches):
            start_idx = batch_idx * CHUNK_SIZE
            batch = clips[start_idx : min(start_idx + CHUNK_SIZE, num_clips)]
            if progress_callback:
                progress_callback(
                    (batch_idx / num_batches) * 0.8,
                    f"Processing batch {batch_idx + 1}/{num_batches} ({len(batch)} clips)...",
                )
            try:
                intermediate_clips.append(
                    self._process_single_batch(
                        batch, batch_idx, num_batches, intermediates_dir, progress_callback
                    )
                )
            except Exception:
                failed_batches += 1
                logger.warning(
                    f"Batch {batch_idx + 1}/{num_batches} failed, skipping "
                    f"({len(batch)} clips lost)"
                )
            self.check_cancelled_fn()

        if not intermediate_clips:
            raise RuntimeError(f"All {num_batches} batches failed — no video could be assembled")

        if failed_batches:
            logger.info(
                f"Assembly continuing with {len(intermediate_clips)}/{num_batches} "
                f"batches ({failed_batches} skipped)"
            )

        if progress_callback:
            progress_callback(0.85, f"Merging {len(intermediate_clips)} batches...")
        result = self.concat.merge_intermediate_batches(
            intermediate_clips, output_path, progress_callback
        )
        if not self.settings.debug_preserve_intermediates:
            shutil.rmtree(intermediates_dir, ignore_errors=True)
        return result
