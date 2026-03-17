"""Live Photo temporal clustering and overlap-aware merging.

Spectrogram cross-correlation aligns audio tracks sample-accurately.
Frame correlation aligns video independently (iPhone MOV has ~50ms
audio/video offset). Gap-aware shutter-centered cuts ensure full
timeline coverage with no holes.

Works for ANY phone/camera — uses audio fingerprint, not Apple metadata.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

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
        """A cluster with 2+ photos is considered a burst (pairs are common for quick reactions)."""
        return self.count >= 2

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
    """Check if a video clip has at least one video stream (fast, no frame decoding)."""
    import subprocess

    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_type",
                "-of",
                "csv=p=0",
                str(clip_path),
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return "video" in result.stdout.strip()
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


def probe_clip_has_audio(clip_path: Path) -> bool:
    """Check if a video clip has at least one audio stream."""
    import subprocess

    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=codec_type",
                "-of",
                "csv=p=0",
                str(clip_path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout.strip() == "audio"
    except Exception:
        return False


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


def align_clips_spectrogram(
    clip_paths: list[Path],
    shutter_timestamps: list[float],
    durations: list[float],
) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    """Compute audio-aligned trim points using spectrogram cross-correlation.

    Returns (video_trims, audio_trims) — separate timelines because iPhone MOV
    containers have a ~50ms offset between audio and video tracks.

    Algorithm:
    1. STFT spectrogram fingerprint of each clip's audio
    2. Cross-correlate consecutive pairs to find exact temporal offset
    3. Shutter-centered cuts (handoff at midpoint between shutters)
    4. Gap-aware: extend clips when handoff falls before next clip starts

    Works for ANY phone/camera — uses audio fingerprint, not Apple metadata.
    """

    n = len(clip_paths)
    if n <= 1:
        return [(0.0, durations[0])], [(0.0, durations[0])]

    audio_starts = _find_audio_offsets(clip_paths, durations)
    video_starts = _find_video_offsets(clip_paths, audio_starts)

    shutter_abs = [s - shutter_timestamps[0] for s in shutter_timestamps]
    return (
        _gap_aware_trims(video_starts, shutter_abs, durations),
        _gap_aware_trims(audio_starts, shutter_abs, durations),
    )


def _find_audio_offsets(clip_paths: list[Path], durations: list[float]) -> list[float]:
    """Find when each clip's audio starts using spectrogram cross-correlation."""
    import subprocess
    import tempfile

    import numpy as np
    from scipy.signal import stft

    sr, nfft, hop = 48000, 1024, 256
    audios: list[np.ndarray] = []
    with tempfile.TemporaryDirectory() as td:
        for i, p in enumerate(clip_paths):
            raw = Path(td) / f"a{i}.raw"
            subprocess.run(  # noqa: S603, S607
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(p),
                    "-vn",
                    "-f",
                    "s16le",
                    "-acodec",
                    "pcm_s16le",
                    "-ar",
                    str(sr),
                    "-ac",
                    "1",
                    str(raw),
                ],
                capture_output=True,
                timeout=10,
            )
            if raw.exists() and raw.stat().st_size > 0:
                audios.append(np.fromfile(str(raw), dtype=np.int16).astype(np.float64) / 32768.0)
            else:
                audios.append(np.zeros(int(durations[i] * sr)))

    specs = [np.abs(stft(a, fs=sr, nperseg=nfft, noverlap=nfft - hop)[2]) for a in audios]

    starts = [0.0]
    for i in range(len(specs) - 1):
        offset = _spectrogram_match(specs[i], specs[i + 1], hop, sr)
        starts.append(starts[i] + offset)
    return starts


