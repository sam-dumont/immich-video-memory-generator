"""The shape every phrase module shares: a country by its CLDR code, a city by a rule,
islands and regions by a listed phrase, and the country after a comma.

A head (island or region) a language does not list gets no phrase, so the trip
title goes without a preposition rather than guess its article or case.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from immich_memories.i18n_places import _codes_by_english_name, localise_country
from immich_memories.place_phrases.place import Place


@dataclass(frozen=True)
class Rules:
    """One language's way of saying "in <place>"."""

    locale: str
    country: Callable[[str, str | None], str | None]
    city: Callable[[str], str | None]
    heads: dict[str, str] = field(default_factory=dict)
    conjunction: str = "and"


def compose(place: Place, rules: Rules) -> str | None:
    """The whole phrase for `place` under `rules`, or None when a piece is unknown."""
    if place.kind == "countries":
        return None
    if not place.heads:
        code = _codes_by_english_name().get(place.english.casefold())
        return rules.country(localise_country(place.english, rules.locale), code)
    if place.kind == "city":
        parts = [rules.city(place.heads[0])]
    else:
        parts = [rules.heads.get(head) for head in place.heads]
    if not all(parts):
        return None
    joined = f" {rules.conjunction} ".join(p for p in parts if p)
    if place.country is None:
        return joined
    return f"{joined}, {localise_country(place.country, rules.locale)}"
