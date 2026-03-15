"""Live Photo temporal clustering and overlap-aware merging.

Apple Live Photos are ~3s each (1.5s before + 1.5s after shutter press).
When taken in rapid succession, the video portions overlap. This module
clusters temporally adjacent Live Photos and computes non-overlapping
trim points so each clip contributes only its unique frames.

Handoff strategy: each clip plays until the next photo's shutter time,
then the next clip picks up from that point in its own timeline. This
puts transitions at the exact moment someone pressed the shutter button
— the natural "moment of interest."

Example: photos at t=0, t=0.5, t=2 (each 3s, half_dur=1.5):
  P1 absolute [-1.5, 1.5] → plays [-1.5, 0.5] → local (0.0, 2.0)
  P2 absolute [-1.0, 2.0] → plays [0.5, 2.0]  → local (1.5, 3.0)
  P3 absolute [0.5, 3.5]  → plays [2.0, 3.5]  → local (1.5, 3.0)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from immich_memories.api.models import Asset

# Default Live Photo clip duration (1.5s before + 1.5s after shutter)
DEFAULT_CLIP_DURATION = 3.0


@dataclass
class LivePhotoCluster:
    """A group of temporally adjacent Live Photos with overlap-aware trim points."""

    assets: list[Asset]
    clip_duration: float = DEFAULT_CLIP_DURATION

    @property
    def count(self) -> int:
        return len(self.assets)

    @property
    def is_burst(self) -> bool:
        """A cluster with 3+ photos is considered a burst."""
        return self.count >= 3

    @property
    def is_favorite(self) -> bool:
        """Cluster is favorite if ANY photo in it is marked as favorite."""
        return any(a.is_favorite for a in self.assets)

    def trim_points(self) -> list[tuple[float, float]]:
        """Compute (start, end) trim points for each clip, eliminating overlap.

        Each clip plays until the next photo's shutter time, then hands off.
        This puts transitions at shutter presses — the moments of interest.

        Absolute-to-local conversion: clip i's absolute start is
        shutter_i - half_dur, so absolute time T → local = T - shutter_i + half_dur.
        """
        if not self.assets:
            return []

        n = len(self.assets)
        half_dur = self.clip_duration / 2.0

        if n == 1:
            return [(0.0, self.clip_duration)]

        timestamps = [a.file_created_at.timestamp() for a in self.assets]

        starts = [0.0] * n
        ends = [self.clip_duration] * n

        for i in range(n - 1):
            # Handoff at next shutter time
            next_shutter = timestamps[i + 1]

            # In clip i's local time: T_local = T_abs - shutter_i + half_dur
            handoff_in_current = next_shutter - timestamps[i] + half_dur
            ends[i] = min(handoff_in_current, self.clip_duration)

            # In clip i+1's local time
            handoff_in_next = next_shutter - timestamps[i + 1] + half_dur
            starts[i + 1] = max(handoff_in_next, 0.0)

            # If there's a gap (no overlap), both clips keep their natural boundaries
            gap = timestamps[i + 1] - timestamps[i]
            if gap >= self.clip_duration:
                ends[i] = self.clip_duration
                starts[i + 1] = 0.0

        return [(starts[i], ends[i]) for i in range(n)]

    @property
    def estimated_duration(self) -> float:
        """Total estimated duration after trimming overlaps."""
        return sum(end - start for start, end in self.trim_points())

    @property
    def video_asset_ids(self) -> list[str]:
        """Get the live photo video IDs for downloading."""
        return [a.live_photo_video_id for a in self.assets if a.live_photo_video_id]


def cluster_live_photos(
    assets: list[Asset],
    merge_window_seconds: float = 10.0,
    clip_duration: float = DEFAULT_CLIP_DURATION,
) -> list[LivePhotoCluster]:
    """Group Live Photo assets into temporal clusters.

    Uses shutter timestamps (file_created_at) to group photos taken within
    merge_window_seconds of each other. Within each cluster, overlap-aware
    trim points ensure no duplicate video frames.
    """
    if not assets:
        return []

    sorted_assets = sorted(assets, key=lambda a: a.file_created_at)

    clusters: list[list[Asset]] = [[sorted_assets[0]]]

    for asset in sorted_assets[1:]:
        prev = clusters[-1][-1]
        gap = (asset.file_created_at - prev.file_created_at).total_seconds()

        if gap <= merge_window_seconds:
            clusters[-1].append(asset)
        else:
            clusters.append([asset])

    return [LivePhotoCluster(assets=c, clip_duration=clip_duration) for c in clusters]


def probe_clip_has_video(clip_path: Path) -> bool:
    """Check if a video clip has at least one decodable video frame."""
    import subprocess

    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-count_frames",
                "-show_entries",
                "stream=nb_read_frames",
                "-of",
                "csv=p=0",
                str(clip_path),
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        frame_count = result.stdout.strip()
        return frame_count.isdigit() and int(frame_count) > 0
    except Exception:
        return False


def filter_valid_clips(
    clip_paths: list[Path],
    trim_points: list[tuple[float, float]],
) -> tuple[list[Path], list[tuple[float, float]]]:
    """Remove clips that have no valid video stream.

    Probes each clip with ffprobe and filters out those with zero frames.
    Returns the surviving clip paths and their corresponding trim points.
    """
    valid_paths: list[Path] = []
    valid_trims: list[tuple[float, float]] = []

    for path, trim in zip(clip_paths, trim_points, strict=True):
        if probe_clip_has_video(path):
            valid_paths.append(path)
            valid_trims.append(trim)

    return valid_paths, valid_trims


def _detect_clip_hdr(clip_path: Path) -> bool:
    """Check if a video clip is HDR by probing color_transfer."""
    import subprocess

    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=color_transfer",
                "-of",
                "csv=p=0",
                str(clip_path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        transfer = result.stdout.strip().lower()
        return transfer in ("arib-std-b67", "smpte2084")
    except Exception:
        return False


def build_merge_command(
    clip_paths: list[Path],
    trim_points: list[tuple[float, float]],
    output: Path,
) -> list[str]:
    """Build an FFmpeg command that trims and concatenates Live Photo clips.

    Each clip is trimmed to its non-overlapping portion (from trim_points),
    then all clips are concatenated into a single output file.

    HDR clips (iPhone HLG) are encoded with libx265 10-bit to preserve
    color metadata. SDR clips use libx264.
    """
    is_hdr = clip_paths and _detect_clip_hdr(clip_paths[0])

    cmd: list[str] = ["ffmpeg", "-y"]

    # Input files
    for path in clip_paths:
        cmd.extend(["-i", str(path)])

    n = len(clip_paths)

    # Build filter_complex
    parts: list[str] = []
    v_labels: list[str] = []
    a_labels: list[str] = []

    for i, (start, end) in enumerate(trim_points):
        v_label = f"v{i}"
        a_label = f"a{i}"
        parts.extend(
            (
                f"[{i}:v]trim=start={start}:end={end},setpts=PTS-STARTPTS[{v_label}]",
                f"[{i}:a]atrim=start={start}:end={end},asetpts=PTS-STARTPTS[{a_label}]",
            )
        )
        v_labels.append(f"[{v_label}]")
        a_labels.append(f"[{a_label}]")

    if n > 1:
        concat_inputs = "".join(f"{v}{a}" for v, a in zip(v_labels, a_labels, strict=True))
        parts.append(f"{concat_inputs}concat=n={n}:v=1:a=1[outv][outa]")
        filter_str = ";\n".join(parts)
        cmd.extend(["-filter_complex", filter_str])
        cmd.extend(["-map", "[outv]", "-map", "[outa]"])
    else:
        filter_str = ";\n".join(parts)
        cmd.extend(["-filter_complex", filter_str])
        cmd.extend(["-map", f"[{v_labels[0].strip('[]')}]", "-map", f"[{a_labels[0].strip('[]')}]"])

    if is_hdr:
        # Preserve HDR: use HEVC 10-bit with color metadata
        cmd.extend(
            [
                "-c:v",
                "libx265",
                "-pix_fmt",
                "yuv420p10le",
                "-color_primaries",
                "bt2020",
                "-color_trc",
                "arib-std-b67",
                "-colorspace",
                "bt2020nc",
                "-tag:v",
                "hvc1",
                "-c:a",
                "aac",
                str(output),
            ]
        )
    else:
        cmd.extend(["-c:v", "libx264", "-c:a", "aac", str(output)])

    return cmd
