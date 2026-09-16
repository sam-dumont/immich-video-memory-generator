"""Render 2024 Live Photo bursts with metadata trims and measured-content trims.

For #1012 visual review: pick burst candidates (Cyprus included), align each
burst's companions by frame correlation instead of the midpoint shutter guess,
render both stitch plans per burst stacked in one side-by-side video, and
measure how much content each rendered join actually rewinds or skips.

Outputs under --output:
  review/<slug>.mp4      top half: metadata plan; bottom half: aligned plan
  report.md              per-burst joins table for the run's diagnostics
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from immich_memories.api.models import Asset, AssetType
from immich_memories.api.sync_client import SyncImmichClient
from immich_memories.config_loader import get_config
from immich_memories.processing.live_photo_merger import LivePhotoCluster
from immich_memories.processing.stitch_alignment import (
    PROBE_FPS,
    aligned_trims,
    companion_frames,
    pairwise_clock_offset,
)

_RENDER_FPS = 30
_RENDER_HEIGHT = 270
# Label the two halves; drawtext needs an explicit fontfile on macOS.
_FONT = (
    Path(__file__).resolve().parents[1]
    / "src/immich_memories/titles/bundled_fonts/outfit/latin-700-normal.ttf"
)


def find_bursts(client: SyncImmichClient, year: int, wanted: int) -> list[list[dict]]:
    """Multi-member Live Photo bursts, Cyprus first, then spread across the year."""
    assets: list[dict] = []
    page = 1
    while True:
        result = client.search_metadata(
            asset_type=AssetType("IMAGE"),
            taken_after=datetime(year, 1, 1, tzinfo=UTC),
            taken_before=datetime(year + 1, 1, 1, tzinfo=UTC),
            page=page,
            size=500,
        )
        items = result.assets.items
        assets.extend(item.model_dump(by_alias=True, mode="json") for item in items)
        if result.assets.next_page is None:
            break
        page += 1
    live = [a for a in assets if a.get("livePhotoVideoId")]
    live.sort(key=lambda a: a["fileCreatedAt"])
    clusters: list[list[dict]] = []
    group: list[dict] = []
    for asset in live:
        taken = datetime.fromisoformat(asset["fileCreatedAt"].replace("Z", "+00:00"))
        if (
            group
            and (
                taken - datetime.fromisoformat(group[-1]["fileCreatedAt"].replace("Z", "+00:00"))
            ).total_seconds()
            > 10
        ):
            if len(group) >= 2:
                clusters.append(group)
            group = []
        group.append(asset)
    if len(group) >= 2:
        clusters.append(group)

    def place(a: dict) -> str:
        exif = a.get("exifInfo") or {}
        return f"{exif.get('city') or ''} {exif.get('country') or ''}".casefold()

    # Only bursts whose companions can actually overlap: a shutter gap wider
    # than the two files' half-durations has no shared content to align.
    overlapping = []
    for cluster in clusters:
        gaps_ok = True
        for a, b in zip(cluster, cluster[1:], strict=False):
            gap = (
                datetime.fromisoformat(b["fileCreatedAt"].replace("Z", "+00:00"))
                - datetime.fromisoformat(a["fileCreatedAt"].replace("Z", "+00:00"))
            ).total_seconds()
            halves = []
            for asset in (a, b):
                try:
                    companion = client.get_asset(asset["livePhotoVideoId"])
                except Exception:  # noqa: BLE001 — metadata read failure just drops the candidate
                    halves = None
                    break
                duration = companion.duration_seconds or 0.0
                halves.append(duration / 2)
            if halves is None or gap >= sum(halves):
                gaps_ok = False
                break
        if gaps_ok:
            overlapping.append(cluster)
    # Cyprus was the reported repro; keep a slice of it and spread the rest
    # across the year so the review is not one trip's lighting.
    cyprus = [c for c in overlapping if any("cyprus" in place(a) for a in c)]
    cyprus.sort(key=lambda c: c[0]["fileCreatedAt"])
    cyprus_stride = max(1, len(cyprus) // max(1, min(10, wanted)))
    cyprus_picked = cyprus[::cyprus_stride][: min(10, wanted)]
    cyprus_ids = {a["id"] for c in cyprus for a in c}
    rest = [c for c in overlapping if not any(a["id"] in cyprus_ids for a in c)]
    # Spread the rest evenly across the year.
    rest.sort(key=lambda c: c[0]["fileCreatedAt"])
    remaining = wanted - len(cyprus_picked)
    stride = max(1, len(rest) // max(1, remaining))
    picked = cyprus_picked + rest[::stride][:remaining]
    return picked[:wanted]


def _display_size(path: Path, height: int = _RENDER_HEIGHT) -> tuple[int, int]:
    """The source's own aspect ratio at a display height, width even for h264."""
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    native_w, native_h = (int(value) for value in result.stdout.strip().split(","))
    width = round(height * native_w / native_h)
    return width + width % 2, height


