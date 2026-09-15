"""Core assembly engine for multi-clip video assembly.

Orchestrates scalable and strategy-based assembly pipelines.
Includes assembly context building (resolution, HDR, colorspace resolution).
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from pathlib import Path

from immich_memories.processing.assembly_config import (
    AssemblyClip,
    AssemblySettings,
    TransitionType,
)
from immich_memories.processing.clip_caption import resolve_caption_locale
from immich_memories.processing.clip_encoder import ClipEncoder
from immich_memories.processing.ffmpeg_prober import FFmpegProber
from immich_memories.processing.ffmpeg_runner import AssemblyContext
from immich_memories.processing.hdr_utilities import (
    _detect_color_primaries,
    _get_clip_hdr_types,
    _get_colorspace_filter,
)
from immich_memories.processing.probe_cache import ProbeCache
from immich_memories.processing.streaming_assembler import streaming_assemble_full

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

    plan = settings.encoding_plan
    pix_fmt = plan.pixel_format
    target_fps = prober.detect_max_framerate(clips)
    hdr_type = plan.target_transfer.value if plan.hdr else "sdr"

    # WHY: direct/standalone plans have no source provenance. Always inspect the
    # actual clips so an HDR source cannot be relabeled SDR without conversion.
    source_probe_cache = getattr(prober, "probe_cache", None)
    clip_hdr_types = (
        _get_clip_hdr_types(clips, probe_cache=source_probe_cache)
        if isinstance(source_probe_cache, ProbeCache)
        else _get_clip_hdr_types(clips)
    )
    clip_primaries: list[str | None] = []
    if plan.hdr:
        for clip in clips:
            clip_primaries.append(
                _detect_color_primaries(clip.path, probe_cache=source_probe_cache)
                if isinstance(source_probe_cache, ProbeCache)
                else _detect_color_primaries(clip.path)
            )
    else:
        clip_primaries = [None] * len(clips)

    unique_types = {t for t in clip_hdr_types if t is not None}
    if len(unique_types) > 1:
        logger.warning(
            f"Mixed HDR content detected: {unique_types} - converting all to {hdr_type.upper()}"
        )

    colorspace_filter = _get_colorspace_filter(hdr_type)

    return AssemblyContext(
        target_w=target_w,
        target_h=target_h,
        pix_fmt=pix_fmt,
        hdr_type=hdr_type,
        clip_hdr_types=clip_hdr_types,
        clip_primaries=clip_primaries,
        colorspace_filter=colorspace_filter,
        target_fps=target_fps,
        fade_duration=settings.effective_transition_duration,
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
    # WHY: explicit outgoing_transition takes priority over is_title_screen.
    # Content-backed title screens use "cut" (deblur reveal IS the transition).
    if clip_before.outgoing_transition is not None:
        t = clip_before.outgoing_transition
        if t == "fade":
            return t, consecutive_fades + 1, 0
        return t, 0, consecutive_cuts + 1
    if clip_before.is_title_screen or clip_after.is_title_screen:
        return "fade", consecutive_fades + 1, 0
    # Source IDs survive rerenders and temporary-file moves; process RNG state does not.
    boundary = f"{clip_before.asset_id}\0{clip_after.asset_id}".encode()
    use_fade = int.from_bytes(hashlib.sha256(boundary).digest()[:4]) / 2**32 < 0.7
    if consecutive_fades >= 3:
        use_fade = False
    if consecutive_cuts >= 2:
        use_fade = True
    if use_fade:
        return "fade", consecutive_fades + 1, 0
    return "cut", 0, consecutive_cuts + 1


def decide_transitions(
    clips: list[AssemblyClip], mode: TransitionType, duration: float = 0.5
) -> list[str]:
    """Use one reproducible boundary policy with or without title composition."""
    transitions = []
    if duration <= 0:
        return ["cut"] * max(0, len(clips) - 1)
    fades = cuts = 0
    for before, after in zip(clips, clips[1:], strict=False):
        if before.outgoing_transition is not None:
            transition = before.outgoing_transition
        elif mode in (TransitionType.CUT, TransitionType.NONE):
            transition = "cut"
        elif mode == TransitionType.CROSSFADE:
            transition = "fade"
        else:
            transition, _, _ = _pick_transition(before, after, fades, cuts)
        fades, cuts = (fades + 1, 0) if transition == "fade" else (0, cuts + 1)
        transitions.append(transition)
    return transitions


class AssemblyEngine:
    """Orchestrates multi-clip video assembly with transitions."""

    def __init__(
        self,
        settings: AssemblySettings,
        prober: FFmpegProber,
        encoder: ClipEncoder,
    ) -> None:
        self.settings = settings
        self.prober = prober
        self.encoder = encoder

    def assemble_scalable(
        self,
        clips: list[AssemblyClip],
        output_path: Path,
        progress_callback: Callable[[float, str], None] | None = None,
        frame_preview_callback: Callable[[bytes], None] | None = None,
    ) -> Path:
        """Assemble clips via streaming frame blender. Constant memory.

        Decodes one clip at a time, blends crossfade transitions with numpy,
        and pipes frames to a single FFmpeg encode process. Memory stays
        constant regardless of clip count (~550 MB at 4K).
        """
        if not clips:
            raise ValueError("No clips to assemble")
        if len(clips) == 1 and not (
            self.settings.add_date_overlay or self.settings.add_place_overlay
        ):
            return self._assemble_single_clip(clips[0], output_path)

        # Resolve target resolution ONCE for all clips — prevents each chunk
        # from auto-detecting a different resolution/orientation
        target_w, target_h = resolve_target_resolution(self.settings, self.prober, clips)
        saved_res = self.settings.target_resolution
        saved_auto = self.settings.auto_resolution
        self.settings.target_resolution = (target_w, target_h)
        self.settings.auto_resolution = False
        try:
            return self._assemble_scalable_inner(
                clips,
                output_path,
                progress_callback,
                target_w,
                target_h,
                frame_preview_callback,
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
        frame_preview_callback: Callable[[bytes], None] | None = None,
    ) -> Path:
        transitions = self.get_transition_types(clips)
        transitions = self._validate_fade_transitions(
            transitions, [c.duration for c in clips], self.settings.effective_transition_duration
        )

        ctx = create_assembly_context(self.settings, self.prober, clips, target_w, target_h)
        fade_duration = self.settings.effective_transition_duration
        from immich_memories.audio.mixer import music_mute_windows

        # The engine is the only place the FINAL sequence (titles included)
        # and its transitions coexist — the music phase runs later (#466).
        self.settings.music_mute_windows = music_mute_windows(clips, transitions, fade_duration)
        plan = self.settings.encoding_plan
        if plan.hdr:
            logger.info("Streaming %s HDR assembly with %s", ctx.hdr_type.upper(), plan.encoder)
        else:
            logger.info("Streaming SDR assembly with %s", plan.encoder)

        logger.info(f"Streaming assembly: {len(clips)} clips at {target_w}x{target_h}")

        def record_effective_plan(effective_plan) -> None:
            self.settings.encoding_plan = effective_plan

        caption_locale = self.settings.caption_locale
        if caption_locale is None and self.settings.title_screens:
            caption_locale = self.settings.title_screens.locale

        streaming_assemble_full(
            clips=clips,
            transitions=transitions,
            output_path=output_path,
            width=target_w,
            height=target_h,
            fps=ctx.target_fps,
            fade_duration=fade_duration,
            encoding_plan=plan,
            ctx=ctx,
            normalize_audio=self.settings.normalize_clip_audio,
            privacy_mode=self.settings.privacy_mode,
            date_overlay=self.settings.add_date_overlay,
            place_overlay=self.settings.add_place_overlay,
            caption_locale=resolve_caption_locale(caption_locale),
            scale_mode=self.settings.scale_mode,
            progress_callback=progress_callback,
            frame_preview_callback=frame_preview_callback,
            probe_cache=self.prober.probe_cache,
            effective_plan_callback=record_effective_plan,
        )
        return output_path

    def _assemble_single_clip(self, clip: AssemblyClip, output_path: Path) -> Path:
        """Encode a single clip under the resolved final-output plan."""
        target_resolution = resolve_target_resolution(self.settings, self.prober, [clip])
        effective_plan = self.encoder.encode_single_clip(
            clip,
            output_path,
            target_resolution=target_resolution,
        )
        if effective_plan is not None:  # compatibility with injected encoders
            self.settings.encoding_plan = effective_plan
        return output_path

    def get_transition_types(self, clips: list[AssemblyClip]) -> list[str]:
        """Get the transition type for each clip boundary."""
        if self.settings.predecided_transitions:
            return self.settings.predecided_transitions
        return decide_transitions(
            clips, self.settings.transition, self.settings.effective_transition_duration
        )

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
