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
    normalize_audio: bool = True,
    privacy_mode: bool = False,
) -> str:
    """Build FFmpeg filter_complex string for audio crossfade/concat chain.

    Matches filter_builder.build_audio_prep_filters():
    - loudnorm per clip (EBU R128, I=-16, TP=-1.5, LRA=11)
    - Privacy muffle (lowpass=f=200 — makes speech unintelligible)
    - Title screens get null audio source
    - aresample=async=1 + apad for duration safety
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

        if is_title:
            # Title screens have no audio — generate silence
            filter_parts.append(
                f"anullsrc=r=48000:cl=stereo,atrim=0:{clip.duration},{audio_format}[a{i}]"
            )
        else:
            filter_parts.append(
                f"[{i}:a]{audio_format},aresample=async=1,"
                f"asetpts=PTS-STARTPTS{clip_loudnorm}{privacy_muffle},"
                f"apad=whole_dur={clip.duration},atrim=0:{clip.duration}[a{i}]"
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
    normalize_audio: bool = True,
    privacy_mode: bool = False,
) -> None:
    """Extract audio from clips and mix with crossfade transitions.

    Runs a single FFmpeg command with audio-only inputs and acrossfade filters.
    Memory usage is negligible (audio is tiny compared to video frames).
    Output bitrate matches the highest source bitrate (no quality downgrade).
    Applies loudnorm and privacy muffle matching the old filter graph pipeline.

    When privacy_mode is on, audio is pre-processed with segment-wise waveform
    reversal (makes speech unintelligible) before the FFmpeg lowpass mumble filter.
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
        audio_src = str(reversed_paths[0]) if reversed_paths else str(clips[0].path)
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

    # WHY: Title screens have no audio file — we generate silence via lavfi.
    # Use -f lavfi for title screen inputs, -i for real clips.
    inputs: list[str] = []
    rev_idx = 0
    for clip in clips:
        is_title = getattr(clip, "is_title_screen", False)
        if is_title:
            inputs.extend(["-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={clip.duration}"])
        elif reversed_paths:
            inputs.extend(["-i", str(reversed_paths[rev_idx])])
            rev_idx += 1
        else:
            inputs.extend(["-i", str(clip.path)])

    filter_complex = _build_audio_filter_graph(
        clips, transitions, fade_duration, normalize_audio, privacy_mode
    )

    cmd = [
        "ffmpeg",
        "-y",
        *inputs,
        "-filter_complex",
        filter_complex,
        "-map",
        "[aout]",
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

    Pads or trims audio to match video duration to prevent desync from
    frame count rounding differences between video and audio passes.
    """
    video_dur = _probe_duration(video_path)
    # WHY: Audio and video passes produce slightly different durations due to
    # frame count rounding. Use apad+atrim to force audio to exact video length.
    # apad extends if audio is shorter, atrim trims if longer.
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(audio_path),
        "-c:v",
        "copy",
        "-af",
        f"apad=whole_dur={video_dur},atrim=0:{video_dur}",
        "-c:a",
        "aac",
        "-b:a",
        "320k",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)  # noqa: S603, S607
    if result.returncode != 0:
        raise RuntimeError(f"Muxing failed: {result.stderr[-500:]}")
