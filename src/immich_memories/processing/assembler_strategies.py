"""Assembly strategy methods for VideoAssembler.

This mixin provides the main assembly strategies: cuts, crossfade,
smart transitions, chunked assembly, batch direct assembly, and
intermediate batch merging.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from immich_memories.processing.assembly_config import (
    CHUNKED_ASSEMBLY_THRESHOLD,
    AssemblyClip,
)
from immich_memories.processing.hdr_utilities import (
    _get_hdr_conversion_filter,
)

logger = logging.getLogger(__name__)


class AssemblerStrategyMixin:
    """Mixin providing assembly strategy methods for VideoAssembler."""

    def _assemble_with_cuts(
        self,
        clips: list[AssemblyClip],
        output_path: Path,
        progress_callback: Callable[[float, str], None] | None = None,
    ) -> Path:
        """Assemble clips with hard cuts (re-encodes to handle codec mismatches).

        Args:
            clips: List of clips to assemble.
            output_path: Output path.
            progress_callback: Progress callback.

        Returns:
            Path to output video.
        """
        if len(clips) == 0:
            raise ValueError("No clips to assemble")

        if len(clips) == 1:
            return self._process_single_clip(clips[0], output_path)

        # Resolve resolution and create assembly context
        target_w, target_h = self._resolve_target_resolution(clips)
        ctx = self._create_assembly_context(clips, target_w, target_h)

        # Determine frame rate for cuts (uses different logic than xfade methods)
        if self.settings.target_framerate:
            out_fps = self.settings.target_framerate
        elif self.settings.preserve_framerate:
            out_fps = self._detect_max_framerate(clips)
        else:
            out_fps = 30

        logger.info(f"Cuts assembly: {len(clips)} clips, {target_w}x{target_h} @ {out_fps}fps")

        # Build input arguments
        input_args = []
        for clip in clips:
            input_args.extend(["-i", str(clip.path)])

        # Scale each input (cuts uses simpler scale without aspect ratio handling)
        filter_parts = []
        for i, _clip in enumerate(clips):
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

            filter_parts.append(
                f"[{i}:v]setpts=PTS-STARTPTS,"
                f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease:flags=lanczos,"
                f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:black,"
                f"fps={out_fps},settb=1/{out_fps},format={ctx.pix_fmt}{hdr_conversion}{ctx.colorspace_filter},setsar=1[v{i}]"
            )

        # Build concat filter
        video_inputs = "".join(f"[v{i}]" for i in range(len(clips)))
        audio_inputs = "".join(f"[{i}:a]" for i in range(len(clips)))
        filter_parts.append(f"{video_inputs}concat=n={len(clips)}:v=1:a=0[vout]")
        filter_parts.append(f"{audio_inputs}concat=n={len(clips)}:v=0:a=1[aout]")

        filter_complex = ";".join(filter_parts)

        result = self._run_ffmpeg_assembly(
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
            logger.error(f"FFmpeg cuts assembly error: {result.stderr}")
            raise RuntimeError(f"Failed to assemble video with cuts: {result.stderr}")

        return output_path

    def _assemble_with_crossfade(
        self,
        clips: list[AssemblyClip],
        output_path: Path,
        progress_callback: Callable[[float, str], None] | None = None,
    ) -> Path:
        """Assemble clips with crossfade transitions.

        Args:
            clips: List of clips.
            output_path: Output path.
            progress_callback: Progress callback.

        Returns:
            Path to output video.
        """
        if len(clips) < 2:
            return self._process_single_clip(clips[0], output_path)

        # Memory optimization: use chunked assembly for many clips at 4K
        if len(clips) > CHUNKED_ASSEMBLY_THRESHOLD:
            logger.info(
                f"Using chunked assembly for {len(clips)} clips (threshold: {CHUNKED_ASSEMBLY_THRESHOLD})"
            )
            return self._assemble_chunked(clips, output_path, progress_callback)

        # Resolve resolution and create context
        target_w, target_h = self._resolve_target_resolution(clips)
        ctx = self._create_assembly_context(clips, target_w, target_h)

        # Build inputs
        inputs = []
        for clip in clips:
            inputs.extend(["-i", str(clip.path)])

        # Build per-clip video filters
        filter_parts = []
        for i, clip in enumerate(clips):
            filter_parts.append(self._build_clip_video_filter(i, clip, ctx))

        # Build audio prep filters
        audio_filter_parts, audio_labels = self._build_audio_prep_filters(clips)
        filter_parts.extend(audio_filter_parts)

        # Build xfade chain
        xfade_parts, final_video, final_audio, _ = self._build_xfade_chain(
            clips,
            ctx,
            audio_labels,
        )
        filter_parts.extend(xfade_parts)

        filter_complex = ";".join(filter_parts)

        # Run FFmpeg
        result = self._run_ffmpeg_assembly(
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
            stderr_tail = result.stderr[-1000:] if len(result.stderr) > 1000 else result.stderr
            logger.warning(
                f"Crossfade failed (code {result.returncode}), falling back to cuts. Error: {stderr_tail}"
            )
            return self._assemble_with_cuts(clips, output_path, progress_callback)

        return output_path

    def _decide_transitions(self, clips: list[AssemblyClip]) -> list[str]:
        """Decide which transition type to use between each pair of clips.

        Uses pre-decided transitions from clip.outgoing_transition when available,
        otherwise falls back to smart algorithm:
        - ALWAYS use fade for transitions involving title screens (intro/outro/dividers)
        - Use pre-decided transition if clip has outgoing_transition set
        - For remaining: 70% crossfade, 30% cut with consecutive limits

        Args:
            clips: List of clips to generate transitions for.

        Returns:
            List of transition types ("fade" or "cut") for each transition.
        """
        import random

        num_clips = len(clips)
        if num_clips < 2:
            return []

        num_transitions = num_clips - 1
        transitions = []
        consecutive_fades = 0
        consecutive_cuts = 0
        predecided_used = 0

        for i in range(num_transitions):
            # Get the two clips involved in this transition
            clip_before = clips[i]
            clip_after = clips[i + 1]

            # ALWAYS use fade for title screen transitions (never cut)
            if clip_before.is_title_screen or clip_after.is_title_screen:
                transitions.append("fade")
                consecutive_fades += 1
                consecutive_cuts = 0
                continue

            # Use pre-decided transition if available (from clips.plan_transitions)
            if clip_before.outgoing_transition is not None:
                transition = clip_before.outgoing_transition
                transitions.append(transition)
                predecided_used += 1
                if transition == "fade":
                    consecutive_fades += 1
                    consecutive_cuts = 0
                else:
                    consecutive_cuts += 1
                    consecutive_fades = 0
                continue

            # Fall back to smart algorithm: 70% crossfade, 30% cut
            use_fade = random.random() < 0.7

            # Force cut if too many consecutive fades
            if consecutive_fades >= 3:
                use_fade = False

            # Force fade if too many consecutive cuts
            if consecutive_cuts >= 2:
                use_fade = True

            if use_fade:
                transitions.append("fade")
                consecutive_fades += 1
                consecutive_cuts = 0
            else:
                transitions.append("cut")
                consecutive_cuts += 1
                consecutive_fades = 0

        logger.info(
            f"Smart transitions: {transitions.count('fade')} crossfades, "
            f"{transitions.count('cut')} cuts"
            + (f" ({predecided_used} pre-decided)" if predecided_used > 0 else "")
        )
        return transitions

    def _assemble_with_smart_transitions(
        self,
        clips: list[AssemblyClip],
        output_path: Path,
        progress_callback: Callable[[float, str], None] | None = None,
    ) -> Path:
        """Assemble clips with a mix of crossfades and cuts for variety.

        Args:
            clips: List of clips.
            output_path: Output path.
            progress_callback: Progress callback.

        Returns:
            Path to output video.
        """
        if len(clips) < 2:
            return self._process_single_clip(clips[0], output_path)

        # Memory optimization: use chunked assembly for many clips at 4K
        if len(clips) > CHUNKED_ASSEMBLY_THRESHOLD:
            logger.info(
                f"Using chunked assembly for {len(clips)} clips (threshold: {CHUNKED_ASSEMBLY_THRESHOLD})"
            )
            return self._assemble_chunked(clips, output_path, progress_callback)

        # Decide transitions for each clip pair
        transitions = self._decide_transitions(clips)

        # Resolve resolution and create context
        target_w, target_h = self._resolve_target_resolution(clips)
        ctx = self._create_assembly_context(clips, target_w, target_h)

        # Build inputs
        inputs = []
        for clip in clips:
            inputs.extend(["-i", str(clip.path)])

        # Build per-clip video filters (no aspect ratio handling for smart - uses pad)
        filter_parts = []
        for i, clip in enumerate(clips):
            filter_parts.append(
                self._build_clip_video_filter(i, clip, ctx, use_aspect_ratio_handling=False)
            )

        # Build audio prep filters (smart uses aresample mode)
        audio_filter_parts, audio_labels = self._build_audio_prep_filters(
            clips,
            use_amix_fallback=False,
        )
        filter_parts.extend(audio_filter_parts)

        # Build smart transition chain (mix of xfade and concat)
        transition_parts, final_video, final_audio = self._build_smart_transition_chain(
            clips,
            transitions,
            ctx,
            audio_labels,
        )
        filter_parts.extend(transition_parts)

        filter_complex = ";".join(filter_parts)

        # Run FFmpeg
        result = self._run_ffmpeg_assembly(
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
            stderr_tail = result.stderr[-1000:] if len(result.stderr) > 1000 else result.stderr
            logger.warning(
                f"Smart transitions failed (code {result.returncode}), falling back to crossfade. Error: {stderr_tail}"
            )
            return self._assemble_with_crossfade(clips, output_path, progress_callback)

        return output_path
