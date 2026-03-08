"""Scalable assembly methods for VideoAssembler.

This mixin provides the scalable assembly pipeline that encodes clips
individually and assembles them using xfade chains, along with
transition type determination and validation.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from immich_memories.processing.assembly_config import (
    CHUNK_SIZE,
    AssemblyClip,
    TransitionType,
)

logger = logging.getLogger(__name__)


class AssemblerScalableMixin:
    """Mixin providing scalable assembly methods for VideoAssembler."""

    def _validate_fade_transitions(
        self,
        transitions: list[str],
        clip_durations: list[float],
        fade: float,
    ) -> list[str]:
        """Validate and downgrade fade transitions where clips are too short.

        Args:
            transitions: List of transition types to validate.
            clip_durations: Duration of each clip.
            fade: Fade duration.

        Returns:
            Updated transitions list (may have some fades downgraded to cuts).
        """
        min_duration_for_fade = fade * 2
        for i in range(len(transitions)):
            if transitions[i] == "fade":
                clip_a_dur = clip_durations[i]
                clip_b_dur = clip_durations[i + 1] if i + 1 < len(clip_durations) else 0

                if clip_a_dur < min_duration_for_fade or clip_b_dur < min_duration_for_fade:
                    logger.warning(
                        f"Transition {i}: Downgrading fade to cut - "
                        f"clip durations too short ({clip_a_dur:.2f}s, {clip_b_dur:.2f}s) "
                        f"for {fade}s fade"
                    )
                    transitions[i] = "cut"

        logger.info(
            f"Transitions: {sum(1 for t in transitions if t == 'fade')} fades, "
            f"{sum(1 for t in transitions if t == 'cut')} cuts"
        )
        return transitions

    def _assemble_scalable_chunks(
        self,
        encoded_clips: list[Path],
        clip_durations: list[float],
        transitions: list[str],
        fade: float,
        output_path: Path,
        temp_dir: Path,
        progress_callback: Callable[[float, str], None] | None = None,
    ) -> None:
        """Assemble encoded clips in chunks with xfade, then concat.

        Args:
            encoded_clips: Pre-encoded clip paths.
            clip_durations: Duration of each clip.
            transitions: Transition types for each boundary.
            fade: Fade duration.
            output_path: Final output path.
            temp_dir: Directory for temporary chunk files.
            progress_callback: Progress callback.
        """
        SCALABLE_CHUNK_SIZE = 4

        if len(encoded_clips) <= SCALABLE_CHUNK_SIZE:
            if progress_callback:
                progress_callback(0.7, "Building final assembly...")
            self._assemble_xfade_chain(
                encoded_clips, clip_durations, transitions, fade, output_path
            )
            return

        # Split into chunks
        chunks: list[tuple[list[Path], list[float], list[str]]] = []
        i = 0
        while i < len(encoded_clips):
            end = min(i + SCALABLE_CHUNK_SIZE, len(encoded_clips))
            chunk_clips = encoded_clips[i:end]
            chunk_durs = clip_durations[i:end]
            chunk_trans = transitions[i : end - 1] if end < len(encoded_clips) else transitions[i:]
            chunks.append((chunk_clips, chunk_durs, chunk_trans))
            i = end

        logger.info(
            f"Chunked assembly: {len(chunks)} chunks of up to {SCALABLE_CHUNK_SIZE} clips each"
        )

        chunk_outputs: list[Path] = []
        for ci, (chunk_clips, chunk_durs, chunk_trans) in enumerate(chunks):
            if progress_callback:
                progress_callback(
                    0.6 + (ci / len(chunks)) * 0.3, f"Assembling chunk {ci + 1}/{len(chunks)}"
                )

            if len(chunk_clips) == 1:
                chunk_outputs.append(chunk_clips[0])
            else:
                chunk_path = temp_dir / f"chunk_{ci:02d}.mp4"
                self._assemble_xfade_chain(chunk_clips, chunk_durs, chunk_trans, fade, chunk_path)
                chunk_outputs.append(chunk_path)

        # Concat chunks
        if progress_callback:
            progress_callback(0.95, "Joining chunks...")

        if len(chunk_outputs) == 1:
            import shutil as _shutil

            _shutil.copy2(chunk_outputs[0], output_path)
        else:
            self._concat_with_copy(chunk_outputs, output_path)

    def _assemble_scalable(
        self,
        clips: list[AssemblyClip],
        output_path: Path,
        progress_callback: Callable[[float, str], None] | None = None,
    ) -> Path:
        """Assemble clips using scalable transition-only rendering.

        This method is memory-efficient and scales to any number of clips:
        1. Encode each clip individually (constant memory per clip)
        2. Render only transition segments (0.5s each)
        3. Concat all with stream copy (no re-encoding)

        Memory usage: O(1) - constant regardless of clip count.

        Args:
            clips: List of clips to assemble.
            output_path: Output video path.
            progress_callback: Progress callback.

        Returns:
            Path to assembled video.
        """
        import shutil

        if len(clips) < 2:
            if len(clips) == 1:
                shutil.copy2(clips[0].path, output_path)
                return output_path
            raise ValueError("No clips to assemble")

        fade = self.settings.transition_duration
        temp_dir = output_path.parent / ".assembly_temps"
        temp_dir.mkdir(parents=True, exist_ok=True)

        # Determine target resolution ONCE for ALL clips
        target_resolution = self._resolve_target_resolution(clips)

        logger.info(
            f"Scalable assembly: {len(clips)} clips with transition-only rendering "
            f"at {target_resolution[0]}x{target_resolution[1]}"
        )

        try:
            # Step 1: Encode each clip to target format
            encoded_clips: list[Path] = []
            for i, clip in enumerate(clips):
                if progress_callback:
                    progress_callback(i / len(clips) * 0.6, f"Encoding clip {i + 1}/{len(clips)}")
                encoded_path = temp_dir / f"clip_{i:03d}.mp4"
                self._encode_single_clip(clip, encoded_path, target_resolution=target_resolution)
                encoded_clips.append(encoded_path)

            # Step 2: Probe clip durations and determine transitions
            clip_durations = [self._probe_duration(p, "video") for p in encoded_clips]
            transitions = self._get_transition_types(clips)
            transitions = self._validate_fade_transitions(transitions, clip_durations, fade)

            # Step 3: Chunked xfade assembly
            self._assemble_scalable_chunks(
                encoded_clips,
                clip_durations,
                transitions,
                fade,
                output_path,
                temp_dir,
                progress_callback,
            )

            return output_path

        finally:
            if not self.settings.debug_preserve_intermediates:
                shutil.rmtree(temp_dir, ignore_errors=True)
            else:
                logger.info(f"Debug mode: preserving temp files in {temp_dir}")

    def _get_transition_types(self, clips: list[AssemblyClip]) -> list[str]:
        """Get the transition type for each clip boundary.

        Args:
            clips: List of clips.

        Returns:
            List of "fade" or "cut" for each boundary (len = len(clips) - 1).
        """
        # Use predecided transitions if available
        if self.settings.predecided_transitions:
            return self.settings.predecided_transitions

        # Otherwise determine based on settings
        transitions = []
        for i in range(len(clips) - 1):
            clip = clips[i]
            next_clip = clips[i + 1]

            # Title screens always use fade
            if (
                clip.is_title_screen
                or next_clip.is_title_screen
                or self.settings.transition == TransitionType.CROSSFADE
            ):
                transitions.append("fade")
            elif self.settings.transition == TransitionType.CUT:
                transitions.append("cut")
            elif self.settings.transition == TransitionType.SMART:
                # Use outgoing_transition if set, otherwise default to fade
                trans = getattr(clip, "outgoing_transition", None) or "fade"
                transitions.append(trans)
            else:
                transitions.append("cut")

        return transitions

    def _assemble_chunked(
        self,
        clips: list[AssemblyClip],
        output_path: Path,
        progress_callback: Callable[[float, str], None] | None = None,
    ) -> Path:
        """Assemble many clips using chunked processing to avoid memory exhaustion.

        When processing many clips (44+) at 4K with xfade transitions, FFmpeg's
        filter_complex can exceed available memory. This method divides clips into
        smaller batches, processes each batch separately, then concatenates the
        intermediate files.

        Args:
            clips: List of clips to assemble (typically > 12 clips).
            output_path: Final output video path.
            progress_callback: Progress callback for UI updates.

        Returns:
            Path to assembled video.
        """
        import math
        import shutil

        num_clips = len(clips)
        num_batches = math.ceil(num_clips / CHUNK_SIZE)

        logger.info(
            f"Chunked assembly: {num_clips} clips -> {num_batches} batches of ~{CHUNK_SIZE}"
        )

        intermediates_dir = output_path.parent / ".intermediates"
        intermediates_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Intermediate files will be stored in: {intermediates_dir}")

        intermediate_clips: list[AssemblyClip] = []
        try:
            for batch_idx in range(num_batches):
                start_idx = batch_idx * CHUNK_SIZE
                end_idx = min(start_idx + CHUNK_SIZE, num_clips)
                batch = clips[start_idx:end_idx]

                if progress_callback:
                    batch_progress = (batch_idx / num_batches) * 0.8
                    progress_callback(
                        batch_progress,
                        f"Processing batch {batch_idx + 1}/{num_batches} ({len(batch)} clips)...",
                    )

                intermediate_path = intermediates_dir / f"batch_{batch_idx:03d}.mp4"

                if len(batch) == 1:
                    shutil.copy2(batch[0].path, intermediate_path)
                    batch_duration = batch[0].duration
                else:
                    batch_base_progress = (batch_idx / num_batches) * 0.8
                    batch_progress_range = (1 / num_batches) * 0.8

                    def make_batch_progress_cb(
                        base: float,
                        range_: float,
                        idx: int,
                        total: int,
                    ) -> Callable[[float, str], None]:
                        def batch_progress_cb(pct: float, msg: str) -> None:
                            if progress_callback:
                                overall_pct = base + (pct * range_)
                                progress_callback(overall_pct, f"Batch {idx + 1}/{total}: {msg}")

                        return batch_progress_cb

                    cb = make_batch_progress_cb(
                        batch_base_progress,
                        batch_progress_range,
                        batch_idx,
                        num_batches,
                    )
                    self._assemble_batch_direct(
                        batch,
                        intermediate_path,
                        cb if progress_callback else None,
                    )

                    batch_duration = sum(c.duration for c in batch)
                    batch_duration -= self.settings.transition_duration * (len(batch) - 1)

                is_title = batch[-1].is_title_screen if batch else False

                intermediate_clips.append(
                    AssemblyClip(
                        path=intermediate_path,
                        duration=batch_duration,
                        date=None,
                        asset_id=f"batch_{batch_idx}",
                        is_title_screen=is_title,
                    )
                )

                logger.info(
                    f"Batch {batch_idx + 1}/{num_batches} complete: {intermediate_path.name}"
                )
                self._check_cancelled()

            if progress_callback:
                progress_callback(0.85, f"Merging {num_batches} batches...")

            logger.info(f"Final merge: {len(intermediate_clips)} intermediate files")

            result = self._merge_intermediate_batches(
                intermediate_clips,
                output_path,
                progress_callback,
            )

            if self.settings.debug_preserve_intermediates:
                logger.info(f"Debug mode: preserving intermediate files in {intermediates_dir}")
            else:
                logger.info(f"Cleaning up intermediate files in {intermediates_dir}")
                shutil.rmtree(intermediates_dir, ignore_errors=True)

            return result

        except Exception:
            logger.error(
                f"Chunked assembly failed. Intermediate files preserved in: {intermediates_dir}"
            )
            raise

    # _merge_intermediate_batches and _assemble_batch_direct are in AssemblerBatchMixin
