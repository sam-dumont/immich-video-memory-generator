"""Short, common names for islands and regions, offline.

Immich names a picture's region with GeoNames' admin-1 label, and an opt-in
geocoder may answer with an administrative unit ("Regional Unit of Heraklion",
"Région Crète"). A title wants the name people say: "Crete", "Crète". This
module holds the small tables that get there without asking anyone:

- `short_place_name` drops administrative wording and maps local spellings to
  the English name the rest of the app reads ("Puglia" -> "Apulia").
- `island_at` finds the island a picture was taken on, for the islands GeoNames
  files under a larger region (Mallorca under the Balearic Islands). Islands
  that are their own region (Crete, Sicily, Sardinia, Corsica) or their own
  country (Cyprus) need no box, but the ones listed here win over their region.
- `localise_place_part` gives an English island or region name in the film's
  language, and leaves any name it does not know as it is.

The bounding boxes are hand-drawn from public coordinates, tight enough that
the nearest mainland stays outside, and each is also checked against the
picture's country.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class _Island:
    name: str
    country: str
    south: float
    west: float
    north: float
    east: float

    def holds(self, lat: float, lon: float, country: str) -> bool:
        return (
            country == self.country
            and self.south <= lat <= self.north
            and self.west <= lon <= self.east
        )


_ISLANDS = (
    _Island("Crete", "Greece", 34.80, 23.45, 35.72, 26.40),
    _Island("Rhodes", "Greece", 35.85, 27.68, 36.48, 28.25),
    _Island("Corfu", "Greece", 39.35, 19.60, 39.83, 20.12),
    _Island("Santorini", "Greece", 36.33, 25.33, 36.48, 25.49),
    _Island("Mykonos", "Greece", 37.39, 25.29, 37.51, 25.47),
    _Island("Zakynthos", "Greece", 37.64, 20.62, 37.95, 20.99),
    _Island("Kos", "Greece", 36.72, 26.90, 36.91, 27.36),
    _Island("Cyprus", "Cyprus", 34.55, 32.25, 35.72, 34.62),
    _Island("Mallorca", "Spain", 39.25, 2.30, 39.97, 3.48),
    _Island("Menorca", "Spain", 39.79, 3.78, 40.10, 4.33),
    _Island("Ibiza", "Spain", 38.82, 1.15, 39.12, 1.63),
    _Island("Tenerife", "Spain", 27.99, -16.93, 28.60, -16.10),
    _Island("Gran Canaria", "Spain", 27.73, -15.84, 28.19, -15.35),
    _Island("Lanzarote", "Spain", 28.83, -13.89, 29.25, -13.41),
    _Island("Fuerteventura", "Spain", 28.04, -14.52, 28.76, -13.82),
    _Island("Madeira", "Portugal", 32.62, -17.27, 32.88, -16.65),
    _Island("Oléron", "France", 45.78, -1.42, 46.05, -1.18),
    _Island("Bali", "Indonesia", -8.85, 114.43, -8.06, 115.71),
    _Island("Oahu", "United States", 21.25, -158.29, 21.72, -157.64),
    _Island("Maui", "United States", 20.57, -156.70, 21.04, -155.97),
    _Island("Kauai", "United States", 21.86, -159.80, 22.24, -159.29),
    _Island("Big Island", "United States", 18.90, -156.07, 20.28, -154.79),
)

# Local or administrative spellings -> the English name the app reads.
_ENGLISH = {
    "puglia": "Apulia",
    "sachsen": "Saxony",
    "freistaat sachsen": "Saxony",
    "niedersachsen": "Lower Saxony",
    "bayern": "Bavaria",
    "kriti": "Crete",
    "κρήτη": "Crete",
    "crète": "Crete",
    "sicilia": "Sicily",
    "sardegna": "Sardinia",
    "corse": "Corsica",
    "toscana": "Tuscany",
    "lombardia": "Lombardy",
    "piemonte": "Piedmont",
    "cataluña": "Catalonia",
    "catalunya": "Catalonia",
    "andalucía": "Andalusia",
    "illes balears": "Balearic Islands",
    "islas baleares": "Balearic Islands",
    "canarias": "Canary Islands",
    "bretagne": "Brittany",
    "normandie": "Normandy",
    "notio aigaio": "South Aegean",
    "ionia nisia": "Ionian Islands",
    "attiki": "Attica",
    "majorque": "Mallorca",
}

# English island and region names in French. Anything absent stays English,
# which for most US states and many regions is also the French name.
_FRENCH = {
    "Crete": "Crète",
    "Rhodes": "Rhodes",
    "Corfu": "Corfou",
    "Santorini": "Santorin",
    "Zakynthos": "Zante",
    "Cyprus": "Chypre",
    "Mallorca": "Majorque",
    "Menorca": "Minorque",
    "Gran Canaria": "Grande Canarie",
    "Madeira": "Madère",
    "Big Island": "Grande Île d'Hawaï",
    "Apulia": "Pouilles",
    "Saxony": "Saxe",
    "Lower Saxony": "Basse-Saxe",
    "Bavaria": "Bavière",
    "Sicily": "Sicile",
    "Sardinia": "Sardaigne",
    "Corsica": "Corse",
    "Tuscany": "Toscane",
    "Lombardy": "Lombardie",
    "Piedmont": "Piémont",
    "Campania": "Campanie",
    "Calabria": "Calabre",
    "Liguria": "Ligurie",
    "Catalonia": "Catalogne",
    "Andalusia": "Andalousie",
    "Balearic Islands": "Îles Baléares",
    "Canary Islands": "Îles Canaries",
    "Brittany": "Bretagne",
    "Normandy": "Normandie",
    "South Aegean": "Égée-Méridionale",
    "Ionian Islands": "Îles Ioniennes",
    "Attica": "Attique",
    "California": "Californie",
    "Florida": "Floride",
    "Hawaii": "Hawaï",
    "Scotland": "Écosse",
    "England": "Angleterre",
    "Wales": "pays de Galles",
    "Flanders": "Flandre",
    "Wallonia": "Wallonie",
}

_ADMIN_PREFIXES = (
    "decentralized administration of ",
    "regional unit of ",
    "region of ",
    "autonomous province of ",
    "province of ",
    "free state of ",
    "state of ",
    "région ",
    "provincia di ",
    "provincia de ",
)
_ADMIN_SUFFIXES = (" regional unit", " region", " province", " district", " governorate")

# How "A and B" is joined in each title language.
_AND = {"en": " and ", "fr": " et "}


def _is_latin(text: str) -> bool:
    return all(not ch.isalpha() or unicodedata.name(ch, "").startswith("LATIN") for ch in text)


def short_place_name(label: str | None) -> str | None:
    """The common English name for a region or city label, or None if it has none.

    Administrative wording is dropped ("Limassol District" -> "Limassol"), a
    known local spelling becomes English ("Puglia" -> "Apulia"), and a label
    left in a non-Latin script with no English name is None, so the trip is
    named one scale up rather than in an alphabet the title may not speak.
    """
    if not label:
        return None
    name = label.strip()
    folded = name.casefold()
    for prefix in _ADMIN_PREFIXES:
        if folded.startswith(prefix):
            name, folded = name[len(prefix) :], folded[len(prefix) :]
    for suffix in _ADMIN_SUFFIXES:
        if folded.endswith(suffix):
            name, folded = name[: -len(suffix)], folded[: -len(suffix)]
    name = _ENGLISH.get(folded, name)
    return name if name and _is_latin(name) else None


def island_at(lat: float, lon: float, country: str) -> str | None:
    """The listed island this point is on, or None."""
    return next((i.name for i in _ISLANDS if i.holds(lat, lon, country)), None)


def localise_place_part(name: str, locale: str) -> str:
    """An English island or region name (or "A and B") in the film's language."""
    parts = name.split(_AND["en"])
    if locale == "fr":
        parts = [_FRENCH.get(part, part) for part in parts]
    return _AND.get(locale, _AND["en"]).join(parts)


def is_known_area(english_name: str) -> bool:
    """Whether this is an island or region the tables above name."""
    return english_name in _FRENCH or any(i.name == english_name for i in _ISLANDS)
