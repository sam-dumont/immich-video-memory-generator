"""Whether a picture shows the people it names, read off the faces in it.

A picture can name somebody and still not show them. In the finish-line frame
that prompted this, the named runner's face covers 0.41 % of the picture, sits
hard against a border, and is half the size of the stranger's face beside it.
Every fact the pick read -- the caption, the place, the heads, the people --
said only that he was there. Two frames of the moment were indistinguishable to
it, so it took the sharper one and the film showed a crowd.

Two questions are answered here, from the picture's own boxes and its own frame:

* **inside** -- does the named face have a face's width of air between it and
  every border? Where the box is too big for that (three of it will not fit
  across the frame) the question degrades to the only one left: is it cut by a
  border?
* **foremost** -- is it the largest face in the picture, or one of the small ones?

The share of the frame it covers rides along as a number, so a reader comparing
two frames of one moment can see which of them shows the person.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Any, NamedTuple

# A face box cut into three still fits across the frame; that is the largest box
# for which "a face's width of air on either side" is a question the frame can answer.
_AIR_FITS = 3


@dataclass(frozen=True, slots=True)
class FaceBox:
    """One detected face, normalized to its own picture's frame."""

    x1: float
    y1: float
    x2: float
    y2: float
    named: bool
    # The Immich person matched to this face, so a memory about one person reads
    # that person's face. None when nobody named is matched, or when the box was
    # banked before identities were, which no framing question may guess past.
    person_id: str | None = None

    @property
    def area(self) -> float:
        return max(0.0, self.x2 - self.x1) * max(0.0, self.y2 - self.y1)

    @property
    def inside_the_frame(self) -> bool:
        return _has_air(self.x1, self.x2) and _has_air(self.y1, self.y2)


@dataclass(frozen=True, slots=True)
class SubjectFraming:
    """How well the best-shown named face in one picture is framed."""

    share: float
    inside: bool
    foremost: bool

    @property
    def rung(self) -> int:
        """1 for shown at all, one more for each of inside and foremost."""
        return 1 + int(self.inside) + int(self.foremost)


class SubjectVisibility(NamedTuple):
    """What a line says about its named subject; all zero when it says nothing."""

    rung: int
    share: float


_ANNOTATION = re.compile(r"subject-framing:(\d)\s*\(([\d.]+)% of the frame")


def _has_air(low: float, high: float) -> bool:
    size = high - low
    if size <= 0:
        return False
    if size * _AIR_FITS > 1:
        return low > 0 and high < 1
    return low >= size and (1 - high) >= size


def subject_framing(
    boxes: Sequence[FaceBox], subjects: Collection[str] | None = None
) -> SubjectFraming | None:
    """How the best-shown subject face sits in its frame, or None if it shows none.

    ``subjects`` are the person ids a memory is about. Without them any named face
    is a subject; with them only those people's faces are, so a larger face of
    somebody else in the frame counts against the subject instead of for it.
    """
    named = [
        face
        for face in boxes
        if face.area > 0 and (face.person_id in subjects if subjects is not None else face.named)
    ]
    if not named:
        return None
    subject = max(named, key=lambda face: face.area)
    largest = max(face.area for face in boxes)
    return SubjectFraming(
        share=subject.area,
        inside=subject.inside_the_frame,
        foremost=subject.area >= largest,
    )


def framing_annotation(framing: SubjectFraming) -> str:
    """The grounded observation for the evidence line, machine-readable at its head."""
    placement = "inside the frame" if framing.inside else "at the frame's edge"
    standing = "the largest face" if framing.foremost else "not the largest face"
    return (
        f"subject-framing:{framing.rung} "
        f"({framing.share * 100:.2f}% of the frame, {placement}, {standing})"
    )


def framing_visibility(line: str) -> SubjectVisibility:
    """Read the fact back off a picture's line, without re-deriving it."""
    found = _ANNOTATION.search(line)
    if not found:
        return SubjectVisibility(0, 0.0)
    return SubjectVisibility(int(found.group(1)), float(found.group(2)) / 100)


def face_boxes_of(faces: Sequence[Any]) -> tuple[FaceBox, ...]:
    """Immich's faces for one picture, normalized by the frame each was found on.

    Immich reports a face box in the pixels of whatever rendition it ran detection
    over, and states those dimensions beside it. Normalizing by the box's own
    reference is the only way two boxes on one picture are comparable at all.
    """
    boxes = (_normalized(face) for face in faces)
    return tuple(box for box in boxes if box is not None)


def _normalized(face: Any) -> FaceBox | None:
    width = getattr(face, "image_width", 0) or 0
    height = getattr(face, "image_height", 0) or 0
    if width <= 0 or height <= 0:
        return None
    person = getattr(face, "person", None)
    named = bool(person and person.name.strip())
    return FaceBox(
        x1=max(0.0, min(1.0, face.bounding_box_x1 / width)),
        y1=max(0.0, min(1.0, face.bounding_box_y1 / height)),
        x2=max(0.0, min(1.0, face.bounding_box_x2 / width)),
        y2=max(0.0, min(1.0, face.bounding_box_y2 / height)),
        named=named,
        person_id=person.id if named and person is not None else None,
    )
