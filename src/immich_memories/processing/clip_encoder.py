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
from immich_memories.processing.hardware_encode import (
    HardwareChainUnavailable,
    apply_hardware_encode,
    encoder_backend,
)
from immich_memories.processing.hdr_utilities import (
    _detect_hdr_type,
    _get_colorspace_filter,
    _get_hdr_conversion_filter,
)
from immich_memories.processing.probe_cache import ProbeCache
from immich_memories.processing.scaling_utilities import _get_smart_crop_filter
from immich_memories.security import validate_video_path

logger = logging.getLogger(__name__)


def encoder_args_for_plan(plan: EncodingPlan) -> list[str]:
    """Build FFmpeg arguments from a resolved plan without selecting again."""
    args = ["-c:v", plan.encoder, *plan.encoder_args]
    # A VAAPI/QSV encoder reads hardware surfaces; naming a software pixel
    # format here asks it to encode frames it cannot see. The plan's format
    # reaches the encode through the upload filter instead.
    if encoder_backend(plan.encoder) is None:
        args.extend(["-pix_fmt", plan.pixel_format])
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


def hardware_command_for_plan(
    build: Callable[[EncodingPlan], list[str]],
    plan: EncodingPlan,
    *,
    video_label: str | None = None,
) -> tuple[EncodingPlan, list[str]]:
    """Build a command on the device, or deliberately build a software one instead.

    Returns the plan that the returned command actually encodes with, so the
    caller reports the encoder that ran rather than the one it asked for.
    """
    try:
        return plan, apply_hardware_encode(
            build(plan), pixel_format=plan.pixel_format, video_label=video_label
        )
    except HardwareChainUnavailable as exc:
        fallback = software_fallback_plan(plan)
        logger.warning(
            "No %s device chain for this command (%s); encoding with %s instead",
            plan.encoder,
            exc,
            fallback.encoder,
        )
        return fallback, build(fallback)


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
        source_probe_cache = getattr(self.prober, "probe_cache", None)
        source_hdr = (
            _detect_hdr_type(clip.path, probe_cache=source_probe_cache)
            if isinstance(source_probe_cache, ProbeCache)
            else _detect_hdr_type(clip.path)
        )
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
    ) -> EncodingPlan:
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

        plan, command = hardware_command_for_plan(
            build_command, self.settings.encoding_plan, video_label="[vout]"
        )

        def retry_in_software() -> tuple[subprocess.CompletedProcess, EncodingPlan]:
            fallback_plan = software_fallback_plan(plan)
            logger.warning(
                "Hardware encoder %s failed; retrying %s in software",
                plan.encoder,
                fallback_plan.codec.value,
            )
            return (
                subprocess.run(
                    build_command(fallback_plan), capture_output=True, text=True, timeout=1800
                ),
                fallback_plan,
            )

        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=1800)
            effective_plan = plan
        except (OSError, subprocess.TimeoutExpired):
            if not uses_hardware_encoder(plan):
                raise
            result, effective_plan = retry_in_software()
        else:
            if result.returncode == 0 or not uses_hardware_encoder(plan):
                if result.returncode != 0:
                    raise RuntimeError(f"Failed to encode clip: {result.stderr[-500:]}")
                return effective_plan
            result, effective_plan = retry_in_software()
        if result.returncode != 0:
            raise RuntimeError(f"Failed to encode clip: {result.stderr[-500:]}")
        return effective_plan

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
        framerate_args = ["-r", str(ctx.target_fps)]
        logger.info(f"Output frame rate: {ctx.target_fps}fps")

        def build(plan: EncodingPlan) -> list[str]:
            return [
                "ffmpeg",
                "-y",
                *inputs,
                "-filter_complex",
                filter_complex,
                "-map",
                video_label,
                "-map",
                audio_label,
                *encoder_args_for_plan(plan),
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

        plan, cmd = hardware_command_for_plan(
            build, self.settings.encoding_plan, video_label=video_label
        )
        logger.info(
            "Encoding final output with %s (%s)",
            plan.encoder,
            "HDR" if plan.hdr else "SDR",
        )

        total_duration = self.prober.estimate_duration(clips)
        logger.debug(f"Running assembly: {' '.join(cmd)}")
        return _run_ffmpeg_with_progress(cmd, total_duration, progress_callback)
