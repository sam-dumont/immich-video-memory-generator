"""Audio extraction, mixing, and muxing for the streaming assembler."""

from __future__ import annotations

import contextlib
import logging
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from immich_memories.processing.ffmpeg_runner import (
    ffmpeg_error_excerpt,
    ffmpeg_exit_reason,
    filter_complex_from_file,
)

if TYPE_CHECKING:
    from immich_memories.processing.assembly_config import AssemblyClip
    from immich_memories.processing.probe_cache import ProbeCache

logger = logging.getLogger(__name__)


AUDIO_FORMAT = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"

# WHY: FFmpeg holds ~75 MB resident per loudnorm instance whatever the clip's
# length, and a left-deep acrossfade chain keeps buffering the prefix it has
# already mixed, so its cost climbs faster than linearly past ~100 links. One
# graph carrying every clip therefore scales with the length of the cut: a
# 174-clip mix measures ~13 GB of loudnorm on top of ~2.4 GB of chain buffers,
# and the NAS that reported #782 had 4 GB, so the kernel killed FFmpeg without
# an error line for the log to quote. Rendering clips one at a time and merging
# in bounded groups takes the clip count out of the memory bill entirely.
_MERGE_GROUP_SIZE = 24
_SEGMENT_TIMEOUT_S = 300
_MERGE_TIMEOUT_S = 900


def _segment_chain(
    clip: AssemblyClip,
    fps: int,
    normalize_audio: bool = True,
    privacy_mode: bool = False,
) -> str:
    """Build the filter chain that renders one clip's audio as a segment.

    Uses frame-aligned durations (int(dur * fps) / fps) so audio timing
    matches the video frame count exactly — prevents cumulative drift.
    """
    # WHY: The video consumes int(clip.duration * fps) frames per clip.
    # Audio must match this exact frame-aligned duration, not clip.duration.
    # Without this, int() truncation loses ~0.017s/clip → ~1.2s drift at 70 clips.
    frame_dur = int(clip.duration * fps) / fps
    if getattr(clip, "is_title_screen", False):
        return f"atrim=0:{frame_dur},{AUDIO_FORMAT}"

    # WHY: Single-pass loudnorm can emit NaN samples for short silent inputs
    # (common for generated photo clips). AAC rejects those samples with EINVAL.
    # Replace only non-finite output with digital silence, then undo loudnorm's
    # internal high-rate resampling so every transition receives stable 48 kHz.
    loudnorm = (
        f",loudnorm=I=-16:TP=-1.5:LRA=11,aeval='if(isnan(val(ch)),0,val(ch))':c=same,{AUDIO_FORMAT}"
        if normalize_audio
        else ""
    )
    # WHY: lowpass=300 on top of segment-wise reversal creates a warm "mumble" —
    # you hear people talking but can't understand words. The reversal destroys
    # phoneme order; the lowpass removes remaining high-freq consonant artifacts.
    privacy_muffle = ",lowpass=f=300" if privacy_mode else ""
    # WHY: apad/atrim sandwiches loudnorm on BOTH sides.
    # Before: loudnorm one-pass mode loses ~35ms/clip at boundaries
    #   (cumulative: ~2.5s over 70 clips if only trimmed before).
    # After: loudnorm's limiter release adds a small silence tail
    #   (~25ms/clip, only visible with concat transitions, not acrossfade).
    # Double atrim guarantees exact frame-aligned duration regardless.
    return (
        f"{AUDIO_FORMAT},aresample=async=1,asetpts=PTS-STARTPTS,"
        f"apad=whole_dur={frame_dur},atrim=0:{frame_dur}"
        f"{loudnorm}{privacy_muffle},"
        f"atrim=0:{frame_dur}"
    )


def _build_merge_graph(
    count: int,
    transitions: list[str],
    fade_duration: float,
    tail: str = "",
) -> str:
    """Build the crossfade/concat chain over already-rendered audio segments."""
    if not transitions:
        return f"[0:a]{AUDIO_FORMAT}[aout]{tail}"
    filter_parts = [f"[{i}:a]{AUDIO_FORMAT}[a{i}]" for i in range(count)]
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
    return ";".join(filter_parts) + tail


