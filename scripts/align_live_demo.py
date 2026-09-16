"""Rebuild a docs Live Photo demo from its sources, aligned by measured audio.

The published demos carry their own source clips but no stills, so there are no
shutter timestamps to place windows with. Their audio fingerprints say when
each clip starts on the first clip's timeline — the same measurement the merge
path uses — and the burst then plays as one continuous film with handoffs at
the midpoint of the shared content. Reports each join's rewind before and
after (#1012).

    uv run python scripts/align_live_demo.py docs-site/static/demos/live-photos/bike_race
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from immich_memories.processing.live_photo_merger import (  # noqa: E402
    _find_audio_offsets,
)
from immich_memories.processing.stitch_alignment import (  # noqa: E402
    companion_frames,
)

# The measurement primitive inside align_clips_spectrogram, called with no
# shutter timestamps: a demo has companion files but no stills.


def continuous_windows(
    durations: list[float], measured_deltas: list[float]
) -> list[tuple[float, float]]:
    """Windows that play a burst as one continuous film, from measured clocks alone.

    Each handoff sits at the midpoint of the content two files share, so every
    member contributes the footage only it covers and no moment repeats or
    jumps: by construction `end_i = start_{i+1} + delta_i`.
    """
    if len(durations) != len(measured_deltas) + 1:
        raise ValueError("continuous windows need one measured delta per join")
    handoffs: list[float] = []
    for index, delta in enumerate(measured_deltas):
        overlap_end = min(durations[index], delta + durations[index + 1])
        if overlap_end <= delta:
            raise ValueError("consecutive companions do not overlap")
        handoffs.append((delta + overlap_end) / 2)
    windows: list[tuple[float, float]] = []
    for index, duration in enumerate(durations):
        start = handoffs[index - 1] - measured_deltas[index - 1] if index else 0.0
        end = handoffs[index] if index < len(handoffs) else duration
        if start < 0 or end > duration or end <= start:
            raise ValueError("measured clocks leave a companion less than it must show")
        windows.append((start, end))
    return windows


_RENDER_HEIGHT = 270
_RENDER_FPS = 30


def _probe(path: Path) -> dict:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,r_frame_rate",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    values = dict(line.split("=") for line in result.stdout.strip().splitlines())
    num, den = values["r_frame_rate"].split("/")
    return {
        "width": int(values["width"]),
        "height": int(values["height"]),
        "duration": float(values["duration"]),
        "fps": float(num) / float(den),
    }


def measure_rewind_at(path: Path, positions: list[float], *, window: float = 0.4) -> list[float]:
    """How far the content rewinds at each known join position.

    The joins are known because this script placed them; the number that
    matters is how much footage before the join reappears after it. Zero means
    the film plays through without going back on itself.
    """
    frames = companion_frames(path.read_bytes(), fps=_RENDER_FPS)
    length = int(window * _RENDER_FPS)
    rewinds = []
    for position in positions:
        centre = int(position * _RENDER_FPS)
        if centre - 3 * length < 0 or centre + length > len(frames):
            rewinds.append(float("nan"))
            continue
        after = frames[centre : centre + length]
        best, best_distance = 255.0, 0.0
        for distance in range(0, 2 * length + 1):
            before = frames[centre - distance - length : centre - distance]
            error = float(np.abs(after - before).mean())
            if error < best:
                best, best_distance = error, distance / _RENDER_FPS
        rewinds.append(round(best_distance, 2))
    return rewinds


def measure_join_jumps(path: Path, *, window: float = 0.4) -> list[float]:
    """Find the file's cuts by frame-difference spikes and read each rewind.

    A stitch joins two windows of one continuous scene, so a real cut inside
    the file is where content jumps; how far the content after the cut rewinds
    before the join is the number this fix drives to zero.
    """
    frames = companion_frames(path.read_bytes(), fps=_RENDER_FPS)
    diffs = np.abs(np.diff(frames, axis=0)).mean(axis=(1, 2))
    length = int(window * _RENDER_FPS)
    candidates = [
        index
        for index in range(length, len(frames) - length)
        if diffs[index] > diffs[max(0, index - length) : index + length].mean() * 2.5
        and diffs[index] > diffs[index - 1]
        and diffs[index] >= diffs[index + 1]
    ]
    jumps = []
    for index in candidates:
        before = frames[index - length : index]
        best, best_distance = 255.0, 0.0
        for distance in range(0, 2 * length + 1):
            after = frames[index - distance : index - distance + length]
            if len(after) < length:
                continue
            error = float(np.abs(before - after).mean())
            if error < best:
                best, best_distance = error, distance / _RENDER_FPS
        if best < 25.0:
            jumps.append(round(best_distance, 2))
    return jumps


def render(paths: list[Path], windows: list[tuple[float, float]], target: Path, size: dict) -> None:
    """Concatenate the windows at the source's own proportions."""
    width = round(_RENDER_HEIGHT * size["width"] / size["height"])
    width += width % 2
    with tempfile.TemporaryDirectory(prefix="live-demo-") as directory:
        segments = []
        for index, (path, (start, end)) in enumerate(zip(paths, windows, strict=True)):
            segment = Path(directory) / f"seg{index}.mp4"
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-ss",
                    f"{start:.6f}",
                    "-to",
                    f"{end:.6f}",
                    "-i",
                    str(path),
                    "-vf",
                    f"scale={width}:{_RENDER_HEIGHT},fps={size['fps']:.6f},setpts=PTS-STARTPTS",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "slow",
                    "-crf",
                    "20",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "128k",
                    "-y",
                    str(segment),
                ],
                capture_output=True,
                check=True,
            )
            segments.append(segment)
        listing = Path(directory) / "list.txt"
        listing.write_text("".join(f"file '{segment}'\n" for segment in segments))
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(listing),
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                "-y",
                str(target),
            ],
            capture_output=True,
            check=True,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("demo_dir", type=Path)
    parser.add_argument("--dry-run", action="store_true", help="measure without writing merged.mp4")
    args = parser.parse_args()

    sources = sorted(args.demo_dir.glob("source_*.mp4"))
    merged = args.demo_dir / "merged.mp4"
    if len(sources) < 2:
        print(f"{args.demo_dir}: needs at least two source clips")
        return 1
    print(f"{args.demo_dir.name}: {len(sources)} sources, existing {merged.name}")
    print(
        f"  current merged joins (rewind seconds): {measure_join_jumps(merged) if merged.is_file() else '—'}"
    )

    payloads = [source.read_bytes() for source in sources]
    sizes = [_probe(source) for source in sources]
    frames = [companion_frames(payload) for payload in payloads]
    shapes = {frame.shape[1:] for frame in frames}
    if len(shapes) > 1:
        print(f"  refused: members disagree on orientation ({sorted(shapes)})")
        return 1
    durations = [size["duration"] for size in sizes]
    try:
        starts = _find_audio_offsets(sources, durations, None)
    except ImportError:
        print("  refused: audio alignment needs SciPy (uv pip install scipy)")
        return 1
    measured = [round(starts[index + 1] - starts[index], 6) for index in range(len(starts) - 1)]
    print(f"  measured clock deltas: {[round(value, 3) for value in measured]}")

    windows = continuous_windows(durations, measured)
    print(f"  windows: {[(round(start, 3), round(end, 3)) for start, end in windows]}")
    if args.dry_run:
        return 0
    positions, position = [], 0.0
    for start, end in windows[:-1]:
        position += end - start
        positions.append(position)
    with tempfile.TemporaryDirectory(prefix="live-demo-out-") as directory:
        staged = Path(directory) / "merged.mp4"
        render(sources, windows, staged, sizes[0])
        print(f"  new merged rewinds at its joins: {measure_rewind_at(staged, positions)}")
        staged.replace(merged)
    print(f"  wrote {merged}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
