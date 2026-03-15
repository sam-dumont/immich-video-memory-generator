"""Single-clip encoding and FFmpeg command execution."""

from __future__ import annotations

import logging
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from immich_memories.config import get_config
from immich_memories.processing.assembly_config import (
    AssemblyClip,
    AssemblySettings,
    _get_rotation_filter,
)
from immich_memories.processing.ffmpeg_prober import VideoProber
from immich_memories.processing.ffmpeg_runner import (
    AssemblyContext,
    _run_ffmpeg_with_progress,
)
from immich_memories.processing.filter_builder import FilterBuilder
from immich_memories.processing.hdr_utilities import (
    _detect_hdr_type,
    _get_colorspace_filter,
    _get_gpu_encoder_args,
)
from immich_memories.processing.scaling_utilities import _get_smart_crop_filter
from immich_memories.security import validate_video_path

logger = logging.getLogger(__name__)


def configure_pyav_output_stream(
    output_container: Any,
    target_fps: int,
    is_hdr: bool,
    width: int,
    height: int,
    crf: int,
) -> Any:
    """Configure a PyAV output stream. macOS uses VideoToolbox, Linux uses libx265."""
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


def log_ffmpeg_error(result: subprocess.CompletedProcess) -> str:
    """Extract useful error lines from FFmpeg stderr."""
    stderr_lines = result.stderr.split("\n")
    error_lines = [
        line
        for line in stderr_lines
        if "error" in line.lower() or "Error" in line or "invalid" in line.lower()
    ]
    if error_lines:
        return "\n".join(error_lines[-10:])
    return result.stderr[-2000:] if len(result.stderr) > 2000 else result.stderr


