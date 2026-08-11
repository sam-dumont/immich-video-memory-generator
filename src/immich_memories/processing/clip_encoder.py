"""Single-clip encoding and FFmpeg command execution."""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable
from pathlib import Path

from immich_memories.processing.assembly_config import (
    AssemblyClip,
    AssemblySettings,
    _get_rotation_filter,
)
from immich_memories.processing.encoding_plan import (
    EncodingPlan,
    HdrTransfer,
    software_fallback_plan,
    uses_hardware_encoder,
)
from immich_memories.processing.ffmpeg_prober import FFmpegProber
from immich_memories.processing.ffmpeg_runner import (
    AssemblyContext,
    _run_ffmpeg_with_progress,
)
from immich_memories.processing.hdr_utilities import (
    _detect_hdr_type,
    _get_colorspace_filter,
    _get_hdr_conversion_filter,
)
from immich_memories.processing.scaling_utilities import _get_smart_crop_filter
from immich_memories.security import validate_video_path

logger = logging.getLogger(__name__)


def encoder_args_for_plan(plan: EncodingPlan) -> list[str]:
    """Build FFmpeg arguments from a resolved plan without selecting again."""
    args = ["-c:v", plan.encoder, *plan.encoder_args, "-pix_fmt", plan.pixel_format]
    if plan.codec.value == "h265" and plan.container == "mp4":
        args.extend(["-tag:v", "hvc1"])
    if plan.hdr:
        color_trc = "smpte2084" if plan.target_transfer is HdrTransfer.PQ else "arib-std-b67"
        args.extend(
            [
                "-colorspace",
                "bt2020nc",
                "-color_primaries",
                "bt2020",
                "-color_trc",
                color_trc,
            ]
        )
    else:
        args.extend(
            [
                "-colorspace",
                "bt709",
                "-color_primaries",
                "bt709",
                "-color_trc",
                "bt709",
            ]
        )
    return args


def log_ffmpeg_error(result: subprocess.CompletedProcess) -> str:
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
        prober: FFmpegProber,
        face_center_fn: Callable[[Path], tuple[float, float] | None],
        *,
        default_resolution: tuple[int, int] = (1920, 1080),
    ) -> None:
        self.settings = settings
        self.prober = prober
        self.face_center_fn = face_center_fn
        self.default_resolution = default_resolution

    def resolve_encode_resolution(
        self, target_resolution: tuple[int, int] | None
    ) -> tuple[int, int]:
        if target_resolution:
            return target_resolution
        if self.settings.target_resolution:
            return self.settings.target_resolution
        return self.default_resolution

    def resolve_encode_hdr(self, clip: AssemblyClip) -> tuple[str, str]:
        plan = self.settings.encoding_plan
        source_hdr = _detect_hdr_type(clip.path)
        if plan.hdr:
            target_hdr = plan.target_transfer.value
            conversion = _get_hdr_conversion_filter(source_hdr, target_hdr, required=True)
            return target_hdr, conversion + _get_colorspace_filter(target_hdr)
        if source_hdr:
            return "sdr", _get_hdr_conversion_filter(
                source_hdr, "sdr", required=True
            ) + _get_colorspace_filter("sdr")
        return "sdr", _get_colorspace_filter("sdr")

    def encode_single_clip(
        self,
        clip: AssemblyClip,
        output_path: Path,
        target_resolution: tuple[int, int] | None = None,
    ) -> None:
        """Encode with normalized resolution, frame rate, and A/V sync guarantee."""
        validate_video_path(clip.path, must_exist=True)
        target_w, target_h = self.resolve_encode_resolution(target_resolution)

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

        def build_command(plan: EncodingPlan) -> list[str]:
            common_suffix = (
                f"{fps_filter},settb=1/{target_fps},"
                f"format={plan.pixel_format}{colorspace_filter},setsar=1,"
                f"trim=0:{clip.duration},setpts=PTS-STARTPTS"
            )
            filter_complex = self._build_single_clip_filter(
                clip, target_w, target_h, rotation_filter, common_suffix, audio_filter
            )
            return [
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
                *encoder_args_for_plan(plan),
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

        plan = self.settings.encoding_plan
        result = subprocess.run(build_command(plan), capture_output=True, text=True, timeout=1800)
        if result.returncode != 0 and uses_hardware_encoder(plan):
            fallback_plan = software_fallback_plan(plan)
            logger.warning(
                "Hardware encoder %s failed; retrying %s in software",
                plan.encoder,
                fallback_plan.codec.value,
            )
            result = subprocess.run(
                build_command(fallback_plan), capture_output=True, text=True, timeout=1800
            )
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
        # WHY: frosted glass effect — blur + noise texture + smooth.
        # Cinematic look, not surveillance. Scales with shorter dimension.
        privacy_filter = ""
        if self.settings.privacy_mode and not clip.is_title_screen:
            short_side = min(target_w, target_h)
            sigma = int(short_side * 0.035)
            privacy_filter = f"gblur=sigma={sigma},noise=alls=15:allf=t,gblur=sigma=10,"

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
                    video_filter = f"{rotation_filter}{privacy_filter}setpts=PTS-STARTPTS,{crop_filter},{common_suffix}"
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
                f"[0:v]{rotation_filter}{privacy_filter}setpts=PTS-STARTPTS,split[bg][fg];"
                f"[bg]scale={target_w}:{target_h}:force_original_aspect_ratio=increase:flags=fast_bilinear,"
                f"crop={target_w}:{target_h},boxblur=luma_radius=150:chroma_radius=150:luma_power=3:chroma_power=3[blurred];"
                f"[fg]scale={target_w}:{target_h}:force_original_aspect_ratio=decrease:flags=lanczos[scaled];"
                f"[blurred][scaled]overlay=(W-w)/2:(H-h)/2,{common_suffix}[vout];"
                f"{audio_filter}"
            )

        video_filter = (
            f"{rotation_filter}{privacy_filter}setpts=PTS-STARTPTS,"
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
        """Re-encodes for frame-accurate trim boundaries (stream copy can't do this)."""
        validate_video_path(input_path, must_exist=True)

        plan = self.settings.encoding_plan
        video_codec_args = encoder_args_for_plan(plan)
        _, color_filter = self.resolve_encode_hdr(AssemblyClip(path=input_path, duration=duration))
        video_filter = f"{color_filter},format={plan.pixel_format}"

        audio_format = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
        loudnorm = ",loudnorm=I=-16:TP=-1.5:LRA=11" if self.settings.normalize_clip_audio else ""

        filter_complex = (
            f"[0:v]trim=start={start}:duration={duration},setpts=PTS-STARTPTS"
            f"{video_filter}[vout];"
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
                f"[0:v]trim=start={start}:duration={duration},setpts=PTS-STARTPTS"
                f"{video_filter}[vout];"
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
        video_codec_args = encoder_args_for_plan(self.settings.encoding_plan)
        logger.info(
            "Encoding final output with %s (%s)",
            self.settings.encoding_plan.encoder,
            "HDR" if self.settings.encoding_plan.hdr else "SDR",
        )

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