def render_stitch(paths: list[Path], trims: list[tuple[float, float]], target: Path) -> None:
    """Concatenate trimmed segments, re-encoded small, video only.

    Zero-length windows carry lineage but display nothing, exactly like the
    render material's own `segments` view. The frame keeps the sources' own
    aspect ratio; nothing here decides what shape the video is.
    """
    pairs = [
        (path, (start, end)) for path, (start, end) in zip(paths, trims, strict=True) if end > start
    ]
    width, height = _display_size(pairs[0][0])
    with tempfile.TemporaryDirectory(prefix="stitch-render-") as directory:
        segments = []
        for index, (path, (start, end)) in enumerate(pairs):
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
                    f"scale={width}:{height},fps={_RENDER_FPS},setpts=PTS-STARTPTS",
                    "-an",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-crf",
                    "20",
                    "-y",
                    str(segment),
                ],
                capture_output=True,
                check=True,
            )
            segments.append(segment)
        concat_list = Path(directory) / "list.txt"
        concat_list.write_text("".join(f"file '{seg}'\n" for seg in segments))
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
                str(concat_list),
                "-c",
                "copy",
                "-y",
                str(target),
            ],
            capture_output=True,
            check=True,
        )


def stack_vertical(top: Path, bottom: Path, target: Path, gap: int = 8) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(top),
            "-i",
            str(bottom),
            "-filter_complex",
            f"[0:v]drawtext=fontfile={_FONT}:text=TOP metadata plan:"
            f"x=8:y=6:fontsize=14:fontcolor=white:box=1:boxcolor=black@0.7[t];"
            f"[1:v]drawtext=fontfile={_FONT}:text=BOTTOM aligned:"
            f"x=8:y=6:fontsize=14:fontcolor=white:box=1:boxcolor=black@0.7[b];"
            f"[t][b]vstack=inputs=2,pad=iw+{gap}:{_RENDER_HEIGHT * 2 + gap}:{gap // 2}:{gap // 2}:color=black",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-y",
            str(target),
        ],
        capture_output=True,
        check=True,
    )


