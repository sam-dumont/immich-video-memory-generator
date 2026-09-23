"""The frames a video is decided on: eight across its length, not one early preview.

Immich's preview for a video is a single frame near its start, and a clip that shows
something only in its second half was read as if it did not. Measured on four clips:
the preview alone held two of them (0.30, 0.28 on the exposure head); eight frames
sampled evenly held all four (0.93-0.95). The head's answer for a video is therefore
the strongest of its frames -- a hold anywhere in a clip holds the clip -- and never
the mean, which a single ordinary frame would drag back under the cut.

The frames come from the byte-range keyframe reader the motion line already uses
(``processing/playback_keyframes.py``): the MP4 index and a few keyframes rather than
the whole rendition, and no FFmpeg path of its own. Sampling happens here, in the
process that can reach Immich; the detector worker is handed file paths.
"""

from __future__ import annotations

import subprocess
import tempfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path

import httpx

from immich_memories.analysis.editorial_preparation_detectors import MARQO_HEAD
from immich_memories.api.models import Asset
from immich_memories.processing.playback_keyframes import sample_keyframes

# Docling decides what kind of picture a frame is, which the preview already answers,
# so it keeps its one read; only the exposure head is worth eight.
FRAMES = 8
# The detector resizes the short side to 384 and crops there. At 768 the short side of
# 16:9 material still arrives above that, so the crop downscales instead of inventing
# pixels; a taller frame costs the same bytes, only a slightly longer scale.
FRAME_WIDTH = 768
STAGE = "detector_frames"


class DetectorFrames:
    """The videos this pass may read on frames, and the frames themselves.

    Without a playback reader nothing is owed and every source keeps its single
    preview, which is exactly what the run did before there were frames.
    """

    def __init__(
        self,
        assets: Sequence[Asset],
        read_playback: Callable[[str, int, int], tuple[bytes, int]] | None,
    ) -> None:
        self._read = read_playback
        self.video_ids = (
            frozenset(asset.id for asset in assets if asset.is_video)
            if read_playback is not None
            else frozenset()
        )

    @contextmanager
    def sampled(
        self,
        asset_ids: Sequence[str],
        *,
        check: Callable[[], None],
        report: Callable[[str, int, int], None],
        failures: dict[str, str],
        timed: Callable[[str, int], AbstractContextManager[None]],
    ) -> Iterator[dict[str, list[Path]]]:
        """Frames on disk for each video among ``asset_ids``, for as long as the block runs.

        A clip that cannot be read leaves no entry and is named in ``failures``: its
        source falls back to the one preview rather than being banked as unread.
        """
        wanted = [asset_id for asset_id in asset_ids if asset_id in self.video_ids]
        if not wanted:
            yield {}
            return
        with tempfile.TemporaryDirectory(prefix="immich-detector-frames-") as directory:
            paths: dict[str, list[Path]] = {}
            with timed(STAGE, len(wanted)):
                for index, asset_id in enumerate(wanted, 1):
                    check()
                    self._sample(asset_id, Path(directory), paths, failures)
                    report(STAGE, index, len(wanted))
            yield paths

    def _sample(
        self, asset_id: str, directory: Path, paths: dict[str, list[Path]], failures: dict[str, str]
    ) -> None:
        try:
            paths[asset_id] = self._write(asset_id, directory / asset_id)
        except (httpx.HTTPError, OSError, subprocess.SubprocessError, ValueError) as exc:
            failures[f"{STAGE}:{asset_id}"] = f"{type(exc).__name__}: {exc}"

    def _write(self, asset_id: str, workdir: Path) -> list[Path]:
        if self._read is None:  # pragma: no cover - video_ids is empty without a reader
            raise ValueError("no playback reader")
        read = self._read
        workdir.mkdir(parents=True, exist_ok=True)
        sampled = sample_keyframes(
            lambda start, length: read(asset_id, start, length),
            count=FRAMES,
            width=FRAME_WIDTH,
            workdir=workdir,
        )
        written = []
        for index, frame in enumerate(sampled.frames):
            # Not `frame-*.jpg`: that name belongs to the sampler's own scratch files.
            path = workdir / f"detector-{index:02d}.jpg"
            path.write_bytes(frame)
            written.append(path)
        return written


def served_locally(offloaded: Mapping[str, str], asset_id: str, videos: frozenset[str]) -> bool:
    """Whether the exposure head must stay in process for this source.

    The inference service is handed one picture per source and cannot be handed eight,
    so a video it answered for would carry the frame-read producer version over a single
    frame. It keeps every other head and every still.
    """
    return MARQO_HEAD in offloaded and asset_id in videos
