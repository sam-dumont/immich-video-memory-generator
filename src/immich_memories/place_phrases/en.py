"""English: "in" plus the article a name takes ("in the Netherlands", "in Utah and Nevada")."""

from __future__ import annotations

from immich_memories.place_phrases.place import Place

# Names that take "the" although nothing in their spelling says so.
_THE = frozenset(
    {
        "Bahamas",
        "Big Island",
        "Caribbean Netherlands",
        "Comoros",
        "Gambia",
        "Isle of Man",
        "Maldives",
        "Netherlands",
        "North Aegean",
        "Philippines",
        "Seychelles",
        "South Aegean",
    }
)
# Plural and "republic of ..." style names take "the" by their last word.
_THE_ENDINGS = (
    " Islands",
    " Republic",
    " Kingdom",
    " States",
    " Emirates",
    " Territories",
    " Territory",
)


def with_article(name: str) -> str:
    """The name as it follows "in": "the Netherlands", "the Canary Islands", "Italy"."""
    return f"the {name}" if name in _THE or name.endswith(_THE_ENDINGS) else name


def phrase(place: Place) -> str:
    """ "in the Netherlands", "in Crete, Greece", "in Utah and Nevada, United States"."""
    if place.kind == "countries":
        return f"across {place.english}"
    if not place.heads:
        return f"in {with_article(place.english)}"
    heads = " and ".join(with_article(head) for head in place.heads)
    preposition = "on" if place.heads == ("Big Island",) else "in"
    country = place.country
    return f"{preposition} {heads}, {country}" if country else f"{preposition} {heads}"