def _clip_sources(
    clips: list,
    fps: int,
    reversed_paths: list[Path],
    pre_extracted_audio: list[Path] | None,
    privacy_mode: bool,
) -> list[list[str]]:
    """Pick the FFmpeg input arguments carrying each clip's best audio source.

    Pre-extracted WAVs (from the video decode pass) have exact frame-level
    timing; reversed paths are privacy-processed; original clip paths are the
    fallback. A title screen has no source audio and gets generated silence.
    """
    sources: list[list[str]] = []
    rev_idx = 0
    for i, clip in enumerate(clips):
        has_pre = (
            pre_extracted_audio
            and not privacy_mode
            and i < len(pre_extracted_audio)
            and pre_extracted_audio[i].name
            and pre_extracted_audio[i].exists()
        )
        if getattr(clip, "is_title_screen", False):
            frame_dur = int(clip.duration * fps) / fps
            sources.append(["-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={frame_dur}"])
        elif has_pre and pre_extracted_audio:
            sources.append(["-i", str(pre_extracted_audio[i])])
        elif reversed_paths:
            sources.append(["-i", str(reversed_paths[rev_idx])])
            rev_idx += 1
        else:
            sources.append(["-i", str(clip.path)])
    return sources


def _render_segments(
    clips: list,
    sources: list[list[str]],
    work_dir: Path,
    fps: int,
    normalize_audio: bool,
    privacy_mode: bool,
) -> list[Path]:
    """Render each clip's audio to its own frame-aligned WAV.

    One FFmpeg process per clip keeps exactly one loudnorm instance alive at a
    time. The WAVs are float32 at 48 kHz, the sample format the merge graph
    works in, so the round trip through disk changes no samples.
    """
    segments: list[Path] = []
    for i, clip in enumerate(clips):
        segment = work_dir / f"segment_{i:05d}.wav"
        cmd = [
            "ffmpeg",
            "-y",
            *sources[i],
            "-vn",
            "-af",
            _segment_chain(clip, fps, normalize_audio, privacy_mode),
            "-c:a",
            "pcm_f32le",
            str(segment),
        ]
        result = subprocess.run(  # noqa: S603
            cmd, capture_output=True, text=True, timeout=_SEGMENT_TIMEOUT_S
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Audio mixing failed on clip {i} "
                f"({ffmpeg_exit_reason(result.returncode)}): "
                f"{ffmpeg_error_excerpt(result.stderr)}"
            )
        segments.append(segment)
    return segments


def _run_merge(
    segments: list[Path],
    transitions: list[str],
    output_path: Path,
    fade_duration: float,
    tail: str,
    map_label: str,
    encode_args: list[str],
) -> None:
    """Crossfade one bounded group of segments into a single output."""
    inputs: list[str] = []
    for segment in segments:
        inputs.extend(["-i", str(segment)])
    # WHY: the graph grows by ~250 bytes per clip and Linux caps one argv string
    # at 128 KB, so past ~500 clips exec fails with "Argument list too long"
    # before FFmpeg even starts (#780). A script file has no such cap.
    graph_path = output_path.with_suffix(".filter_complex.txt")
    graph_path.write_text(
        _build_merge_graph(len(segments), transitions, fade_duration, tail), encoding="utf-8"
    )
    cmd = [
        "ffmpeg",
        "-y",
        *inputs,
        *filter_complex_from_file(graph_path),
        "-map",
        map_label,
        *encode_args,
        str(output_path),
    ]
    try:
        result = subprocess.run(  # noqa: S603
            cmd, capture_output=True, text=True, timeout=_MERGE_TIMEOUT_S
        )
    finally:
        graph_path.unlink(missing_ok=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"Audio mixing failed ({ffmpeg_exit_reason(result.returncode)}): "
            f"{ffmpeg_error_excerpt(result.stderr)}"
        )