def _spectrogram_match(spec_a: np.ndarray, spec_b: np.ndarray, hop: int, sr: int) -> float:
    """Find where spec_b's start appears in spec_a using spectral fingerprint."""
    import numpy as np

    tpl = spec_b[:, :20].flatten()
    tpl_norm = float(np.linalg.norm(tpl))
    best_sim, best_pos = -1.0, 0
    for j in range(spec_a.shape[1] - 20):
        w = spec_a[:, j : j + 20].flatten()
        sim = float(np.dot(w, tpl) / (np.linalg.norm(w) * tpl_norm + 1e-10))
        if sim > best_sim:
            best_sim, best_pos = sim, j
    return best_pos * hop / sr


def _find_video_offsets(clip_paths: list[Path], audio_starts: list[float]) -> list[float]:
    """Find when each clip's video starts using frame correlation."""

    fps = 30
    starts = [0.0]
    for i in range(len(clip_paths) - 1):
        frames_a = _extract_grayscale_frames(clip_paths[i], fps)
        frames_b = _extract_grayscale_frames(clip_paths[i + 1], fps)
        offset = _frame_match(frames_a, frames_b, fps)
        if offset is not None:
            starts.append(starts[i] + offset)
        else:
            # Fallback to audio alignment when frames are too similar
            starts.append(starts[i] + (audio_starts[i + 1] - audio_starts[i]))
    return starts


def _extract_grayscale_frames(clip_path: Path, fps: int) -> list:
    """Extract small grayscale frames from a clip for fast comparison."""
    import subprocess
    import tempfile

    import numpy as np

    with tempfile.TemporaryDirectory() as td:
        fd = Path(td) / "frames"
        fd.mkdir()
        subprocess.run(  # noqa: S603, S607
            [
                "ffmpeg",
                "-y",
                "-i",
                str(clip_path),
                "-vf",
                "scale=80:60,format=gray",
                "-r",
                str(fps),
                str(fd / "f%04d.raw"),
            ],
            capture_output=True,
            timeout=30,
        )
        return [
            np.fromfile(str(f), dtype=np.uint8).astype(np.float32) for f in sorted(fd.glob("*.raw"))
        ]


def _frame_match(frames_a: list, frames_b: list, fps: int) -> float | None:
    """Find frame offset of clip B in clip A. Returns None if no strong match."""
    import numpy as np

    offsets = []
    search_start = len(frames_a) // 2
    for j in range(min(10, len(frames_b))):
        best_s, best_k = -1.0, 0
        for k in range(search_start, len(frames_a)):
            if len(frames_a[k]) == len(frames_b[j]):
                s = float(
                    np.dot(frames_a[k], frames_b[j])
                    / (np.linalg.norm(frames_a[k]) * np.linalg.norm(frames_b[j]) + 1e-10)
                )
                if s > best_s:
                    best_s, best_k = s, k
        if best_s > 0.98:
            offsets.append(best_k - j)

    if offsets:
        return float(np.median(offsets)) / fps
    return None


def _gap_aware_trims(
    clip_starts: list[float], shutter_abs: list[float], durations: list[float]
) -> list[tuple[float, float]]:
    """Compute shutter-centered trims that cover gaps in the timeline."""
    n = len(clip_starts)
    trims = []
    for i in range(n):
        if i == 0:
            left = clip_starts[0]
        else:
            left = max((shutter_abs[i - 1] + shutter_abs[i]) / 2, clip_starts[i])
        if i == n - 1:
            right = clip_starts[i] + durations[i]
        else:
            right = max((shutter_abs[i] + shutter_abs[i + 1]) / 2, clip_starts[i + 1])
        ls = max(0.0, left - clip_starts[i])
        le = min(durations[i], right - clip_starts[i])
        trims.append((round(ls, 4), round(le, 4)))
    return trims


