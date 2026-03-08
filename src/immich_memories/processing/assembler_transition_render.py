"""Transition segment extraction and rendering for VideoAssembler.

This mixin provides segment extraction for transitions and the complete
transition segment rendering pipeline with multiple fallback strategies.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from immich_memories.processing.hdr_utilities import (
    _detect_hdr_type,
    _get_gpu_encoder_args,
)

logger = logging.getLogger(__name__)


class AssemblerTransitionRenderMixin:
    """Mixin providing transition segment rendering methods for VideoAssembler."""

    def _extract_segment_for_transition(
        self,
        src: Path,
        output: Path,
        start: float,
        duration: float,
    ) -> None:
        """Extract a segment from a video for use in a transition.

        Uses filter_complex with anullsrc mixing to GUARANTEE audio output,
        even when seeking causes audio/video timestamp misalignment.

        Args:
            src: Source video path.
            output: Output path for extracted segment.
            start: Start time in seconds.
            duration: Duration to extract.
        """
        video_codec_args = _get_gpu_encoder_args(
            crf=self.settings.output_crf,
            preserve_hdr=self.settings.preserve_hdr,
        )

        audio_format = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"

        # ALWAYS use filter_complex with anullsrc to guarantee audio output.
        filter_complex = (
            # Video: trim to exact segment, reset timestamps
            f"[0:v]trim=start={start}:duration={duration},setpts=PTS-STARTPTS[vout];"
            # Generate silence for guaranteed duration
            f"anullsrc=r=48000:cl=stereo,atrim=0:{duration}[silence];"
            # Try to extract audio (may fail if seeking lands between audio frames)
            f"[0:a]atrim=start={start}:duration={duration},{audio_format},"
            f"asetpts=PTS-STARTPTS,apad=whole_dur={duration}[asrc];"
            # Mix: silence provides guaranteed duration, source audio provides content
            f"[silence][asrc]amix=inputs=2:duration=longest:weights='0.001 1',"
            f"atrim=0:{duration},asetpts=PTS-STARTPTS[aout]"
        )

        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(src),
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
            str(output),
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)

        # If filter_complex failed (e.g., atrim on audio failed), retry with silence only
        if result.returncode != 0:
            logger.warning(
                f"Extraction with audio failed, retrying with silence: {result.stderr[-200:]}"
            )

            filter_complex_silent = (
                f"[0:v]trim=start={start}:duration={duration},setpts=PTS-STARTPTS[vout];"
                f"anullsrc=r=48000:cl=stereo,atrim=0:{duration},{audio_format},asetpts=PTS-STARTPTS[aout]"
            )

            cmd_silent = [
                "ffmpeg",
                "-y",
                "-i",
                str(src),
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
                str(output),
            ]

            result = subprocess.run(cmd_silent, capture_output=True, text=True, timeout=1800)
            if result.returncode != 0:
                raise RuntimeError(f"Failed to extract segment: {result.stderr[-500:]}")

        # Verify the result has both streams and proper duration
        has_video_out = self._has_video_stream(output)
        has_audio_out = self._has_audio_stream(output)
        if not has_video_out or not has_audio_out:
            logger.error(
                f"Segment extraction incomplete: {output.name} - "
                f"video={has_video_out}, audio={has_audio_out}"
            )
        else:
            # Log actual durations for debugging
            out_video_dur = self._probe_duration(output, "video")
            out_audio_dur = self._probe_duration(output, "audio")
            logger.debug(
                f"Extracted {output.name}: requested={duration:.3f}s, "
                f"actual video={out_video_dur:.3f}s, audio={out_audio_dur:.3f}s"
            )

    def _render_transition_segment(
        self,
        src_a: Path,
        a_start: float,
        a_duration: float,
        src_b: Path,
        b_start: float,
        b_duration: float,
        output_path: Path,
    ) -> None:
        """Render a crossfade transition segment using pre-extracted segments.

        This approach extracts each segment to a clean temporary file first,
        then applies xfade to those clean files. This avoids timestamp and
        filter complexity issues that cause "nothing written" errors.

        Args:
            src_a: First source video path.
            a_start: Start time in src_a.
            a_duration: Duration to extract from src_a.
            src_b: Second source video path.
            b_start: Start time in src_b.
            b_duration: Duration to extract from src_b.
            output_path: Output path for transition segment.
        """
        import shutil

        # Safety validation - ensure we have valid positions
        if a_start < 0:
            logger.error(f"Invalid a_start={a_start}, src_a duration may be too short")
            raise ValueError(f"Transition source A start position is negative: {a_start}")
        if b_start < 0:
            logger.error(f"Invalid b_start={b_start}")
            raise ValueError(f"Transition source B start position is negative: {b_start}")
        if a_duration <= 0 or b_duration <= 0:
            raise ValueError(f"Invalid duration: a={a_duration}, b={b_duration}")

        fade_dur = a_duration  # Both should be equal (the fade duration)

        # Probe actual durations and apply safety margin to avoid seeking past content
        actual_a_dur = self._probe_duration(src_a, "video")
        actual_b_dur = self._probe_duration(src_b, "video")

        # Check audio streams for better diagnostics
        has_audio_a = self._has_audio_stream(src_a)
        has_audio_b = self._has_audio_stream(src_b)

        # Apply safety margin (0.1s) to avoid edge-of-video issues
        safety_margin = 0.1
        max_a_start = max(0, actual_a_dur - fade_dur - safety_margin)
        if a_start > max_a_start:
            logger.warning(
                f"Transition seek adjusted: a_start {a_start:.2f}s -> {max_a_start:.2f}s "
                f"(src_a duration: {actual_a_dur:.2f}s, fade: {fade_dur:.2f}s)"
            )
            a_start = max_a_start

        logger.debug(
            f"Rendering transition: {src_a.name}[{a_start:.2f}s] -> {src_b.name}[{b_start:.2f}s], "
            f"fade={fade_dur:.2f}s, has_audio: a={has_audio_a}, b={has_audio_b}, "
            f"durations: a={actual_a_dur:.2f}s, b={actual_b_dur:.2f}s"
        )

        # Get encoder args for final output
        hdr_type = "hlg"
        if self.settings.preserve_hdr:
            hdr_a = _detect_hdr_type(src_a)
            if hdr_a:
                hdr_type = hdr_a

        video_codec_args = _get_gpu_encoder_args(
            crf=self.settings.output_crf,
            preserve_hdr=self.settings.preserve_hdr,
            hdr_type=hdr_type,
        )

        # Create temp directory for segment extraction
        temp_dir = output_path.parent / f".trans_{output_path.stem}"
        temp_dir.mkdir(exist_ok=True)

        audio_format = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"

        try:
            # Step 1: Extract segment A (end of clip A)
            seg_a = temp_dir / "seg_a.mp4"
            self._extract_segment_for_transition(src_a, seg_a, a_start, a_duration)

            # Step 2: Extract segment B (start of clip B)
            seg_b = temp_dir / "seg_b.mp4"
            self._extract_segment_for_transition(src_b, seg_b, b_start, b_duration)

            # Verify extracted segments have audio (should be guaranteed now)
            seg_a_audio = self._has_audio_stream(seg_a)
            seg_b_audio = self._has_audio_stream(seg_b)
            logger.debug(f"Extracted segments have audio: seg_a={seg_a_audio}, seg_b={seg_b_audio}")

            # Step 3: Try frame-by-frame rendering first (precise, no stutter)
            if self._render_transition_framewise(seg_a, seg_b, output_path, fade_dur):
                logger.debug("Frame-by-frame transition successful")
                return  # Success! Skip xfade fallback

            # Fallback: Use FFmpeg xfade (may have stutter with mixed framerates)
            logger.debug("Falling back to FFmpeg xfade for transition")

            # Get resolutions - xfade REQUIRES both inputs to have SAME resolution
            res_a = self._get_video_resolution(seg_a)
            res_b = self._get_video_resolution(seg_b)

            # Use the larger resolution as target (to avoid quality loss)
            if res_a and res_b:
                target_w = max(res_a[0], res_b[0])
                target_h = max(res_a[1], res_b[1])
            elif res_a:
                target_w, target_h = res_a
            elif res_b:
                target_w, target_h = res_b
            else:
                # Fallback to 1080p
                target_w, target_h = 1920, 1080

            logger.debug(
                f"Transition resolution: seg_a={res_a}, seg_b={res_b}, target={target_w}x{target_h}"
            )

            # Step 3: xfade the two clean segments
            scale_filter = (
                f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,"
                f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:black,setsar=1"
            )
            filter_complex = (
                f"[0:v]{scale_filter}[v0scaled];"
                f"[1:v]{scale_filter}[v1scaled];"
                f"[v0scaled][v1scaled]xfade=transition=fade:duration={fade_dur}:offset=0,trim=0:{fade_dur},setpts=PTS-STARTPTS[vout];"
                f"[0:a][1:a]acrossfade=d={fade_dur}:c1=tri:c2=tri,atrim=0:{fade_dur},asetpts=PTS-STARTPTS[aout]"
            )

            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                str(seg_a),
                "-i",
                str(seg_b),
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
            if result.returncode != 0:
                # If acrossfade failed, try manual audio crossfade with amix
                logger.warning(
                    f"acrossfade failed (seg_a_audio={seg_a_audio}, seg_b_audio={seg_b_audio}), "
                    f"trying amix fallback: {result.stderr[-200:]}"
                )

                filter_complex_fallback = (
                    f"[0:v]{scale_filter}[v0scaled];"
                    f"[1:v]{scale_filter}[v1scaled];"
                    f"[v0scaled][v1scaled]xfade=transition=fade:duration={fade_dur}:offset=0,trim=0:{fade_dur},setpts=PTS-STARTPTS[vout];"
                    f"anullsrc=r=48000:cl=stereo,atrim=0:{fade_dur}[silence];"
                    f"[0:a]{audio_format},afade=t=out:st=0:d={fade_dur}[afade_a];"
                    f"[1:a]{audio_format},afade=t=in:st=0:d={fade_dur}[afade_b];"
                    f"[afade_a][afade_b]amix=inputs=2:duration=first[amixed];"
                    f"[silence][amixed]amix=inputs=2:duration=first:weights='0 1',atrim=0:{fade_dur},asetpts=PTS-STARTPTS[aout]"
                )

                cmd_fallback = [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(seg_a),
                    "-i",
                    str(seg_b),
                    "-filter_complex",
                    filter_complex_fallback,
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

                result = subprocess.run(cmd_fallback, capture_output=True, text=True, timeout=1800)
                if result.returncode != 0:
                    # Last resort: video xfade with silent audio
                    logger.warning(
                        f"amix fallback also failed, using silent audio: {result.stderr[-200:]}"
                    )

                    filter_complex_silent = (
                        f"[0:v]{scale_filter}[v0scaled];"
                        f"[1:v]{scale_filter}[v1scaled];"
                        f"[v0scaled][v1scaled]xfade=transition=fade:duration={fade_dur}:offset=0,trim=0:{fade_dur},setpts=PTS-STARTPTS[vout];"
                        f"anullsrc=r=48000:cl=stereo,atrim=0:{fade_dur},{audio_format},asetpts=PTS-STARTPTS[aout]"
                    )

                    cmd_silent = [
                        "ffmpeg",
                        "-y",
                        "-i",
                        str(seg_a),
                        "-i",
                        str(seg_b),
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
                        "-t",
                        str(fade_dur),
                        "-movflags",
                        "+faststart",
                        str(output_path),
                    ]

                    result = subprocess.run(
                        cmd_silent, capture_output=True, text=True, timeout=1800
                    )
                    if result.returncode != 0:
                        raise RuntimeError(f"Failed to render transition: {result.stderr[-500:]}")

        finally:
            # Cleanup temp files
            if not self.settings.debug_preserve_intermediates:
                shutil.rmtree(temp_dir, ignore_errors=True)
