"""Streaming video assembler — constant-memory frame blending."""

from __future__ import annotations

import contextlib
import logging
import shutil
import subprocess
import threading
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from immich_memories.processing.clip_caption import ClipCaption, timeline_captions
from immich_memories.processing.clip_encoder import encoder_args_for_plan
from immich_memories.processing.encoding_plan import (
    EncodingPlan,
    HdrTransfer,
    OutputCodec,
    software_fallback_plan,
    uses_hardware_encoder,
)
from immich_memories.processing.ffmpeg_runner import drain_stderr_tail
from immich_memories.processing.hdr_utilities import _get_colorspace_filter
from immich_memories.processing.streaming_audio import (
    _probe_duration,
    extract_and_mix_audio,
    mux_video_audio,
)
from immich_memories.processing.streaming_frame_blender import FrameBlender
from immich_memories.processing.streaming_frame_decoder import make_decoder

if TYPE_CHECKING:
    from immich_memories.processing.probe_cache import ProbeCache

logger = logging.getLogger(__name__)


def _default_streaming_plan() -> EncodingPlan:
    """Concrete SDR/H.264 contract for standalone streaming callers."""
    return EncodingPlan(
        codec=OutputCodec.H264,
        encoder="libx264",
        encoder_args=("-preset", "medium", "-crf", "18"),
        target_transfer=HdrTransfer.NONE,
        tone_map_to_sdr=False,
        pixel_format="yuv420p",
        container="mp4",
        crf=18,
    )


class StreamingEncoderWriteError(RuntimeError):
    """FFmpeg stopped accepting raw frames from the streaming encoder."""


def _notify_effective_plan(
    callback: Callable[[EncodingPlan], None] | None,
    plan: EncodingPlan,
) -> None:
    """Report the plan that actually encoded the artifact when requested."""
    if callback is not None:
        callback(plan)


