"""Title screen insertion: generation, dividers, and assemble_with_titles."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any

from immich_memories.processing.assembly_config import (
    AssemblyClip,
    AssemblySettings,
    TransitionType,
)
from immich_memories.processing.ffmpeg_prober import FFmpegProber
from immich_memories.processing.scaling_utilities import aggregate_mood_from_clips

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

    # ------------------------------------------------------------------
    # Pre-render first clip for title background
    # ------------------------------------------------------------------

    def _pre_render_first_clip(
        self,
        clips: list[AssemblyClip],
        output_dir: Path,
        target_w: int,
        target_h: int,
        fps: int,
        hdr_type: str | None,
    ) -> Path | None:
        """Pre-render the first clip through the SAME assembly pipeline.

        Uses FrameDecoder (the streaming assembler's decoder) with identical
        filters: rotation, scale_mode, HDR conversion, resolution. This
        guarantees the title background matches the clip it reveals into —
        no orientation guessing, no resolution guessing.
        """
        if not clips:
            return None

        import subprocess

        from immich_memories.processing.assembly_engine import (
            create_assembly_context,
        )
        from immich_memories.processing.hdr_utilities import _get_gpu_encoder_args
        from immich_memories.processing.streaming_assembler import _make_decoder

        output_path = output_dir / "first_clip_processed.mp4"
        ctx = create_assembly_context(self.settings, self.prober, clips, target_w, target_h)

        # WHY: _make_decoder applies the EXACT same filter chain as the
        # streaming assembler: rotation, scale_mode (blur bg), HDR conversion,
        # resolution, fps, SAR. The output is pixel-identical to what the
        # assembler will produce for this clip.
        decoder = _make_decoder(
            clips[0],
            0,
            target_w,
            target_h,
            fps,
            ctx,
            privacy_mode=self.settings.privacy_mode,
            scale_mode=self.settings.scale_mode or "blur",
            hdr_type=hdr_type,
        )

        preserve_hdr = hdr_type is not None
        try:
            encoder_args = _get_gpu_encoder_args(
                crf=12,
                preserve_hdr=preserve_hdr,
                hdr_type=hdr_type or "hlg",
            )
        except Exception:
            encoder_args = ["-c:v", "libx264", "-crf", "12"]

        pix_fmt = "yuv420p10le" if hdr_type else "rgb24"
        # WHY: rawvideo pipe strips color metadata — must tag input explicitly
        input_color_args: list[str] = []
        if hdr_type:
            input_color_args = [
                "-color_range",
                "tv",
                "-color_primaries",
                "bt2020",
                "-color_trc",
                "arib-std-b67",
                "-colorspace",
                "bt2020nc",
            ]
        cmd = [
            "ffmpeg", "-y",
            "-f", "rawvideo", "-pix_fmt", pix_fmt,
            "-s", f"{target_w}x{target_h}", "-r", str(fps),
            *input_color_args,
            "-i", "pipe:0",
            *encoder_args,
            "-an", "-movflags", "+faststart",
            str(output_path),
        ]  # fmt: skip

        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
            frame_count = 0
            max_frames = fps * 1  # 1 second
            for frame in decoder:
                if frame_count >= max_frames:
                    break
                proc.stdin.write(frame.data)  # type: ignore[union-attr]
                frame_count += 1
            proc.stdin.close()  # type: ignore[union-attr]
            proc.wait(timeout=30)
            if proc.returncode == 0 and output_path.exists():
                logger.info(
                    f"Pre-rendered first clip ({frame_count} frames, "
                    f"{target_w}x{target_h}): {output_path}"
                )
                return output_path
            stderr = proc.stderr.read().decode()[-200:] if proc.stderr else ""
            logger.warning(f"Pre-render encode failed: {stderr}")
        except Exception:
            logger.warning("Failed to pre-render first clip", exc_info=True)
        return None

    @staticmethod
    def _trim_first_clip(clips: list[AssemblyClip], trim_seconds: float) -> None:
        """Trim seconds from the start of the first clip (used in title slow-mo)."""
        if not clips:
            return
        first = clips[0]
        if first.duration > trim_seconds + 1.0:
            clips[0] = AssemblyClip(
                path=first.path,
                duration=first.duration - trim_seconds,
                date=first.date,
                asset_id=first.asset_id,
                input_seek=trim_seconds,
                latitude=first.latitude,
                longitude=first.longitude,
                location_name=first.location_name,
            )

    def _build_title_config(
        self,
        title_settings: Any,
        target_w: int,
        target_h: int,
        fps: int,
        hdr: bool,
    ) -> Any:
        """Build TitleScreenConfig from assembly parameters."""
        from immich_memories.titles import TitleScreenConfig

        orientation = "portrait" if target_h > target_w else "landscape"
        max_dim = max(target_w, target_h)
        resolution_tier = "4k" if max_dim >= 2160 else "1080p" if max_dim >= 1080 else "720p"
        logger.info(
            f"Generating title screens ({target_w}x{target_h}, {'HDR' if hdr else 'SDR'}, {fps}fps)"
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
            orientation=orientation,
            resolution=resolution_tier,
            fps=float(fps),
            hdr=hdr,
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
            ending_clip = self._pre_render_last_clip(
                clips,
                title_output_dir,
                target_w,
                target_h,
                detected_fps,
                hdr_type,
            )
        ending_screen = generator.generate_ending_screen(content_clip_path=ending_clip)
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
            final_clips[-1] = AssemblyClip(
                path=last.path,
                duration=trim_dur,
                date=last.date,
                asset_id=last.asset_id,
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

    def _pre_render_last_clip(
        self,
        clips: list[AssemblyClip],
        output_dir: Path,
        target_w: int,
        target_h: int,
        fps: int,
        hdr_type: str | None,
    ) -> Path | None:
        """Pre-render the last clip's final second for the ending screen."""
        if not clips:
            return None

        import subprocess

        from immich_memories.processing.assembly_engine import create_assembly_context
        from immich_memories.processing.hdr_utilities import _get_gpu_encoder_args
        from immich_memories.processing.streaming_assembler import _make_decoder

        clip = clips[-1]
        output_path = output_dir / "last_clip_processed.mp4"
        ctx = create_assembly_context(self.settings, self.prober, clips, target_w, target_h)

        decoder = _make_decoder(
            clip,
            len(clips) - 1,
            target_w,
            target_h,
            fps,
            ctx,
            privacy_mode=self.settings.privacy_mode,
            scale_mode=self.settings.scale_mode or "blur",
            hdr_type=hdr_type,
        )

        preserve_hdr = hdr_type is not None
        try:
            encoder_args = _get_gpu_encoder_args(
                crf=12,
                preserve_hdr=preserve_hdr,
                hdr_type=hdr_type or "hlg",
            )
        except Exception:
            encoder_args = ["-c:v", "libx264", "-crf", "12"]

        pix_fmt = "yuv420p10le" if hdr_type else "rgb24"
        input_color_args: list[str] = []
        if hdr_type:
            input_color_args = [
                "-color_range",
                "tv",
                "-color_primaries",
                "bt2020",
                "-color_trc",
                "arib-std-b67",
                "-colorspace",
                "bt2020nc",
            ]
        cmd = [
            "ffmpeg", "-y",
            "-f", "rawvideo", "-pix_fmt", pix_fmt,
            "-s", f"{target_w}x{target_h}", "-r", str(fps),
            *input_color_args,
            "-i", "pipe:0",
            *encoder_args,
            "-an", "-movflags", "+faststart",
            str(output_path),
        ]  # fmt: skip

        try:
            # WHY: read ALL frames, keep only the last fps*1 frames
            all_frames: list[bytes] = []
            for frame in decoder:
                all_frames.append(bytes(frame.data))
                # Keep only last 2 seconds worth of frames
                if len(all_frames) > fps * 2:
                    all_frames.pop(0)

            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
            # WHY: write exactly the last 0.5s (source_seconds) to match
            # what SlowmoBackgroundReader reads. The ending starts where
            # the clip ends — no "go back in time".
            source_frames = max(1, fps // 2)  # 0.5s
            for frame_data in all_frames[-source_frames:]:
                proc.stdin.write(frame_data)  # type: ignore[union-attr]
            proc.stdin.close()  # type: ignore[union-attr]
            proc.wait(timeout=30)

            if proc.returncode == 0 and output_path.exists():
                logger.info(f"Pre-rendered last clip for ending: {output_path}")
                return output_path
        except Exception:
            logger.warning("Failed to pre-render last clip", exc_info=True)
        return None

    # ------------------------------------------------------------------
    # Date parsing
    # ------------------------------------------------------------------

    def parse_clip_date(self, clip: AssemblyClip) -> date | None:
        """Parse the date from an AssemblyClip."""
        if not clip.date:
            return None
        try:
            for fmt in (
                "%Y-%m-%d",
                "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M:%S",
                "%B %d, %Y",
                "%b %d, %Y",
            ):
                try:
                    return datetime.strptime(clip.date, fmt).date()
                except ValueError:
                    continue
            return datetime.strptime(clip.date[:10], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            logger.debug(f"Could not parse date: {clip.date}")
            return None

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
    # Month / year change detection
    # ------------------------------------------------------------------

    def detect_month_changes(self, clips: list[AssemblyClip]) -> list[tuple[int, int, int]]:
        """Detect month changes. Returns [(insert_index, month, year)]."""
        month_changes: list[tuple[int, int, int]] = []
        current_month: tuple[int, int] | None = None
        month_clip_counts: dict[tuple[int, int], int] = {}
        clips_with_dates = 0

        for i, clip in enumerate(clips):
            clip_date = self.parse_clip_date(clip)
            if clip_date is None:
                continue
            clips_with_dates += 1
            month_key = (clip_date.year, clip_date.month)
            month_clip_counts[month_key] = month_clip_counts.get(month_key, 0) + 1
            if current_month is None or month_key != current_month:
                month_changes.append((i, clip_date.month, clip_date.year))
                logger.debug(f"Month change at clip {i}: {current_month} -> {month_key}")
            current_month = month_key

        logger.info(f"Month detection: {len(month_changes)} changes in {clips_with_dates} clips")
        return month_changes

    def detect_year_changes(self, clips: list[AssemblyClip]) -> list[tuple[int, int]]:
        """Detect year changes. Returns [(insert_index, year)]."""
        year_changes: list[tuple[int, int]] = []
        current_year: int | None = None

        for i, clip in enumerate(clips):
            clip_date = self.parse_clip_date(clip)
            if clip_date is None:
                continue
            if current_year is None or clip_date.year != current_year:
                year_changes.append((i, clip_date.year))
                if current_year is not None:
                    logger.info(
                        f"Year change detected at clip {i}: {current_year} -> {clip_date.year}"
                    )
                current_year = clip_date.year

        logger.info(f"Year detection: {len(year_changes)} year changes found")
        return year_changes

    # ------------------------------------------------------------------
    # Divider generation
    # ------------------------------------------------------------------

    def generate_year_dividers(
        self,
        clips: list[AssemblyClip],
        generator: Any,
        title_settings: Any,
        progress_callback: Callable[[float, str], None] | None,
    ) -> dict[int, Path]:
        """Generate year divider screens. Returns {year: path}."""
        year_changes = self.detect_year_changes(clips)
        year_divider_paths: dict[int, Path] = {}
        if not year_changes:
            return year_divider_paths

        if progress_callback:
            progress_callback(0.05, "Generating year dividers...")

        for _, year in year_changes:
            if year not in year_divider_paths:
                divider = generator.generate_year_divider(year)
                year_divider_paths[year] = divider.path
                logger.info(f"Generated year divider: {year}")
        return year_divider_paths

    def build_clips_with_year_dividers(
        self,
        clips: list[AssemblyClip],
        year_divider_paths: dict[int, Path],
        title_settings: Any,
    ) -> list[AssemblyClip]:
        """Interleave clips with year divider screens."""
        result: list[AssemblyClip] = []
        current_year: int | None = None
        for clip in clips:
            clip_date = self.parse_clip_date(clip)
            if clip_date:
                if (
                    current_year is None or clip_date.year != current_year
                ) and clip_date.year in year_divider_paths:
                    result.append(
                        AssemblyClip(
                            path=year_divider_paths[clip_date.year],
                            duration=title_settings.month_divider_duration,
                            date=None,
                            asset_id=f"year_divider_{clip_date.year}",
                            is_title_screen=True,
                        )
                    )
                current_year = clip_date.year
            result.append(clip)
        return result

    def generate_month_dividers(
        self,
        clips: list[AssemblyClip],
        generator: Any,
        title_settings: Any,
        progress_callback: Callable[[float, str], None] | None,
    ) -> dict[tuple[int, int], Path]:
        """Generate month divider screens. Returns {(year, month): path}."""
        month_changes = self.detect_month_changes(clips)
        month_divider_paths: dict[tuple[int, int], Path] = {}

        if not (title_settings.show_month_dividers and month_changes):
            return month_divider_paths

        if progress_callback:
            progress_callback(0.05, "Generating month dividers...")

        for _, month, year in month_changes:
            key = (year, month)
            if key not in month_divider_paths:
                is_birthday = (
                    title_settings.birthday_month is not None
                    and month == title_settings.birthday_month
                )
                divider = generator.generate_month_divider(
                    month, year, is_birthday_month=is_birthday
                )
                month_divider_paths[key] = divider.path
                logger.info(
                    f"Generated month divider: {month}/{year}"
                    + (" (birthday!)" if is_birthday else "")
                )
        return month_divider_paths

    def build_clips_with_dividers(
        self,
        clips: list[AssemblyClip],
        month_divider_paths: dict[tuple[int, int], Path],
        title_settings: Any,
    ) -> list[AssemblyClip]:
        """Interleave clips with month divider screens."""
        result: list[AssemblyClip] = []
        current_month: tuple[int, int] | None = None

        for clip in clips:
            clip_date = self.parse_clip_date(clip)
            if clip_date:
                month_key = (clip_date.year, clip_date.month)
                # WHY: skip the first month divider — the intro title already
                # shows the month/year context. Only insert dividers when the
                # month CHANGES (not for the very first clip).
                if (
                    title_settings.show_month_dividers
                    and current_month is not None
                    and month_key != current_month
                    and month_key in month_divider_paths
                ):
                    result.append(
                        AssemblyClip(
                            path=month_divider_paths[month_key],
                            duration=title_settings.month_divider_duration,
                            date=None,
                            asset_id=f"month_divider_{month_key[1]:02d}",
                            is_title_screen=True,
                        )
                    )
                current_month = month_key
            result.append(clip)
        return result

    # ------------------------------------------------------------------
    # Location dividers (trip memories)
    # ------------------------------------------------------------------

    def make_location_card_clip(
        self,
        name: str,
        cache: dict[str, Path],
        generator: Any,
        title_settings: Any,
    ) -> AssemblyClip:
        """Return an AssemblyClip for a location card, using cache to avoid duplicates."""
        if name not in cache:
            card = generator.generate_location_card_screen(name)
            cache[name] = card.path
        return AssemblyClip(
            path=cache[name],
            duration=title_settings.month_divider_duration,
            date=None,
            asset_id=f"location_{name}",
            is_title_screen=True,
        )

    def build_clips_with_location_dividers(
        self,
        clips: list[AssemblyClip],
        generator: Any,
        title_settings: Any,
        progress_callback: Callable[[float, str], None] | None,
    ) -> list[AssemblyClip]:
        """Insert location cards between clips when location changes (>30km)."""
        from immich_memories.analysis.trip_detection import haversine_km

        if progress_callback:
            progress_callback(0.05, "Generating location cards...")

        result: list[AssemblyClip] = []
        location_card_cache: dict[str, Path] = {}
        prev_lat: float | None = None
        prev_lon: float | None = None
        threshold_km = 30.0

        for clip in clips:
            if clip.latitude is not None and clip.longitude is not None:
                if prev_lat is not None and prev_lon is not None:
                    dist = haversine_km(prev_lat, prev_lon, clip.latitude, clip.longitude)
                    if dist > threshold_km and clip.location_name:
                        card = self.make_location_card_clip(
                            clip.location_name, location_card_cache, generator, title_settings
                        )
                        result.append(card)
                        logger.info(f"Location card: {clip.location_name} (dist={dist:.0f}km)")
                prev_lat = clip.latitude
                prev_lon = clip.longitude
            result.append(clip)
        return result

    # ------------------------------------------------------------------
    # Divider strategy selection
    # ------------------------------------------------------------------

    def select_divider_strategy(
        self,
        clips: list[AssemblyClip],
        generator: Any,
        title_settings: Any,
        progress_callback: Callable[[float, str], None] | None,
        is_trip: bool,
    ) -> list[AssemblyClip]:
        """Select and apply the appropriate divider strategy for clips."""
        if is_trip and getattr(title_settings, "show_location_cards", True):
            return self.build_clips_with_location_dividers(
                clips, generator, title_settings, progress_callback
            )

        divider_mode = getattr(title_settings, "divider_mode", "month")
        if divider_mode == "year":
            year_divider_paths = self.generate_year_dividers(
                clips, generator, title_settings, progress_callback
            )
            return self.build_clips_with_year_dividers(clips, year_divider_paths, title_settings)

        if divider_mode == "month" and title_settings.show_month_dividers:
            month_divider_paths = self.generate_month_dividers(
                clips, generator, title_settings, progress_callback
            )
            return self.build_clips_with_dividers(clips, month_divider_paths, title_settings)

        return clips.copy()

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
        hdr_type = ctx.hdr_type if self.settings.preserve_hdr else None
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
        source_has_hdr = hdr_type is not None

        title_config = self._build_title_config(
            title_settings, target_w, target_h, detected_fps, source_has_hdr
        )

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

        # 1. Opening title screen (trip map or standard)
        if progress_callback:
            progress_callback(0.0, "Generating title screen...")

        is_trip = getattr(title_settings, "memory_type", None) == "trip"
        use_content_bg = (
            getattr(title_settings, "title_background", "content_backed") == "content_backed"
        )
        if is_trip and title_settings.trip_locations and title_settings.trip_title_text:
            title_screen = generator.generate_trip_map_screen(
                locations=title_settings.trip_locations,
                title_text=title_settings.trip_title_text,
                home_lat=getattr(title_settings, "home_lat", None),
                home_lon=getattr(title_settings, "home_lon", None),
            )
            logger.info(f"Generated trip map intro: {title_screen.path}")
        else:
            content_clip = None
            if use_content_bg:
                content_clip = self._pre_render_first_clip(
                    clips,
                    title_output_dir,
                    target_w,
                    target_h,
                    detected_fps,
                    hdr_type,
                )
            title_screen = generator.generate_title_screen(
                year=title_settings.year,
                month=title_settings.month,
                start_date=title_settings.start_date,
                end_date=title_settings.end_date,
                person_name=title_settings.person_name,
                birthday_age=title_settings.birthday_age,
                content_clip_path=content_clip,
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

        # 2-3. Clips with dividers
        content_clips = self.select_divider_strategy(
            clips, generator, title_settings, progress_callback, is_trip
        )
        final_clips.extend(content_clips)

        # 4. Ending screen
        if title_settings.show_ending_screen:
            self._generate_ending(
                clips,
                final_clips,
                generator,
                title_output_dir,
                target_w,
                target_h,
                detected_fps,
                hdr_type,
                progress_callback,
                use_content_bg=use_content_bg,
            )

        # 5. Assemble
        if progress_callback:
            progress_callback(0.15, "Assembling video...")
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

        try:
            return assemble_fn(final_clips, output_path, progress_callback)
        finally:
            (
                self.settings.transition,
                self.settings.target_resolution,
                self.settings.auto_resolution,
                self.settings.predecided_transitions,
            ) = saved
