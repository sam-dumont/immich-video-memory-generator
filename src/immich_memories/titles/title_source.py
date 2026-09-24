"""Where a film's title came from.

The title is decided in several places (the command line, the album, the
special-day catalogue, the model, the trip's place, the template), and each one
can be overruled by the next. Carrying the winner's name to the end is what
lets a log line and the run record say which one it was, so a template
fallback is never read as the model's work.
"""

from __future__ import annotations

from enum import StrEnum


class TitleSource(StrEnum):
    """The source that produced the title a film opens on."""

    OVERRIDE = "override"  # the person typed it (--title, or edited in the UI)
    ALBUM = "album"  # an album memory is named after its album
    OCCASION = "occasion"  # a holiday's name, or the special-day catalogue's title
    MODEL = "model"  # the title reader wrote it
    PLACE = "place"  # a trip's title, built from where it went
    FALLBACK = "fallback"  # the template: dates, people, the year


def override_source(title: str, memory_type: str | None, preset_params: dict | None) -> TitleSource:
    """Who wrote a title that reached the run as a ready-made override.

    An album memory passes its album's name, and a special day its
    catalogue's title, through the same slot as a typed --title; the preset
    parameters still carry both, which is how they are told apart.
    """
    params = preset_params or {}
    if memory_type == "album" and title == params.get("album_name"):
        return TitleSource.ALBUM
    if title == params.get("title"):
        return TitleSource.OCCASION
    return TitleSource.OVERRIDE