class StreamingEncoder:
    """Encode raw frames to video via FFmpeg stdin pipe."""

    def __init__(
        self,
        output_path: Path,
        width: int,
        height: int,
        fps: int,
        encoding_plan: EncodingPlan | None = None,
        ffmpeg_command: Sequence[str] = ("ffmpeg",),
    ) -> None:
        self._output_path = output_path
        self._width = width
        self._height = height
        self._fps = fps
        self._encoding_plan = encoding_plan or _default_streaming_plan()
        self._encoder_args = encoder_args_for_plan(self._encoding_plan)
        # WHY: Frames arrive as rgb24 (sRGB). For HDR output, zscale converts
        # sRGB → HLG/PQ on the encoder side. Same pattern as photo pipeline.
        self._target_transfer = self._encoding_plan.target_transfer
        self._ffmpeg_command = tuple(ffmpeg_command)
        self._proc: subprocess.Popen[bytes] | None = None
        self._stderr_tail = bytearray()
        self._stderr_reader: threading.Thread | None = None

    def start(self) -> None:
        """Start the FFmpeg encode process."""
        # WHY: For HDR, data arrives as yuv420p10le (native format, zero conversion).
        # The rawvideo pipe STRIPS color range metadata — without explicit flags,
        # the encoder assumes full range (0-1023) when data is tv range (64-940)
        # = washed out colors. Must tag input with color metadata.
        target_type = (
            self._target_transfer.value if self._target_transfer is not HdrTransfer.NONE else "sdr"
        )
        vf_args = ["-vf", _get_colorspace_filter(target_type).removeprefix(",")]
        input_color_args: list[str] = []
        if self._target_transfer is HdrTransfer.HLG:
            input_color_args = [
                "-color_range",
                "tv",
                "-color_trc",
                "arib-std-b67",
                "-color_primaries",
                "bt2020",
                "-colorspace",
                "bt2020nc",
            ]
        elif self._target_transfer is HdrTransfer.PQ:
            input_color_args = [
                "-color_range",
                "tv",
                "-color_trc",
                "smpte2084",
                "-color_primaries",
                "bt2020",
                "-colorspace",
                "bt2020nc",
            ]

        # WHY: yuv420p10le for HDR (native format, zero conversion), rgb24 for SDR
        input_pix_fmt = "yuv420p10le" if self._encoding_plan.hdr else "rgb24"

        cmd = [
            *self._ffmpeg_command,
            "-y",
            "-nostats",
            "-f",
            "rawvideo",
            "-pix_fmt",
            input_pix_fmt,
            "-s",
            f"{self._width}x{self._height}",
            "-r",
            str(self._fps),
            *input_color_args,
            "-i",
            "pipe:0",
            *vf_args,
            *self._encoder_args,
            "-movflags",
            "+faststart",
            str(self._output_path),
        ]
        self._proc = subprocess.Popen(  # noqa: S603, S607
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        # WHY: FFmpeg <= 6.1 prints stats/warnings from the transcode thread and
        # stops reading stdin once the 64 KB stderr pipe is full — draining
        # only in finish() deadlocks any encode longer than a few minutes.
        self._stderr_tail = bytearray()
        self._stderr_reader = threading.Thread(
            target=drain_stderr_tail,
            args=(self._proc.stderr, self._stderr_tail),
            name="streaming-encoder-stderr",
            daemon=True,
        )
        self._stderr_reader.start()

    def write_frame(self, frame: np.ndarray) -> None:
        """Write one frame to the encoder. Uses memoryview for zero-copy."""
        assert self._proc is not None and self._proc.stdin is not None  # noqa: S101
        # WHY: ndarray.data is a memoryview — avoids copying ~25 MB per 4K frame
        # that .tobytes() would allocate
        try:
            self._proc.stdin.write(frame.data)
        except BrokenPipeError as exc:
            raise StreamingEncoderWriteError("Streaming encoder stopped accepting frames") from exc

    def finish(self) -> None:
        """Close stdin pipe and wait for FFmpeg to finish."""
        if self._proc is None:
            return
        assert self._proc.stdin is not None  # noqa: S101
        with contextlib.suppress(BrokenPipeError):
            self._proc.stdin.close()
        self._proc.wait(timeout=3600)
        if self._stderr_reader is not None:
            self._stderr_reader.join(timeout=30)
        if self._proc.returncode != 0:
            stderr = bytes(self._stderr_tail).decode(errors="replace")
            raise RuntimeError(
                f"Streaming encode failed (exit {self._proc.returncode}): {stderr[-500:]}"
            )


def _estimate_total_frames(
    clips: list, transitions: list[str], fps: int, fade_duration: float
) -> int:
    """Estimate total output frames accounting for crossfade overlap."""
    fade_frames = int(fade_duration * fps)
    total = sum(int(c.duration * fps) for c in clips)
    fade_count = sum(1 for t in transitions if t == "fade")
    return max(1, total - fade_count * fade_frames)


def assemble_streaming(
    clips: list,
    transitions: list[str],
    output_path: Path,
    width: int,
    height: int,
    fps: int,
    fade_duration: float = 0.5,
    encoding_plan: EncodingPlan | None = None,
    ctx: Any | None = None,
    privacy_mode: bool = False,
    date_overlay: bool = False,
    place_overlay: bool = False,
    caption_locale: str = "en",
    scale_mode: str = "blur",
    progress_callback: Callable[[int, int], None] | None = None,
    frame_preview_callback: Callable[[bytes], None] | None = None,
    audio_work_dir: Path | None = None,
    effective_plan_callback: Callable[[EncodingPlan], None] | None = None,
    _allow_runtime_fallback: bool = True,
) -> list[Path]:
    """Assemble clips via streaming frame blending (constant memory).

    Returns list of per-clip audio WAV paths extracted during decoding.
    """
    if len(transitions) != len(clips) - 1:
        raise ValueError(f"Expected {len(clips) - 1} transitions, got {len(transitions)}")

    fade_frames = int(fade_duration * fps)
    total_frames = _estimate_total_frames(clips, transitions, fps, fade_duration)

    captions, caption_font = timeline_captions(clips, date_overlay, place_overlay, caption_locale)
    plan = encoding_plan or _default_streaming_plan()
    hdr_type = plan.target_transfer.value if plan.hdr else None
    encoder = StreamingEncoder(output_path, width, height, fps, encoding_plan=plan)
    blender = FrameBlender(
        sink=encoder,
        width=width,
        height=height,
        is_hdr=hdr_type is not None,
        total_frames=total_frames,
        # WHY: Throttle callbacks to every ~0.5s worth of frames to avoid UI overhead
        report_interval=max(1, fps // 2),
        progress_callback=progress_callback,
        frame_preview_callback=frame_preview_callback,
    )

    def retry_in_software() -> list[Path] | None:
        if not _allow_runtime_fallback or not uses_hardware_encoder(plan):
            return None
        fallback_plan = software_fallback_plan(plan)
        logger.warning(
            "Hardware encoder %s failed; retrying %s streaming assembly in software",
            plan.encoder,
            fallback_plan.codec.value,
        )
        return assemble_streaming(
            clips,
            transitions,
            output_path,
            width,
            height,
            fps,
            fade_duration,
            fallback_plan,
            ctx,
            privacy_mode,
            date_overlay,
            place_overlay,
            caption_locale,
            scale_mode,
            progress_callback,
            frame_preview_callback,
            audio_work_dir,
            effective_plan_callback,
            _allow_runtime_fallback=False,
        )

    try:
        encoder.start()
    except (OSError, subprocess.TimeoutExpired):
        fallback_result = retry_in_software()
        if fallback_result is not None:
            return fallback_result
        raise

    try:
        _encode_clip_sequence(
            clips,
            transitions,
            blender,
            fade_frames,
            width,
            height,
            fps,
            ctx,
            privacy_mode,
            captions,
            caption_font,
            scale_mode,
            hdr_type,
            audio_work_dir=audio_work_dir,
        )
    except StreamingEncoderWriteError:
        with contextlib.suppress(OSError, subprocess.TimeoutExpired, RuntimeError):
            encoder.finish()
        fallback_result = retry_in_software()
        if fallback_result is not None:
            return fallback_result
        raise
    except Exception:  # WHY: cleanup safety net — ensures encoder.finish() on non-encoder errors
        with contextlib.suppress(OSError, subprocess.TimeoutExpired, RuntimeError):
            encoder.finish()
        raise

    try:
        encoder.finish()
    except (OSError, subprocess.TimeoutExpired, RuntimeError):
        fallback_result = retry_in_software()
        if fallback_result is not None:
            return fallback_result
        raise

    if progress_callback:
        progress_callback(total_frames, total_frames)
    _notify_effective_plan(effective_plan_callback, plan)
    logger.info(f"Streaming assembly complete: {len(clips)} clips → {output_path.name}")

    # Collect audio WAV files extracted by FrameDecoder during the encode pass
    audio_paths: list[Path] = []
    if audio_work_dir:
        for clip_idx in range(len(clips)):
            wav = audio_work_dir / f"clip_{clip_idx}_audio.wav"
            if wav.exists():
                audio_paths.append(wav)
            else:
                audio_paths.append(Path())
    return audio_paths


def _encode_clip_sequence(
    clips: list,
    transitions: list[str],
    blender: FrameBlender,
    fade_frames: int,
    width: int,
    height: int,
    fps: int,
    ctx: Any | None,
    privacy_mode: bool,
    captions: list[ClipCaption] | None,
    caption_font: str | None,
    scale_mode: str,
    hdr_type: str | None,
    audio_work_dir: Path | None = None,
) -> int:
    """Encode all clips with transitions, tracking frame count for progress."""
    active_iter: Iterator[np.ndarray] | None = None
    skip_frames = 0

    clip_captions: list[ClipCaption | None] = (
        list(captions) if captions is not None else [None] * len(clips)
    )

    def decoder_for(clip_idx: int) -> Iterator[np.ndarray]:
        return iter(
            make_decoder(
                clips[clip_idx],
                clip_idx,
                width,
                height,
                fps,
                ctx,
                privacy_mode,
                clip_captions[clip_idx],
                caption_font,
                scale_mode,
                hdr_type,
                audio_work_dir=audio_work_dir,
            )
        )

    for clip_idx, clip in enumerate(clips):
        if active_iter is None:
            active_iter = decoder_for(clip_idx)

        clip_frames = int(clip.duration * fps)
        has_fade_out = clip_idx < len(transitions) and transitions[clip_idx] == "fade"
        body_frames = clip_frames - skip_frames - (fade_frames if has_fade_out else 0)

        blender.emit_body(active_iter, body_frames)

        if has_fade_out and clip_idx + 1 < len(clips):
            next_iter = decoder_for(clip_idx + 1)
            blender.emit_crossfade(active_iter, next_iter, fade_frames)
            active_iter = next_iter
            skip_frames = fade_frames
        else:
            active_iter = None
            skip_frames = 0

    # WHY: The last FrameDecoder's FFmpeg process inherits the encoder's
    # stdin pipe FD. If not closed before encoder.finish(), the pipe never
    # sees EOF and the encoder hangs waiting for input. Force-close the
    # last iterator to trigger FrameDecoder.__iter__'s finally block
    # (proc.terminate + wait), ensuring the FD is released.
    if active_iter is not None and hasattr(active_iter, "close"):
        active_iter.close()
    return blender.frames_written


def streaming_assemble_full(
    clips: list,
    transitions: list[str],
    output_path: Path,
    width: int,
    height: int,
    fps: int,
    fade_duration: float = 0.5,
    encoding_plan: EncodingPlan | None = None,
    ctx: Any | None = None,
    normalize_audio: bool = True,
    privacy_mode: bool = False,
    date_overlay: bool = False,
    place_overlay: bool = False,
    caption_locale: str = "en",
    scale_mode: str = "blur",
    progress_callback: Callable[[float, str], None] | None = None,
    frame_preview_callback: Callable[[bytes], None] | None = None,
    probe_cache: ProbeCache | None = None,
    effective_plan_callback: Callable[[EncodingPlan], None] | None = None,
) -> Path:
    """Full streaming assembly: plan-bound video encode + audio mix + mux."""
    plan = encoding_plan or _default_streaming_plan()
    work_dir = output_path.parent / ".streaming_work"
    work_dir.mkdir(parents=True, exist_ok=True)

    video_only = work_dir / f"video.{plan.container}"
    audio_only = work_dir / "audio.m4a"
    audio_work_dir = work_dir / "audio_clips"
    audio_work_dir.mkdir(exist_ok=True)

    try:
        if progress_callback:
            progress_callback(0.07, "Streaming video assembly...")

        # WHY: Scale frame-level progress into [0.07, 0.85) range so the caller
        # sees continuous updates during the heavy encode phase.
        def _frame_progress(frames_done: int, frames_total: int) -> None:
            if progress_callback and frames_total > 0:
                frac = frames_done / frames_total
                scaled = 0.07 + frac * 0.80
                total_secs = frames_total / fps if fps > 0 else 0
                done_secs = frames_done / fps if fps > 0 else 0
                time_done = f"{int(done_secs // 60)}:{int(done_secs % 60):02d}"
                time_total = f"{int(total_secs // 60)}:{int(total_secs % 60):02d}"
                progress_callback(
                    scaled,
                    f"Encoding ({time_done} / {time_total}) — {frac * 100:.0f}%",
                )

        # WHY: Extract audio in the same FFmpeg pass as video decoding.
        # This eliminates the separate audio extraction pass and ensures
        # audio timing matches decoded video frames exactly.
        clip_audio_paths = assemble_streaming(
            clips=clips,
            transitions=transitions,
            output_path=video_only,
            width=width,
            height=height,
            fps=fps,
            fade_duration=fade_duration,
            encoding_plan=plan,
            ctx=ctx,
            privacy_mode=privacy_mode,
            date_overlay=date_overlay,
            place_overlay=place_overlay,
            caption_locale=caption_locale,
            scale_mode=scale_mode,
            progress_callback=_frame_progress,
            frame_preview_callback=frame_preview_callback,
            audio_work_dir=audio_work_dir,
            effective_plan_callback=effective_plan_callback,
        )

        if progress_callback:
            progress_callback(0.85, "Mixing audio...")

        # WHY: Probe actual video duration so the audio filter graph can
        # clamp its output to match. This avoids re-encoding audio in the
        # mux step (which would cause double-AAC priming delay).
        video_dur = _probe_duration(video_only, probe_cache=probe_cache)

        extract_and_mix_audio(
            clips=clips,
            transitions=transitions,
            output_path=audio_only,
            fade_duration=fade_duration,
            fps=fps,
            normalize_audio=normalize_audio,
            privacy_mode=privacy_mode,
            pre_extracted_audio=clip_audio_paths,
            video_duration=video_dur,
            probe_cache=probe_cache,
        )

        if progress_callback:
            progress_callback(0.95, "Muxing final output...")

        mux_video_audio(video_only, audio_only, output_path)

        logger.info(f"Full streaming assembly complete: {len(clips)} clips → {output_path.name}")
        return output_path
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