def _merge_segments(
    segments: list[Path],
    transitions: list[str],
    output_path: Path,
    fade_duration: float,
    work_dir: Path,
    *,
    tail: str,
    map_label: str,
    encode_args: list[str],
    depth: int = 0,
) -> None:
    """Crossfade segments together, never more than _MERGE_GROUP_SIZE at once.

    A crossfade only ever touches the tail of its left input and the head of its
    right one, so merging groups and then merging those groups on the
    transitions that straddle their boundaries yields the same samples, and the
    same total duration, as one flat chain over every segment.
    """
    if len(segments) <= _MERGE_GROUP_SIZE:
        _run_merge(segments, transitions, output_path, fade_duration, tail, map_label, encode_args)
        return

    groups: list[Path] = []
    boundaries: list[str] = []
    for start in range(0, len(segments), _MERGE_GROUP_SIZE):
        chunk = segments[start : start + _MERGE_GROUP_SIZE]
        end = start + len(chunk)
        if len(chunk) == 1:
            groups.append(chunk[0])
        else:
            group_out = work_dir / f"group_{depth}_{start:05d}.wav"
            _run_merge(
                chunk,
                transitions[start : end - 1],
                group_out,
                fade_duration,
                "",
                "[aout]",
                ["-c:a", "pcm_f32le"],
            )
            for consumed in chunk:
                consumed.unlink(missing_ok=True)
            groups.append(group_out)
        if end - 1 < len(transitions):
            boundaries.append(transitions[end - 1])

    _merge_segments(
        groups,
        boundaries,
        output_path,
        fade_duration,
        work_dir,
        tail=tail,
        map_label=map_label,
        encode_args=encode_args,
        depth=depth + 1,
    )


def _probe_max_audio_bitrate(clips: list, *, probe_cache: ProbeCache | None = None) -> str:
    """Probe clips for highest audio bitrate. Returns e.g. "256k".

    Falls back to 192k if probing fails (reasonable for iPhone/modern cameras).
    """
    max_bitrate = 0
    for clip in clips:
        if probe_cache is not None:
            from immich_memories.processing.probe_cache import ProbeError

            with contextlib.suppress(OSError, ProbeError, ValueError):
                max_bitrate = max(max_bitrate, probe_cache.get(clip.path).audio_bitrate)
            continue
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
    probe_cache: ProbeCache | None = None,
) -> None:
    """Extract audio from clips and mix with crossfade transitions.

    Renders each clip's audio to its own WAV, then crossfades those segments
    together in bounded groups, so peak memory does not grow with the clip
    count (#782). Output bitrate matches the highest source bitrate.
    Applies loudnorm and privacy muffle matching the old filter graph pipeline.

    When privacy_mode is on, audio is pre-processed with segment-wise waveform
    reversal (makes speech unintelligible) before the FFmpeg lowpass mumble filter.

    When pre_extracted_audio is provided, uses those WAV files instead of
    reading audio from the original clip files — avoids a redundant decode pass
    and guarantees audio/video timing alignment.
    """
    audio_bitrate = _probe_max_audio_bitrate(clips, probe_cache=probe_cache)
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
            raise RuntimeError(
                f"Audio extraction failed ({ffmpeg_exit_reason(result.returncode)}): "
                f"{ffmpeg_error_excerpt(result.stderr)}"
            )
        return

    sources = _clip_sources(clips, fps, reversed_paths, pre_extracted_audio, privacy_mode)

    # WHY: Clamp final audio to video duration INSIDE the filter graph,
    # so the AAC encode produces the exact right length. This avoids
    # re-encoding in the mux step (double AAC encode adds ~200ms of
    # priming delay that varies by hardware encoder).
    if video_duration:
        tail = f";[aout]apad=whole_dur={video_duration},atrim=0:{video_duration}[afinal]"
        map_label = "[afinal]"
    else:
        tail = ""
        map_label = "[aout]"

    work_dir = output_path.parent / f".audio_segments_{output_path.stem}"
    work_dir.mkdir(parents=True, exist_ok=True)
    try:
        segments = _render_segments(clips, sources, work_dir, fps, normalize_audio, privacy_mode)
        _merge_segments(
            segments,
            transitions,
            output_path,
            fade_duration,
            work_dir,
            tail=tail,
            map_label=map_label,
            encode_args=["-c:a", "aac", "-b:a", audio_bitrate],
        )
    finally:
        _cleanup_temp_files(reversed_paths)
        shutil.rmtree(work_dir, ignore_errors=True)


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


def _probe_duration(path: Path, *, probe_cache: ProbeCache | None = None) -> float:
    """Get actual duration of a media file via ffprobe."""
    if probe_cache is not None:
        from immich_memories.processing.probe_cache import ProbeError

        try:
            return probe_cache.get(path).duration_seconds
        except (OSError, ProbeError, ValueError):
            return 0.0
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
        raise RuntimeError(
            f"Muxing failed ({ffmpeg_exit_reason(result.returncode)}): "
            f"{ffmpeg_error_excerpt(result.stderr)}"
        )
