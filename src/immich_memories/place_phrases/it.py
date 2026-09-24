"""Italian: "in" for countries and regions, "a" for cities and small islands, articles for plurals."""

from __future__ import annotations

from immich_memories.place_phrases.compose import Rules, compose
from immich_memories.place_phrases.place import Place

_COUNTRY = {
    "US": "negli Stati Uniti",
    "AE": "negli Emirati Arabi Uniti",
    "NL": "nei Paesi Bassi",
    "GB": "nel Regno Unito",
    "PH": "nelle Filippine",
    "MV": "alle Maldive",
    "SC": "alle Seychelles",
    "BS": "alle Bahamas",
    "CY": "a Cipro",
    "MT": "a Malta",
    "CU": "a Cuba",
    "MU": "a Mauritius",
    "JM": "in Giamaica",
    "SG": "a Singapore",
    "MC": "a Monaco",
    "SM": "a San Marino",
    "VA": "in Vaticano",
}


# Islands and regions a trip is named after, by their English name.
_HEADS = {
    "Crete": "a Creta",
    "Cyprus": "a Cipro",
    "Rhodes": "a Rodi",
    "Corfu": "a Corfù",
    "Santorini": "a Santorini",
    "Mykonos": "a Mykonos",
    "Zakynthos": "a Zante",
    "Kos": "a Kos",
    "Mallorca": "a Maiorca",
    "Menorca": "a Minorca",
    "Ibiza": "a Ibiza",
    "Tenerife": "a Tenerife",
    "Gran Canaria": "a Gran Canaria",
    "Lanzarote": "a Lanzarote",
    "Fuerteventura": "a Fuerteventura",
    "Madeira": "a Madeira",
    "Oléron": "a Oléron",
    "Bali": "a Bali",
    "Oahu": "a Oahu",
    "Maui": "a Maui",
    "Kauai": "a Kauai",
    "Sicily": "in Sicilia",
    "Sardinia": "in Sardegna",
    "Corsica": "in Corsica",
    "Apulia": "in Puglia",
    "Saxony": "in Sassonia",
    "Lower Saxony": "in Bassa Sassonia",
    "Bavaria": "in Baviera",
    "Tuscany": "in Toscana",
    "Lombardy": "in Lombardia",
    "Piedmont": "in Piemonte",
    "Campania": "in Campania",
    "Calabria": "in Calabria",
    "Liguria": "in Liguria",
    "Veneto": "in Veneto",
    "Catalonia": "in Catalogna",
    "Andalusia": "in Andalusia",
    "Balearic Islands": "alle Baleari",
    "Canary Islands": "alle Canarie",
    "Brittany": "in Bretagna",
    "Normandy": "in Normandia",
    "Attica": "in Attica",
    "Scotland": "in Scozia",
    "England": "in Inghilterra",
    "Wales": "nel Galles",
    "Flanders": "nelle Fiandre",
    "Wallonia": "in Vallonia",
    "Tyrol": "in Tirolo",
    "California": "in California",
    "Nevada": "in Nevada",
    "Utah": "nello Utah",
    "Arizona": "in Arizona",
    "Florida": "in Florida",
    "Texas": "in Texas",
    "Colorado": "in Colorado",
    "Oregon": "in Oregon",
    "Hawaii": "alle Hawaii",
}


def _country(name: str, code: str | None) -> str | None:
    return _COUNTRY.get(code or "", f"in {name}")


def _city(name: str) -> str:
    return f"a {name}"


_RULES = Rules("it", _country, _city, _HEADS, "e")


def phrase(place: Place) -> str | None:
    """This language's phrase for the trip's place, or None when a piece is unknown."""
    return compose(place, _RULES)