class ClipEncoder:
    """Encodes individual clips and runs FFmpeg assembly commands."""

    def __init__(
        self,
        settings: AssemblySettings,
        prober: VideoProber,
        filter_builder: FilterBuilder,
        face_center_fn: Callable[[Path], tuple[float, float] | None],
    ) -> None:
        self.settings = settings
        self.prober = prober
        self.filter_builder = filter_builder
        self.face_center_fn = face_center_fn

    def resolve_encode_resolution(
        self, target_resolution: tuple[int, int] | None
    ) -> tuple[int, int]:
        """Resolve target resolution for single clip encoding."""
        if target_resolution:
            return target_resolution
        if self.settings.target_resolution:
            return self.settings.target_resolution
        return get_config().output.resolution_tuple

    def resolve_encode_hdr(self, clip: AssemblyClip) -> tuple[str, str]:
        """Resolve HDR type and colorspace filter for a clip."""
        hdr_type = "hlg"
        if self.settings.preserve_hdr:
            clip_hdr = _detect_hdr_type(clip.path)
            if clip_hdr:
                hdr_type = clip_hdr
            return hdr_type, _get_colorspace_filter(hdr_type)
        return hdr_type, ""

    def encode_single_clip(
        self,
        clip: AssemblyClip,
        output_path: Path,
        target_resolution: tuple[int, int] | None = None,
    ) -> None:
        """Encode a single clip to target format with A/V sync guarantee."""
        validate_video_path(clip.path, must_exist=True)
        target_w, target_h = self.resolve_encode_resolution(target_resolution)

        pix_fmt = (
            ("p010le" if sys.platform == "darwin" else "yuv420p10le")
            if self.settings.preserve_hdr
            else "yuv420p"
        )
        target_fps = 60
        hdr_type, colorspace_filter = self.resolve_encode_hdr(clip)

        rotation_filter = ""
        if clip.rotation_override is not None and clip.rotation_override != 0:
            rotation_filter = _get_rotation_filter(clip.rotation_override) + ","

        has_audio = self.prober.has_audio_stream(clip.path)

        source_fps = self.prober.probe_framerate(clip.path)
        if source_fps < 50:
            fps_filter = f"fps={target_fps},tmix=frames=2:weights='1 1'"
        else:
            fps_filter = f"fps={target_fps}"

        common_suffix = (
            f"{fps_filter},settb=1/{target_fps},"
            f"format={pix_fmt}{colorspace_filter},setsar=1,"
            f"trim=0:{clip.duration},setpts=PTS-STARTPTS"
        )

        audio_format = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
        use_loudnorm = self.settings.normalize_clip_audio and not clip.is_title_screen
        loudnorm = ",loudnorm=I=-16:TP=-1.5:LRA=11" if use_loudnorm else ""
        if has_audio:
            audio_filter = (
                f"[0:a]{audio_format},asetpts=PTS-STARTPTS{loudnorm},"
                f"apad=whole_dur={clip.duration},atrim=0:{clip.duration},asetpts=PTS-STARTPTS[aout]"
            )
        else:
            audio_filter = (
                f"anullsrc=r=48000:cl=stereo,atrim=0:{clip.duration},"
                f"{audio_format},asetpts=PTS-STARTPTS[aout]"
            )

        filter_complex = self._build_single_clip_filter(
            clip, target_w, target_h, rotation_filter, common_suffix, audio_filter
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

    def _build_single_clip_filter(
        self,
        clip: AssemblyClip,
        target_w: int,
        target_h: int,
        rotation_filter: str,
        common_suffix: str,
        audio_filter: str,
    ) -> str:
        """Build filter_complex for encoding a single clip with scale mode handling."""
        use_blur = self.settings.scale_mode == "blur" and not clip.is_title_screen
        use_smart_zoom = self.settings.scale_mode == "smart_zoom" and not clip.is_title_screen

        if use_smart_zoom:
            face_center = self.face_center_fn(clip.path)
            if face_center:
                clip_res = self.prober.get_video_resolution(clip.path)
                if clip_res:
                    src_w, src_h = clip_res
                    crop_filter = _get_smart_crop_filter(
                        src_w, src_h, target_w, target_h, face_center[0], face_center[1]
                    )
                    video_filter = (
                        f"{rotation_filter}setpts=PTS-STARTPTS,{crop_filter},{common_suffix}"
                    )
                    logger.info(
                        f"Smart zoom: cropping centered on face "
                        f"at ({face_center[0]:.2f}, {face_center[1]:.2f})"
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

        video_filter = (
            f"{rotation_filter}setpts=PTS-STARTPTS,"
            f"scale={target_w}:{target_h}:"
            f"force_original_aspect_ratio=decrease:flags=lanczos,"
            f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:black,"
            f"{common_suffix}"
        )
        return f"[0:v]{video_filter}[vout];{audio_filter}"

    def trim_segment_copy(
        self,
        input_path: Path,
        output_path: Path,
        start: float,
        duration: float,
    ) -> None:
        """Trim a video segment using stream copy (no re-encoding)."""
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

    def trim_segment_reencode(
        self,
        input_path: Path,
        output_path: Path,
        start: float,
        duration: float,
    ) -> None:
        """Trim a video segment with re-encoding for frame-accurate boundaries."""
        validate_video_path(input_path, must_exist=True)

        video_codec_args = _get_gpu_encoder_args(
            crf=self.settings.output_crf,
            preserve_hdr=self.settings.preserve_hdr,
        )

        audio_format = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
        loudnorm = ",loudnorm=I=-16:TP=-1.5:LRA=11" if self.settings.normalize_clip_audio else ""

        filter_complex = (
            f"[0:v]trim=start={start}:duration={duration},setpts=PTS-STARTPTS[vout];"
            f"anullsrc=r=48000:cl=stereo,atrim=0:{duration}[silence];"
            f"[0:a]atrim=start={start}:duration={duration},{audio_format},"
            f"asetpts=PTS-STARTPTS{loudnorm},apad=whole_dur={duration}[asrc];"
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

        if result.returncode != 0:
            logger.warning(f"Trim with audio failed, using silence: {result.stderr[-200:]}")

            filter_complex_silent = (
                f"[0:v]trim=start={start}:duration={duration},setpts=PTS-STARTPTS[vout];"
                f"anullsrc=r=48000:cl=stereo,atrim=0:{duration},{audio_format},"
                f"asetpts=PTS-STARTPTS[aout]"
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

    def run_ffmpeg_assembly(
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
        """Build and run the FFmpeg assembly command."""
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

        total_duration = self.prober.estimate_duration(clips)
        logger.debug(f"Running assembly: {' '.join(cmd)}")
        return _run_ffmpeg_with_progress(cmd, total_duration, progress_callback)
