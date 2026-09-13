"""Real-FFmpeg check that merging clips in bounded groups changes no samples.

One filter graph per cut held a loudnorm instance for every clip, ~75 MB
resident each whatever the clip's length, on top of an acrossfade chain that
buffers its own accumulated prefix. A 174-clip mix wanted ~15 GB, so on a 4 GB
NAS the kernel killed FFmpeg and left only its progress ticker in the log
(#782). Clips are now rendered one at a time and merged in groups, which is
only sound if a crossfade never reaches past its immediate neighbours: raising
the group size until everything fits in one chain must produce the same audio.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from immich_memories.processing import streaming_audio
from immich_memories.processing.assembly_config import AssemblyClip
from immich_memories.processing.streaming_audio import extract_and_mix_audio

pytestmark = pytest.mark.integration

FPS = 30
CLIP_SECONDS = 2.0
FADE_SECONDS = 0.5
# Enough clips to spill past the group size, so the merge has to recurse.
CLIP_COUNT = streaming_audio._MERGE_GROUP_SIZE + 6


def _write_tones(tmp_path: Path) -> list[Path]:
    """One FFmpeg process with a distinct tone per output, not one per clip."""
    wavs = [tmp_path / f"tone_{i}.wav" for i in range(CLIP_COUNT)]
    command = ["ffmpeg", "-y"]
    for i in range(CLIP_COUNT):
        command += ["-f", "lavfi", "-i", f"sine=frequency={220 + i * 37}:duration={CLIP_SECONDS}"]
    for i, wav in enumerate(wavs):
        command += ["-map", f"{i}:a", "-ar", "48000", "-ac", "2", str(wav)]
    subprocess.run(command, capture_output=True, timeout=120, check=True)  # noqa: S603, S607
    return wavs


def _decoded_pcm(path: Path) -> bytes:
    result = subprocess.run(  # noqa: S603, S607
        ["ffmpeg", "-v", "quiet", "-i", str(path), "-f", "s16le", "-ac", "2", "-ar", "48000", "-"],
        capture_output=True,
        timeout=120,
        check=True,
    )
    return result.stdout


def _mix(wavs: list[Path], out: Path) -> None:
    extract_and_mix_audio(
        clips=[AssemblyClip(path=wav, duration=CLIP_SECONDS) for wav in wavs],
        transitions=["fade"] * (len(wavs) - 1),
        output_path=out,
        fade_duration=FADE_SECONDS,
        fps=FPS,
    )


def test_grouped_merge_matches_one_flat_chain(tmp_path: Path, monkeypatch) -> None:
    wavs = _write_tones(tmp_path)

    grouped = tmp_path / "grouped.m4a"
    _mix(wavs, grouped)

    # A group size past the clip count collapses the merge back to one chain.
    monkeypatch.setattr(streaming_audio, "_MERGE_GROUP_SIZE", CLIP_COUNT * 2)
    flat = tmp_path / "flat.m4a"
    _mix(wavs, flat)

    assert _decoded_pcm(grouped) == _decoded_pcm(flat)


def test_grouped_merge_keeps_the_whole_running_time(tmp_path: Path) -> None:
    wavs = _write_tones(tmp_path)
    out = tmp_path / "grouped.m4a"
    _mix(wavs, out)

    probe = subprocess.run(  # noqa: S603, S607
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", str(out)],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    frame_dur = int(CLIP_SECONDS * FPS) / FPS
    expected = CLIP_COUNT * frame_dur - (CLIP_COUNT - 1) * FADE_SECONDS
    assert abs(float(probe.stdout.strip()) - expected) < 0.05