def build_merge_command(
    clip_paths: list[Path],
    trim_points: list[tuple[float, float]],
    output: Path,
    *,
    audio_trim_points: list[tuple[float, float]] | None = None,
) -> list[str]:
    """Build an FFmpeg command that trims and merges Live Photo clips.

    Each clip is trimmed to its non-overlapping portion, normalized for
    exposure/white balance consistency, then concatenated with clean cuts.

    If audio_trim_points is provided, audio and video are trimmed independently
    (iPhone MOV containers have ~50ms audio/video offset). A 30ms fade at each
    audio boundary prevents crackling from waveform discontinuities.

    HDR clips (iPhone HLG) are encoded with libx265 10-bit to preserve
    color metadata. SDR clips use libx264.
    """
    is_hdr = bool(clip_paths) and _detect_clip_hdr(clip_paths[0])
    has_audio = all(probe_clip_has_audio(p) for p in clip_paths)
    a_trims = audio_trim_points or trim_points
    n = len(clip_paths)

    cmd: list[str] = ["ffmpeg", "-y"]
    for path in clip_paths:
        cmd.extend(["-i", str(path)])

    parts, v_labels, a_labels = _build_trim_filters(trim_points, a_trims, n, has_audio)
    _build_concat_and_map(cmd, parts, v_labels, a_labels, n, has_audio)

    _append_encoding_args(cmd, is_hdr, has_audio, output)
    return cmd


def _build_trim_filters(
    v_trims: list[tuple[float, float]],
    a_trims: list[tuple[float, float]],
    n: int,
    has_audio: bool,
) -> tuple[list[str], list[str], list[str]]:
    """Build per-clip trim + normalize filter strings."""
    parts: list[str] = []
    v_labels: list[str] = []
    a_labels: list[str] = []
    fade_dur = 0.03  # 30ms anti-crackle fade

    for i, (v_start, v_end) in enumerate(v_trims):
        normalize = ",normalize=smoothing=20:independence=0:strength=0.4"
        fps_filter = ",fps=30" if n > 1 else ""
        parts.append(
            f"[{i}:v]trim=start={v_start}:end={v_end},setpts=PTS-STARTPTS{normalize}{fps_filter}[v{i}]"
        )
        v_labels.append(f"[v{i}]")

        if has_audio:
            a_start, a_end = a_trims[i]
            seg_dur = a_end - a_start
            fade_in = f",afade=t=in:st=0:d={fade_dur}" if i > 0 else ""
            fade_out = (
                f",afade=t=out:st={max(0.01, seg_dur - fade_dur)}:d={fade_dur}" if i < n - 1 else ""
            )
            parts.append(
                f"[{i}:a]atrim=start={a_start}:end={a_end},asetpts=PTS-STARTPTS{fade_in}{fade_out}[a{i}]"
            )
            a_labels.append(f"[a{i}]")

    return parts, v_labels, a_labels


def _build_concat_and_map(
    cmd: list[str],
    parts: list[str],
    v_labels: list[str],
    a_labels: list[str],
    n: int,
    has_audio: bool,
) -> None:
    """Append concat filter and stream mapping to FFmpeg command."""
    if n > 1:
        v_concat = "".join(v_labels)
        parts.append(f"{v_concat}concat=n={n}:v=1:a=0[outv]")
        if has_audio:
            a_concat = "".join(a_labels)
            parts.append(f"{a_concat}concat=n={n}:v=0:a=1[outa]")

    cmd.extend(["-filter_complex", ";\n".join(parts)])

    if n > 1:
        cmd.extend(["-map", "[outv]"])
        if has_audio:
            cmd.extend(["-map", "[outa]"])
    else:
        cmd.extend(["-map", f"[{v_labels[0].strip('[]')}]"])
        if has_audio:
            cmd.extend(["-map", f"[{a_labels[0].strip('[]')}]"])


def _append_encoding_args(cmd: list[str], is_hdr: bool, has_audio: bool, output: Path) -> None:
    """Append video/audio codec arguments to the FFmpeg command."""
    if is_hdr:
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
            ]
        )
    else:
        cmd.extend(["-c:v", "libx264"])

    if has_audio:
        cmd.extend(["-c:a", "aac"])

    cmd.append(str(output))
