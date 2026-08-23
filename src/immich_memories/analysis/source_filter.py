"""Source files a memory must never use, decided by their name alone.

Doorbells, security cameras, screen recorders and messaging apps all upload
into the same timeline as the camera roll. None of it was shot to be kept, and
some of it scores well: a doorbell is a perfectly stable camera pointed at a
place people walk through, and the analysis rated one 0.7 for interest and
called it "people" — correctly, since a person really did arrive at a door.

The filename is the only thing that settles this before analysis has looked at
anything, which is what keeps these out of the analysis budget and what makes
the rule work for anyone with no LLM configured at all. The holistic review is
the second line, for the cameras whose filenames give nothing away.
"""

from __future__ import annotations

import fnmatch
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


def from_an_excluded_source(name: str | None, patterns: Sequence[str]) -> bool:
    """True when a source filename matches one of the excluded glob patterns.

    Case-insensitive: the same export is written `RPReplay_Final` on one
    platform and lower case on another.
    """
    if not name:
        return False
    lowered = name.casefold()
    return any(fnmatch.fnmatch(lowered, pattern.casefold()) for pattern in patterns)
