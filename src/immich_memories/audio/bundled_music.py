"""Bundled royalty-free music, used when no generation backend is available.

The tracks ship in the separate ``immich-memories-music`` distribution (the
``music`` extra) so a plain install stays small, while the Docker image — which
installs every extra — has music without a GPU or a music server.
"""

from __future__ import annotations

import logging
import random
from pathlib import Path

logger = logging.getLogger(__name__)

_SUFFIXES = (".opus", ".mp3", ".m4a", ".flac", ".ogg", ".wav")

# The package ships five folders; the analyser's mood families are wider than
# that. A near neighbour is better music for the memory than the flat pool, so
# the families with no folder of their own borrow the closest one that has.
_MOOD_FOLDERS = {
    "playful": "happy",
    "peaceful": "calm",
    "exciting": "energetic",
    "romantic": "tender",
}


def bundled_library() -> Path | None:
    """Directory of bundled tracks, or None when the music package is absent."""
    try:
        from immich_memories_music import tracks_dir
    except ImportError:
        return None
    directory = tracks_dir()
    return directory if directory.is_dir() else None


def bundled_track_for_mood(
    mood: str | None,
    library: Path | None = None,
    cadence_seconds: float | None = None,
) -> Path | None:
    """Pick a bundled track for a mood, at random so repeats differ.

    Falls back to any bundled track when the mood has no folder of its own: some
    music beats silence, and the analyser's mood vocabulary is wider than the set
    of moods we ship.

    With a photo cadence, the pick stops being random and becomes the track whose
    beat divides that cadence -- the bundled counterpart to asking a generator
    for a beat-aligned tempo (#312). Without photos there is no rhythm to sync
    to, so variety wins and the choice stays random.
    """
    root = library if library is not None else bundled_library()
    if root is None or not root.is_dir():
        return None

    folder = root / _folder_for(mood)
    candidates = _tracks_in(folder) if folder.is_dir() else []
    if not candidates:
        candidates = sorted(
            track for child in root.iterdir() if child.is_dir() for track in _tracks_in(child)
        )
    if not candidates:
        return None

    chosen = _fitting_track(candidates, cadence_seconds) or random.choice(candidates)
    logger.info("Using bundled music: %s/%s", chosen.parent.name, chosen.name)
    return chosen


def _folder_for(mood: str | None) -> str:
    if not mood:
        return ""
    return _MOOD_FOLDERS.get(mood.lower(), mood.lower())


def _tracks_in(folder: Path) -> list[Path]:
    return sorted(p for p in folder.iterdir() if p.suffix.lower() in _SUFFIXES)


def _fitting_track(candidates: list[Path], cadence_seconds: float | None) -> Path | None:
    """The candidate whose beat lands on the cadence, when there is one to land on."""
    if not cadence_seconds:
        return None
    from immich_memories.audio.track_tempo import track_for_cadence

    return track_for_cadence(candidates, cadence_seconds)
