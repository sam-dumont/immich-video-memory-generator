"""Pre-rendered content backgrounds for the title and ending screens."""

from __future__ import annotations

import logging
import subprocess
from collections import deque
from collections.abc import Iterator
from pathlib import Path

from immich_memories.processing.assembly_config import AssemblyClip, AssemblySettings
from immich_memories.processing.encoding_plan import HdrTransfer
from immich_memories.processing.ffmpeg_prober import FFmpegProber
from immich_memories.processing.ffmpeg_runner import write_frames_to_ffmpeg
from immich_memories.processing.hdr_utilities import _get_colorspace_filter
from immich_memories.titles.ffmpeg_pipe import StderrDrain

logger = logging.getLogger(__name__)


class TitleBackgroundRenderer:
    """Renders the content clips that the title and ending screens reveal into.

    The title screen deblurs out of the first clip and the ending screen runs
    the last clip backwards in slow motion, so both need that clip already put
    through the assembly pipeline — same rotation, same scale mode, same HDR
    conversion, same canvas.
    """

    def __init__(self, settings: AssemblySettings, prober: FFmpegProber) -> None:
        self.settings = settings
        self.prober = prober

    def _encode_cmd(
        self,
        output_path: Path,
        target_w: int,
        target_h: int,
        fps: int,
        hdr_type: str | None,
    ) -> list[str]:
        """Build the rawvideo-to-file command both pre-renders encode with."""
        from immich_memories.processing.clip_encoder import encoder_args_for_plan

        plan = self.settings.encoding_plan
        encoder_args = encoder_args_for_plan(plan)
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
                ("smpte2084" if plan.target_transfer is HdrTransfer.PQ else "arib-std-b67"),
                "-colorspace",
                "bt2020nc",
            ]
        cmd = [
            "ffmpeg", "-y",
            "-f", "rawvideo", "-pix_fmt", pix_fmt,
            "-s", f"{target_w}x{target_h}", "-r", str(fps),
            *input_color_args,
            "-i", "pipe:0",
            "-vf", _get_colorspace_filter(hdr_type or "sdr").removeprefix(","),
            *encoder_args,
            "-an", "-movflags", "+faststart",
            str(output_path),
        ]  # fmt: skip
        return cmd

    def render_first_clip(
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

        from immich_memories.processing.assembly_engine import (
            create_assembly_context,
        )
        from immich_memories.processing.streaming_assembler import _make_decoder

        plan = self.settings.encoding_plan
        output_path = output_dir / f"first_clip_processed.{plan.container}"
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

        cmd = self._encode_cmd(output_path, target_w, target_h, fps, hdr_type)

        frame_count = 0

        def _frames() -> Iterator[bytes]:
            nonlocal frame_count
            max_frames = fps * 1  # 1 second
            for frame in decoder:
                if frame_count >= max_frames:
                    break
                yield frame.data
                frame_count += 1

        try:
            returncode, stderr = write_frames_to_ffmpeg(cmd, _frames(), wait_timeout=30)
            if returncode == 0 and output_path.exists():
                logger.info(
                    f"Pre-rendered first clip ({frame_count} frames, "
                    f"{target_w}x{target_h}): {output_path}"
                )
                return output_path
            logger.warning(f"Pre-render encode failed: {stderr[-200:]}")
        except (OSError, subprocess.SubprocessError, ValueError) as e:
            logger.warning("Failed to pre-render first clip: %s", e, exc_info=True)
        return None

    def render_last_clip(
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

        from immich_memories.processing.assembly_engine import create_assembly_context
        from immich_memories.processing.streaming_assembler import _make_decoder

        clip = clips[-1]
        plan = self.settings.encoding_plan
        output_path = output_dir / f"last_clip_processed.{plan.container}"
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

        cmd = self._encode_cmd(output_path, target_w, target_h, fps, hdr_type)

        # WHY exactly this many and no more: only the last 0.5s is written,
        # matching what SlowmoBackgroundReader reads — the ending starts where
        # the clip ends, with no going back in time. Buffering two seconds to
        # use half of one cost 4x the memory for nothing, and at 4K/60 a
        # 120-frame rgb24 ring is about 3 GB inside the module whose whole
        # design goal is flat memory.
        source_frames = max(1, fps // 2)

        try:
            tail_frames: deque[bytes] = deque(maxlen=source_frames)
            for frame in decoder:
                tail_frames.append(bytes(frame.data))

            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
            # WHY drained: writing frames to stdin while stderr fills its pipe
            # buffer deadlocks both sides — the failure titles/ffmpeg_pipe.py
            # exists to document. Without it this stalls until the 30s wait
            # expires and the ending is silently dropped.
            drain = StderrDrain(proc).start()
            try:
                for frame_data in tail_frames:
                    proc.stdin.write(frame_data)  # type: ignore[union-attr]
                proc.stdin.close()  # type: ignore[union-attr]
                proc.wait(timeout=30)
            finally:
                stderr_tail = drain.stop()

            if proc.returncode == 0 and output_path.exists():
                logger.info(f"Pre-rendered last clip for ending: {output_path}")
                return output_path
            logger.warning("Pre-render of last clip failed: %s", stderr_tail[-400:])
        except (OSError, subprocess.SubprocessError, ValueError) as e:
            logger.warning("Failed to pre-render last clip: %s", e, exc_info=True)
        return None
