"""Title screen insertion: generation, dividers, and assemble_with_titles."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from immich_memories.processing.assembly_config import (
    AssemblyClip,
    AssemblySettings,
    TransitionType,
)
from immich_memories.processing.ffmpeg_prober import FFmpegProber
from immich_memories.processing.scaling_utilities import aggregate_mood_from_clips
from immich_memories.processing.title_background_renderer import TitleBackgroundRenderer
from immich_memories.processing.title_divider_planner import TitleDividerPlanner

logger = logging.getLogger(__name__)

# Type alias for the assemble callback
AssembleFn = Callable[
    [list[AssemblyClip], Path, Callable[[float, str], None] | None],
    Path,
]


class TitleInserter:
    """Inserts title screens, month/year dividers, and location cards into clip lists."""

    def __init__(self, settings: AssemblySettings, prober: FFmpegProber) -> None:
        self.settings = settings
        self.prober = prober
        self.background_renderer = TitleBackgroundRenderer(settings, prober)

    @staticmethod
    def _trim_first_clip(clips: list[AssemblyClip], trim_seconds: float) -> None:
        """Trim seconds from the start of the first clip (used in title slow-mo)."""
        if not clips:
            return
        first = clips[0]
        if first.duration > trim_seconds + 1.0:
            # WHY replace(): AssemblyClip carries sixteen fields and rebuilding
            # it by hand copied eight, silently dropping a user-set
            # rotation_override, the has_music flag the ducking pass depends on,
            # has_speech, is_photo and the planned outgoing_transition. Trimming
            # a clip should change its start and its length, nothing else.
            clips[0] = replace(
                first,
                duration=first.duration - trim_seconds,
                input_seek=trim_seconds,
            )

    def _build_title_config(
        self,
        title_settings: Any,
        target_w: int,
        target_h: int,
        fps: int,
    ) -> Any:
        """Build TitleScreenConfig from assembly parameters."""
        from immich_memories.titles import TitleScreenConfig

        orientation = "portrait" if target_h > target_w else "landscape"
        max_dim = max(target_w, target_h)
        resolution_tier = "4k" if max_dim >= 2160 else "1080p" if max_dim >= 1080 else "720p"
        logger.info(
            f"Generating title screens ({target_w}x{target_h}, "
            f"{'HDR' if self.settings.encoding_plan.hdr else 'SDR'}, {fps}fps)"
        )
        return TitleScreenConfig(
            enabled=True,
            title_duration=title_settings.title_duration,
            month_divider_duration=title_settings.month_divider_duration,
            ending_duration=title_settings.ending_duration,
            locale=title_settings.locale,
            style_mode=title_settings.style_mode,
            show_month_dividers=title_settings.show_month_dividers,
            month_divider_threshold=title_settings.month_divider_threshold,
            animated_background=title_settings.animated_background,
            show_decorative_lines=title_settings.show_decorative_lines,
            orientation=orientation,
            resolution=resolution_tier,
            resolution_width=target_w,
            resolution_height=target_h,
            fps=float(fps),
            encoding_plan=self.settings.encoding_plan,
            title_override=title_settings.title_override,
            subtitle_override=title_settings.subtitle_override,
        )

    def _decide_transitions_for_final_clips(self, clips: list[AssemblyClip]) -> list[str]:
        """Pre-decide transitions for the full clip list (title + content + ending).

        WHY: the assembler's get_transition_types rebuilds AssemblyContext
        from the full clip list, which shifts HDR type indices when title
        screens are inserted. By pre-deciding here, the assembler uses
        predecided_transitions directly and never rebuilds the context.

        Content clips use the same SMART logic as the assembler (_pick_transition).
        Title screens use explicit outgoing_transition or auto-fade.
        """
        from immich_memories.processing.assembly_engine import _pick_transition

        transitions = []
        consecutive_fades = 0
        consecutive_cuts = 0
        for i in range(len(clips) - 1):
            t, consecutive_fades, consecutive_cuts = _pick_transition(
                clips[i], clips[i + 1], consecutive_fades, consecutive_cuts
            )
            transitions.append(t)
        return transitions

    def _generate_ending(
        self,
        clips: list[AssemblyClip],
        final_clips: list[AssemblyClip],
        generator: Any,
        title_output_dir: Path,
        target_w: int,
        target_h: int,
        detected_fps: int,
        hdr_type: str | None,
        progress_callback: Callable[[float, str], None] | None,
        use_content_bg: bool = True,
    ) -> None:
        """Generate ending screen (reverse slow-mo or fade-to-white)."""
        if progress_callback:
            progress_callback(0.1, "Generating ending screen...")
        ending_clip = None
        if use_content_bg:
            ending_clip = self.background_renderer.render_last_clip(
                clips,
                title_output_dir,
                target_w,
                target_h,
                detected_fps,
                hdr_type,
            )

        def _ending_frame_progress(frame: int, total: int) -> None:
            if progress_callback:
                progress_callback(
                    0.35 + 0.15 * frame / max(total, 1), "Generating ending screen..."
                )

        ending_screen = generator.generate_ending_screen(
            content_clip_path=ending_clip, frame_progress=_ending_frame_progress
        )
        # WHY: last content clip gets hard cut → ending, and trim 0.5s from
        # the end since those frames were used in the ending slow-mo.
        source_seconds = 0.5
        if final_clips and not final_clips[-1].is_title_screen:
            last = final_clips[-1]
            trim_dur = (
                last.duration - source_seconds
                if ending_clip and last.duration > source_seconds + 1.0
                else last.duration
            )
            # Same here: the hand-built copy also lost the clip's place, so a
            # cut ending on a located clip dropped its caption.
            final_clips[-1] = replace(
                last,
                duration=trim_dur,
                outgoing_transition="cut" if use_content_bg else None,
            )
        final_clips.append(
            AssemblyClip(
                path=ending_screen.path,
                duration=ending_screen.duration,
                date=None,
                asset_id="ending_screen",
                is_title_screen=True,
            )
        )
        logger.info(f"Generated ending screen: {ending_screen.path}")

    # ------------------------------------------------------------------
    # Orientation / resolution detection
    # ------------------------------------------------------------------

    def get_orientation_from_clips(self, clips: list[AssemblyClip]) -> str:
        """Detect dominant video orientation from first 10 clips."""
        portrait_count = 0
        landscape_count = 0
        for clip in clips[:10]:
            res = self.prober.get_video_resolution(clip.path)
            if res:
                w, h = res
                if h > w:
                    portrait_count += 1
                elif w > h:
                    landscape_count += 1
        if portrait_count > landscape_count:
            return "portrait"
        return "landscape"

    def get_resolution_tier(self, clips: list[AssemblyClip]) -> str:
        """Detect resolution tier from first 10 clips."""
        max_height = 0
        for clip in clips[:10]:
            res = self.prober.get_video_resolution(clip.path)
            if res:
                max_height = max(max_height, max(res))
        if max_height >= 2160:
            return "4k"
        elif max_height >= 1080:
            return "1080p"
        return "720p"

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def _resolve_assembly_params(
        self, clips: list[AssemblyClip]
    ) -> tuple[int, int, int, str | None]:
        """Resolve target resolution, fps, and HDR type using the assembler's logic."""
        from immich_memories.processing.assembly_engine import (
            create_assembly_context,
            resolve_target_resolution,
        )

        target_w, target_h = resolve_target_resolution(self.settings, self.prober, clips)
        detected_fps = self.prober.detect_max_framerate(clips)
        ctx = create_assembly_context(self.settings, self.prober, clips, target_w, target_h)
        hdr_type = ctx.hdr_type if self.settings.encoding_plan.hdr else None
        return target_w, target_h, detected_fps, hdr_type

    def assemble_with_titles(
        self,
        clips: list[AssemblyClip],
        output_path: Path,
        assemble_fn: AssembleFn,
        progress_callback: Callable[[float, str], None] | None = None,
    ) -> Path:
        """Assemble clips with title screens, dividers, and ending screen.

        Args:
            clips: List of content clips.
            output_path: Path for output video.
            assemble_fn: Callback to the assembler's assemble() method.
            progress_callback: Progress callback (0.0 to 1.0).

        Returns:
            Path to assembled video.
        """
        if not clips:
            raise ValueError("No clips provided")

        title_settings = self.settings.title_screens
        if title_settings is None or not title_settings.enabled:
            return assemble_fn(clips, output_path, progress_callback)

        try:
            from immich_memories.titles import TitleScreenGenerator
        except ImportError as e:
            logger.warning(f"Title screens not available: {e}")
            return assemble_fn(clips, output_path, progress_callback)

        target_w, target_h, detected_fps, hdr_type = self._resolve_assembly_params(clips)
        title_config = self._build_title_config(title_settings, target_w, target_h, detected_fps)

        title_output_dir = output_path.parent / ".title_screens"
        title_output_dir.mkdir(parents=True, exist_ok=True)

        mood = title_settings.mood
        if mood is None:
            mood = aggregate_mood_from_clips(clips)
            logger.info(
                f"Auto-detected mood from clips: {mood}"
                if mood
                else "No mood detected from clips, using default style"
            )

        generator = TitleScreenGenerator(
            config=title_config, mood=mood, output_dir=title_output_dir
        )

        # Progress budget within assemble_with_titles:
        #   0.00 - 0.35  Title screen generation (~90s at 4K)
        #   0.35 - 0.50  Ending screen generation (~90s at 4K)
        #   0.50 - 1.00  Streaming encode of all clips
        import time as _time

        _t_title_start = _time.monotonic()

        # 1. Opening title screen (trip map or standard)
        if progress_callback:
            progress_callback(0.0, "Generating title screen...")

        is_trip = getattr(title_settings, "memory_type", None) == "trip"
        use_content_bg = (
            getattr(title_settings, "title_background", "content_backed") == "content_backed"
        )
        content_clip = None
        if is_trip and title_settings.trip_locations and title_settings.trip_title_text:
            title_screen = generator.generate_trip_map_screen(
                locations=title_settings.trip_locations,
                title_text=title_settings.trip_title_text,
                home_lat=getattr(title_settings, "home_lat", None),
                home_lon=getattr(title_settings, "home_lon", None),
            )
            use_content_bg = False  # trip maps don't use content-backed
            logger.info(f"Generated trip map intro: {title_screen.path}")
        else:
            if use_content_bg:
                content_clip = self.background_renderer.render_first_clip(
                    clips,
                    title_output_dir,
                    target_w,
                    target_h,
                    detected_fps,
                    hdr_type,
                )

            def _title_frame_progress(frame: int, total: int) -> None:
                if progress_callback:
                    progress_callback(0.35 * frame / max(total, 1), "Generating title screen...")

            title_screen = generator.generate_title_screen(
                year=title_settings.year,
                month=title_settings.month,
                start_date=title_settings.start_date,
                end_date=title_settings.end_date,
                person_name=title_settings.person_name,
                birthday_age=title_settings.birthday_age,
                content_clip_path=content_clip,
                frame_progress=_title_frame_progress,
            )
            logger.info(f"Generated title screen: {title_screen.path}")

        final_clips: list[AssemblyClip] = [
            AssemblyClip(
                path=title_screen.path,
                duration=title_screen.duration,
                date=None,
                asset_id="title_screen",
                is_title_screen=True,
                # WHY: content_backed uses hard cut (deblur IS the transition).
                # Gradient mode uses default fade (is_title_screen auto-fades).
                outgoing_transition="cut" if use_content_bg else None,
            )
        ]

        # Trim 0.5s from first clip (used in title slow-mo)
        if use_content_bg and content_clip:
            self._trim_first_clip(clips, 0.5)

        _t_title_done = _time.monotonic()
        if progress_callback:
            progress_callback(0.35, "Title screen ready")

        # 2-3. Clips with dividers
        divider_planner = TitleDividerPlanner(generator, title_settings)
        content_clips = divider_planner.select_divider_strategy(clips, progress_callback, is_trip)
        final_clips.extend(content_clips)

        # 4. Ending screen
        # WHY: ending always uses content-backed (reverse slow-mo) when the
        # style supports it — even for trips where the INTRO uses a map.
        ending_uses_content_bg = (
            getattr(title_settings, "title_background", "content_backed") == "content_backed"
        )
        if title_settings.show_ending_screen:
            if progress_callback:
                progress_callback(0.35, "Generating ending screen...")
            self._generate_ending(
                clips,
                final_clips,
                generator,
                title_output_dir,
                target_w,
                target_h,
                detected_fps,
                hdr_type,
                None,  # don't pass callback to _generate_ending (we handle it here)
                use_content_bg=ending_uses_content_bg,
            )

        _t_ending_done = _time.monotonic()

        # 5. Assemble
        if progress_callback:
            progress_callback(0.50, "Encoding video...")
        logger.info(f"Assembling {len(final_clips)} clips (including title screens)")

        # WHY: pre-decide transitions for the full clip list so the assembler
        # doesn't call get_transition_types (which rebuilds HDR context from
        # the extended clip list, causing HDR type index mismatches).
        transitions = self._decide_transitions_for_final_clips(final_clips)
        logger.info(
            f"Transitions: {transitions.count('fade')} fades, {transitions.count('cut')} cuts"
        )

        saved = (
            self.settings.transition,
            self.settings.target_resolution,
            self.settings.auto_resolution,
            self.settings.predecided_transitions,
        )
        self.settings.transition = TransitionType.SMART
        self.settings.target_resolution = (target_w, target_h)
        self.settings.auto_resolution = False
        self.settings.predecided_transitions = transitions

        # WHY: Scale encoding progress into 0.50-1.0 range so it doesn't
        # reset the overall progress back to 0% after title generation.
        def _scaled_encode_cb(pct: float, msg: str) -> None:
            if progress_callback:
                progress_callback(0.50 + pct * 0.50, msg)

        try:
            result = assemble_fn(final_clips, output_path, _scaled_encode_cb)
            _t_encode_done = _time.monotonic()
            title_dur = _t_title_done - _t_title_start
            ending_dur = _t_ending_done - _t_title_done
            encode_dur = _t_encode_done - _t_ending_done
            total_dur = _t_encode_done - _t_title_start
            logger.info(
                f"Assembly timing ({len(final_clips)} clips, {total_dur:.1f}s): "
                f"title={title_dur:.1f}s, ending={ending_dur:.1f}s, encode={encode_dur:.1f}s"
            )
            return result
        finally:
            (
                self.settings.transition,
                self.settings.target_resolution,
                self.settings.auto_resolution,
                self.settings.predecided_transitions,
            ) = saved
