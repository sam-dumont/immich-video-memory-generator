"""A trip's place, as a phrase module reads it."""

from __future__ import annotations

from dataclasses import dataclass

# How a place label joins two regions, in the English the app stores.
_AND = " and "


@dataclass(frozen=True)
class Place:
    """A trip's place: the English label the app stores and the scale it names.

    `english` is "Crete, Greece", "Netherlands", "Utah and Nevada, United
    States" or "Belgium → France". `kind` is the scale trip naming chose:
    "city", "island", "region", "regions", "country" or "countries".
    """

    english: str
    kind: str

    @property
    def country(self) -> str | None:
        """The country after the last comma, or the whole label when it is one."""
        if self.kind == "countries":
            return None
        head, separator, tail = self.english.rpartition(", ")
        return tail if separator else (self.english if self.kind == "country" else None)

    @property
    def heads(self) -> tuple[str, ...]:
        """The place(s) before the country: ("Crete",), ("Utah", "Nevada"); () for a country."""
        head, separator, _tail = self.english.rpartition(", ")
        if not separator:
            return () if self.kind in ("country", "countries") else (self.english,)
        return tuple(head.split(_AND))


def infer_place_kind(english: str) -> str:
    """The scale of a label saved before trips recorded it.

    Read from the label's shape, CLDR's country names and the offline island
    and region tables; any other head reads as a city, which is what every
    trip title assumed before.
    """
    from immich_memories.i18n_places import is_country
    from immich_memories.place_names import is_known_area

    if " → " in english:
        return "countries"
    if ", " not in english:
        return "country" if is_country(english) else "city"
    head = english.rpartition(", ")[0]
    if _AND in head:
        return "regions"
    return "region" if is_known_area(head) else "city"