def join_jump(path: Path, join_seconds: float, *, window: float = 0.4) -> tuple[float, float]:
    """(rewind seconds, match error) at one join of a rendered stitch.

    Compares the strip after the join against strips slid back before the
    join: a match at distance 0 is continuous content; a match at distance r
    means the stitch rewound r seconds. A high error at every distance means
    the join skipped to new content instead.
    """
    frames = companion_frames(path.read_bytes(), fps=_RENDER_FPS)
    length = int(window * _RENDER_FPS)
    centre = int(join_seconds * _RENDER_FPS)
    if centre - 3 * length < 0 or centre + length > len(frames):
        return 0.0, 255.0  # not enough material either side to measure
    after = frames[centre : centre + length]
    best_error, best_distance = 255.0, 0.0
    for distance in range(0, 2 * length + 1):
        before = frames[centre - distance - length : centre - distance]
        error = float(np.abs(after - before).mean())
        if error < best_error:
            best_error, best_distance = error, distance / _RENDER_FPS
    return best_distance, best_error


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, default=2024)
    parser.add_argument("--wanted", type=int, default=30)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    config = get_config()
    client = SyncImmichClient(
        base_url=config.immich.url,
        api_key=config.immich.api_key,
        api_version=config.immich.api_version,
    )
    try:
        bursts = find_bursts(client, args.year, args.wanted)
    finally:
        client.close()
    review_dir = args.output / "review"
    review_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        "| burst | members | plan seconds | aligned seconds | measured | metadata join rewinds | aligned join rewinds |",
        "|---|---|---|---|---|---|",
    ]
    for index, burst in enumerate(bursts):
        slug = f"{burst[0]['fileCreatedAt'][:10]}-{burst[0]['id'][:8]}"
        city = (burst[0].get("exifInfo") or {}).get("city") or "?"
        print(f"[{index + 1}/{len(bursts)}] {slug} ({city}, {len(burst)} members)")
        # A shared album can hold two stills of one Live Photo; the video is
        # offered once, so the later still leaves the stitch to the plan.
        unique: dict[str, dict] = {}
        for asset in burst:
            unique.setdefault(asset["livePhotoVideoId"], asset)
        burst = list(unique.values())
        if len(burst) < 2:
            print(f"    skipped: {len(unique)} unique companion video(s)")
            continue
        try:
            payloads = {
                a["livePhotoVideoId"]: client.get_video_playback(a["livePhotoVideoId"])
                for a in burst
            }
        except Exception as error:  # noqa: BLE001 — one unreachable library must not end the study
            lines.append(
                f"| {slug} | {len(burst)} | download failed: {type(error).__name__} | | | | |"
            )
            continue
        frames = {vid: companion_frames(payload) for vid, payload in payloads.items()}
        durations = [len(frames[a["livePhotoVideoId"]]) / PROBE_FPS for a in burst]
        video_ids = [a["livePhotoVideoId"] for a in burst]
        stills = [
            Asset(
                id=f"still-{i}",
                type=AssetType.IMAGE,
                fileCreatedAt=datetime.fromisoformat(a["fileCreatedAt"].replace("Z", "+00:00")),
                fileModifiedAt=datetime.fromisoformat(a["fileCreatedAt"].replace("Z", "+00:00")),
                updatedAt=datetime.fromisoformat(a["fileCreatedAt"].replace("Z", "+00:00")),
                live_photo_video_id=vid,
                originalFileName=a.get("originalFileName") or f"still-{i}.jpg",
            )
            for i, (a, vid) in enumerate(zip(burst, video_ids, strict=True))
        ]
        cluster = LivePhotoCluster(
            assets=stills,
            clip_durations={f"still-{i}": d for i, d in enumerate(durations)},
        )
        trims_meta = cluster.trim_points()
        measured: list[float | None] = []
        for a, b in zip(video_ids, video_ids[1:], strict=False):
            offset = pairwise_clock_offset(frames[a], frames[b])
            measured.append(offset.seconds if offset else None)
        measured_count = sum(1 for value in measured if value is not None)
        # Measured-only: a burst with any unmeasurable join is refused a motion
        # offer rather than stitched on the midpoint guess (#1012).
        trims_aligned = (
            aligned_trims(trims_meta, durations, [value for value in measured if value is not None])
            if measured and all(value is not None for value in measured)
            else None
        )
        with tempfile.TemporaryDirectory(prefix="stitch-study-") as directory:
            work = Path(directory)
            files = []
            for vid, payload in payloads.items():
                path = work / f"{vid}.mp4"
                path.write_bytes(payload)
                files.append(path)
            old = work / "old.mp4"
            render_stitch(files, trims_meta, old)
            if trims_aligned is not None:
                new = work / "new.mp4"
                render_stitch(files, trims_aligned, new)
            else:
                new = None
            old_jumps = [
                join_jump(old, sum(e - s for s, e in trims_meta[:i]))
                for i in range(1, len(trims_meta))
            ]
            new_jumps = (
                [
                    join_jump(new, sum(e - s for s, e in trims_aligned[:i]))
                    for i in range(1, len(trims_aligned))
                ]
                if new is not None
                else None
            )
            plan_seconds = round(sum(e - s for s, e in trims_meta), 3)
            aligned_seconds = (
                round(sum(e - s for s, e in trims_aligned), 3) if trims_aligned else None
            )

            def fmt(jumps: list[tuple[float, float]] | None) -> str:
                if jumps is None:
                    return "unmeasurable"
                return ", ".join(f"{distance:.2f}s (err {error:.0f})" for distance, error in jumps)

            measured_note = f"{measured_count}/{len(measured)} joins measured"

            lines.append(
                f"| {slug} ({city}) | {len(burst)} | {plan_seconds} | {aligned_seconds or '—'} "
                f"| {measured_note} | {fmt(old_jumps)} | {fmt(new_jumps)} |"
            )
            if new is not None:
                stack_vertical(old, new, review_dir / f"{slug}.mp4")
            else:
                old.replace(review_dir / f"{slug}-metadata-only.mp4")
    (args.output / "report.md").write_text("\n".join(lines) + "\n")
    print(
        f"wrote {args.output / 'report.md'} and {len(list(review_dir.glob('*.mp4')))} review videos"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
