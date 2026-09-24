"""Which parts of a rendered annotation line say what the picture shows, and which we wrote.

A line joins the caption producer's prose with tags this pipeline writes itself: the date, the
media kind, the place, the people and their relations, the subject framing, the detector heads,
the star, the nominators' flags, the pixel warnings and the source's technical facts. A rule that
reads what a picture shows reads only the first kind: a Live Photo burst that "stitches to a 3s
clip" is not a picture of stitches, and a place or a person's name is not a scene.

A segment this grammar does not know counts as content, so a new tag can only make a content rule
refuse more, never let a picture through that it should have held.
"""

from __future__ import annotations

import re

STARRED = "STARRED by the photographer"
FLAGGED = "FLAGGED "
PIXEL_WARNINGS = ("SOFT (blurry)", "DARK", "BLOWN OUT", "rotated")

_ROW_PREFIX = re.compile(r"^Material picture p\d+: ")
_PIPELINE_TAG = re.compile(
    r"^(?:"
    r"\d{4}-\d\d-\d\d"  # when it was taken
    r"|VIDEO\b|LIVE PHOTO\b"  # the media kind, a burst's stitching included
    r"|at |with "  # the place, and the people with their relations and ages
    r"|subject-framing:"
    r"|[a-z_]+=[^ ]"  # the detector heads, `activity=playing, people=one`
    r"|[a-z][a-z-]*:\S"  # the source's technical facts, `resolution:4032x3024`, `exposure:1/50`
    r"|reencode-suspected$"
    rf"|{re.escape(STARRED)}$"
    rf"|{re.escape(FLAGGED)}"
    rf"|(?:{'|'.join(re.escape(warning) for warning in PIXEL_WARNINGS)})$"
    r")"
)


def content_of(line: str) -> str:
    """The picture's own evidence on a line: the caption and its setting and exposure fields,
    joined as the line joins them, with every pipeline-written tag left out.

    A unit's line may hold one row per material picture; each row is read the same way.
    """
    kept = []
    for row in line.splitlines():
        for part in _ROW_PREFIX.sub("", row.strip()).split(" | "):
            part = part.strip()
            if part and not _PIPELINE_TAG.match(part):
                kept.append(part)
    return " | ".join(kept)
