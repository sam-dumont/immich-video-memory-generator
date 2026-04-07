"""Audio extraction, mixing, and muxing for the streaming assembler."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def _build_audio_filter_graph(
    clips: list,
    transitions: list[str],
    fade_duration: float,
    fps: int,
    normalize_audio: bool = True,
    privacy_mode: bool = False,
) -> str:
    """Build FFmpeg filter_complex string for audio crossfade/concat chain.

    Uses frame-aligned durations (int(dur * fps) / fps) so audio timing
    matches the video frame count exactly — prevents cumulative drift.
    """
    audio_format = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
    loudnorm = ",loudnorm=I=-16:TP=-1.5:LRA=11" if normalize_audio else ""
    # WHY: lowpass=300 on top of segment-wise reversal creates a warm "mumble" —
    # you hear people talking but can't understand words. The reversal destroys
    # phoneme order; the lowpass removes remaining high-freq consonant artifacts.
    privacy_muffle = ",lowpass=f=300" if privacy_mode else ""

    filter_parts: list[str] = []
    for i, clip in enumerate(clips):
        is_title = getattr(clip, "is_title_screen", False)
        clip_loudnorm = loudnorm if not is_title else ""
        # WHY: The video consumes int(clip.duration * fps) frames per clip.
        # Audio must match this exact frame-aligned duration, not clip.duration.
        # Without this, int() truncation loses ~0.017s/clip → ~1.2s drift at 70 clips.
        frame_dur = int(clip.duration * fps) / fps

        if is_title:
            filter_parts.append(
                f"anullsrc=r=48000:cl=stereo,atrim=0:{frame_dur},{audio_format}[a{i}]"
            )
        else:
            # WHY: apad/atrim sandwiches loudnorm on BOTH sides.
            # Before: loudnorm one-pass mode loses ~35ms/clip at boundaries
            #   (cumulative: ~2.5s over 70 clips if only trimmed before).
            # After: loudnorm's limiter release adds a small silence tail
            #   (~25ms/clip, only visible with concat transitions, not acrossfade).
            # Double atrim guarantees exact frame-aligned duration regardless.
            filter_parts.append(
                f"[{i}:a]{audio_format},aresample=async=1,"
                f"asetpts=PTS-STARTPTS,"
                f"apad=whole_dur={frame_dur},atrim=0:{frame_dur}"
                f"{clip_loudnorm}{privacy_muffle},"
                f"atrim=0:{frame_dur}[a{i}]"
            )

    current_label = "a0"
    for i, transition in enumerate(transitions):
        next_label = f"a{i + 1}"
        out_label = f"mix{i}" if i < len(transitions) - 1 else "aout"
        if transition == "fade":
            filter_parts.append(
                f"[{current_label}][{next_label}]"
                f"acrossfade=d={fade_duration}:c1=tri:c2=tri[{out_label}]"
            )
        else:
            filter_parts.append(f"[{current_label}][{next_label}]concat=n=2:v=0:a=1[{out_label}]")
        current_label = out_label

    return ";".join(filter_parts)


def _probe_max_audio_bitrate(clips: list) -> str:
    """Probe clips for highest audio bitrate. Returns e.g. "256k".

    Falls back to 192k if probing fails (reasonable for iPhone/modern cameras).
    """
    max_bitrate = 0
    for clip in clips:
        try:
            result = subprocess.run(  # noqa: S603, S607
                [
                    "ffprobe",
                    "-v",
                    "quiet",
                    "-select_streams",
                    "a:0",
                    "-show_entries",
                    "stream=bit_rate",
                    "-of",
                    "csv=p=0",
                    str(clip.path),
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                bitrate = int(result.stdout.strip())
                max_bitrate = max(max_bitrate, bitrate)
        except (ValueError, subprocess.TimeoutExpired):
            continue

    if max_bitrate <= 0:
        return "192k"
    # Round up to nearest standard AAC bitrate
    kbps = max_bitrate // 1000
    for standard in (96, 128, 160, 192, 256, 320):
        if kbps <= standard:
            return f"{standard}k"
    return "320k"


def extract_and_mix_audio(
    clips: list,
    transitions: list[str],
    output_path: Path,
    fade_duration: float = 0.5,
    fps: int = 30,
    normalize_audio: bool = True,
    privacy_mode: bool = False,
    pre_extracted_audio: list[Path] | None = None,
    video_duration: float | None = None,
) -> None:
    """Extract audio from clips and mix with crossfade transitions.

    Runs a single FFmpeg command with audio-only inputs and acrossfade filters.
    Memory usage is negligible (audio is tiny compared to video frames).
    Output bitrate matches the highest source bitrate (no quality downgrade).
    Applies loudnorm and privacy muffle matching the old filter graph pipeline.

    When privacy_mode is on, audio is pre-processed with segment-wise waveform
    reversal (makes speech unintelligible) before the FFmpeg lowpass mumble filter.

    When pre_extracted_audio is provided, uses those WAV files instead of
    reading audio from the original clip files — avoids a redundant decode pass
    and guarantees audio/video timing alignment.
    """
    audio_bitrate = _probe_max_audio_bitrate(clips)
    logger.info(f"Audio output bitrate: {audio_bitrate} (matched to source max)")

    # WHY: Segment-wise reversal reverses audio in 200ms chunks, destroying
    # phoneme order and making speech unintelligible while preserving rhythm.
    # The reversed audio is saved to temp WAVs that replace clip inputs.
    reversed_paths: list[Path] = []
    if privacy_mode:
        reversed_paths = _preprocess_privacy_audio(clips, output_path.parent)

    if len(clips) == 1:
        audio_src = _resolve_single_clip_audio(clips[0], reversed_paths, pre_extracted_audio)
        result = subprocess.run(  # noqa: S603, S607
            [
                "ffmpeg",
                "-y",
                "-i",
                audio_src,
                "-vn",
                "-c:a",
                "aac",
                "-b:a",
                audio_bitrate,
                str(output_path),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        _cleanup_temp_files(reversed_paths)
        if result.returncode != 0:
            raise RuntimeError(f"Audio extraction failed: {result.stderr[-500:]}")
        return

    # WHY: Build FFmpeg inputs from the best available audio source per clip.
    # Pre-extracted WAVs (from video decode pass) have exact frame-level timing;
    # reversed paths are privacy-processed; original clip paths are the fallback.
    inputs: list[str] = []
    rev_idx = 0
    for i, clip in enumerate(clips):
        is_title = getattr(clip, "is_title_screen", False)
        has_pre = (
            pre_extracted_audio
            and not privacy_mode
            and i < len(pre_extracted_audio)
            and pre_extracted_audio[i].name
            and pre_extracted_audio[i].exists()
        )
        if has_pre and pre_extracted_audio:
            inputs.extend(["-i", str(pre_extracted_audio[i])])
        elif is_title:
            inputs.extend(["-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={clip.duration}"])
        elif reversed_paths:
            inputs.extend(["-i", str(reversed_paths[rev_idx])])
            rev_idx += 1
        else:
            inputs.extend(["-i", str(clip.path)])

    filter_complex = _build_audio_filter_graph(
        clips,
        transitions,
        fade_duration,
        fps,
        normalize_audio,
        privacy_mode,
    )

    # WHY: Clamp final audio to video duration INSIDE the filter graph,
    # so the AAC encode produces the exact right length. This avoids
    # re-encoding in the mux step (double AAC encode adds ~200ms of
    # priming delay that varies by hardware encoder).
    if video_duration:
        filter_complex += f";[aout]apad=whole_dur={video_duration},atrim=0:{video_duration}[afinal]"
        map_label = "[afinal]"
    else:
        map_label = "[aout]"

    cmd = [
        "ffmpeg",
        "-y",
        *inputs,
        "-filter_complex",
        filter_complex,
        "-map",
        map_label,
        "-c:a",
        "aac",
        "-b:a",
        audio_bitrate,
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)  # noqa: S603
    _cleanup_temp_files(reversed_paths)
    if result.returncode != 0:
        raise RuntimeError(f"Audio mixing failed: {result.stderr[-500:]}")


def _resolve_single_clip_audio(
    clip: object, reversed_paths: list[Path], pre_extracted: list[Path] | None
) -> str:
    """Pick the audio source path for a single-clip assembly."""
    is_title = getattr(clip, "is_title_screen", False)
    if reversed_paths:
        return str(reversed_paths[0])
    if pre_extracted and not is_title and pre_extracted[0].name:
        return str(pre_extracted[0])
    return str(getattr(clip, "path", ""))


def _preprocess_privacy_audio(clips: list, work_dir: Path) -> list[Path]:
    """Pre-process non-title clip audio with segment-wise reversal.

    Returns list of WAV paths (one per non-title clip) in clip order.
    """
    from immich_memories.processing.privacy_audio import apply_privacy_audio

    paths: list[Path] = []
    for i, clip in enumerate(clips):
        if getattr(clip, "is_title_screen", False):
            continue
        out = work_dir / f".privacy_audio_{i}.wav"
        apply_privacy_audio(clip.path, out)
        paths.append(out)
    return paths


def _cleanup_temp_files(paths: list[Path]) -> None:
    for p in paths:
        p.unlink(missing_ok=True)
        # Also clean up the intermediate .raw.wav if it wasn't deleted
        p.with_suffix(".raw.wav").unlink(missing_ok=True)


def _probe_duration(path: Path) -> float:
    """Get actual duration of a media file via ffprobe."""
    result = subprocess.run(  # noqa: S603, S607
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    return float(result.stdout.strip()) if result.returncode == 0 else 0.0


def mux_video_audio(
    video_path: Path,
    audio_path: Path,
    output_path: Path,
) -> None:
    """Mux video and audio streams into final output.

    Uses -c:a copy to avoid double AAC encoding. Re-encoding audio in the
    mux step adds ~200ms of priming sample drift (encoder-dependent), which
    would require a hardware-specific offset to compensate. Stream copy
    preserves the exact timing from the filter graph.
    """
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(audio_path),
        "-c:v",
        "copy",
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)  # noqa: S603, S607
    if result.returncode != 0:
        raise RuntimeError(f"Muxing failed: {result.stderr[-500:]}")
