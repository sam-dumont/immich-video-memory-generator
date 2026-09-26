"""Whether a clip shows its moment across its frames, not only in the frame Immich previews.

A video's line was read off one picture: Immich's preview, its faces and the heads decided on
it. A clip of a child peeking round a door, mostly wall and radiator with the child at the
edge, reads there as a people moment with a well-framed face, and the text reader approved it
in one film and refused it in the next on the same row. What the viewer sees is every frame.

So the `frame_kind` head reads the eight frames the exposure head already samples across the
clip, and the clip carries one fact: the share of its frames that show something a film can
hold. Measured on 2026-09-23 over 33 real clips: the two a reviewer called "mostly wall,
subject at the edge" read five of eight frames as a moment (mean carrying probability 0.51
and 0.54); every clip kept beside them read six or more (0.75 and up). A clip that shows its
moment in fewer than three frames of four does not stand on its own, whatever its preview.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

from immich_memories.analysis.editorial_carrier_eligibility import CARRYING_KINDS
from immich_memories.triage.heads import HeadFact

CLIP_FRAMES_HEAD = "clip_frames"
# The head that reads each frame, and how many frames it reads: a new frame head or a new
# sample count is a new question, and every clip is read again.
CLIP_FRAMES_VERSION = "frame_kind-public-v1/8-frames"
SHOWS_ITS_MOMENT = "shows_its_moment"
SUBJECT_OFTEN_MISSING = "subject_often_missing"
CARRYING_SHARE = 0.75


def clip_frames_fact(frame_kinds: Sequence[str]) -> HeadFact | None:
    """The clip's fact from the `frame_kind` label of each sampled frame; None without one."""
    if not frame_kinds:
        return None
    share = sum(kind in CARRYING_KINDS for kind in frame_kinds) / len(frame_kinds)
    return HeadFact(
        head=CLIP_FRAMES_HEAD,
        label=SHOWS_ITS_MOMENT if share >= CARRYING_SHARE else SUBJECT_OFTEN_MISSING,
        confidence=share,
        version=CLIP_FRAMES_VERSION,
    )


# How the fact reads on a picture's line (`annotation_lines` renames the head to `frames`), so
# a reader of the line needs no second source for it.
LINE_NAME = "frames"
_ON_THE_LINE = f"{LINE_NAME}={SUBJECT_OFTEN_MISSING}"


def subject_often_missing(line: str) -> bool:
    """Whether a picture's line says its clip's frames often miss the subject."""
    return _ON_THE_LINE in line


def unusable_video(unit: Mapping, line: str) -> bool:
    """A non-favourite video must show its subject across the sampled frames.

    A Live Photo keeps its still; this refusal applies only to a standalone video.
    """
    return unit.get("kind") == "video" and not unit.get("favourite") and subject_often_missing(line)


def load_clip_frames(store_path: Path | str | None, clip_ids: Iterable[str]) -> dict[str, str]:
    """The banked `clip_frames` label of each of these clips; a clip never read is absent."""
    ids = sorted(set(clip_ids))
    if store_path is None or not ids or not Path(store_path).exists():
        return {}
    con = sqlite3.connect(f"file:{store_path}?mode=ro", uri=True)
    out: dict[str, str] = {}
    try:
        for start in range(0, len(ids), 500):
            chunk = ids[start : start + 500]
            marks = ",".join("?" * len(chunk))
            rows = con.execute(
                f"select asset_id, label from head_facts where asset_id in ({marks}) "  # noqa: S608
                "and head = ? and version = ?",
                [*chunk, CLIP_FRAMES_HEAD, CLIP_FRAMES_VERSION],
            )
            out.update({str(asset_id): str(label) for asset_id, label in rows})
    except sqlite3.OperationalError:
        return {}
    finally:
        con.close()
    return out


def clips_miss_subject(clip_frames: Mapping[str, str], clip_ids: Iterable[str]) -> bool:
    """Whether any of a Live Photo's clips was read as often missing its subject.

    A Live Photo is its still: such a clip costs it its motion, never its place. A clip
    nobody read keeps what it had before, motion decided by its residual alone.
    """
    return any(clip_frames.get(clip) == SUBJECT_OFTEN_MISSING for clip in clip_ids)
