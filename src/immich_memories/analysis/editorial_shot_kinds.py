"""What kind of shot a picture is, for a film's variety: a portrait, or texture.

A portrait is one or two people and nothing else going on. Texture is everything a film
needs around its portraits: a place outdoors or a landscape, a crowd or a group, a race, a
party, a stage, sightseeing, animals, a record of something. An empty interior (store
shelves, a hallway) is not a place a film is about, so it has no kind. Read off the ingest
heads alone, so every library gets the same answer at no cost; a picture the heads read as
carrying nothing, or never read, has no kind.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping

PORTRAIT = "portrait"
TEXTURE = "texture"

_GROUPS = frozenset({"small-group", "crowd"})
# What people are doing that makes a people shot an event rather than a pose.
_EVENT_ACTIVITIES = frozenset(
    {"sport-active", "celebration", "performing", "sightseeing", "animal-nature"}
)

KindOf = Callable[[str], str | None]


def shot_kind(heads: Mapping[str, str]) -> str | None:
    """PORTRAIT, TEXTURE, or None when the frame head carries nothing or never read it."""
    frame = heads.get("frame_kind")
    if frame == "place_or_scenery":
        outside = heads.get("location") == "outdoor" or heads.get("people") in _GROUPS
        return TEXTURE if outside else None
    if frame == "meaningful_record":
        return TEXTURE
    if frame != "people_moment":
        return None
    if heads.get("people") in _GROUPS or heads.get("activity") in _EVENT_ACTIVITIES:
        return TEXTURE
    return PORTRAIT


def kind_mix(assets: Iterable[str], kind_of: KindOf) -> dict[str, int]:
    """How many shots of each kind; shots with no kind are not counted."""
    return dict(sorted(Counter(k for a in assets if (k := kind_of(a))).items()))


def lacking(kinds: Iterable[str | None]) -> str | None:
    """The kind this company holds fewer of, or None when it holds as many of each."""
    counts = Counter(kinds)
    if counts[TEXTURE] == counts[PORTRAIT]:
        return None
    return TEXTURE if counts[TEXTURE] < counts[PORTRAIT] else PORTRAIT
