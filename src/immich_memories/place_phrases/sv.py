"""Swedish: "i Italien", "på Kreta", "på Filippinerna"."""

from __future__ import annotations

from immich_memories.place_phrases.compose import Rules, compose
from immich_memories.place_phrases.place import Place

_COUNTRY = {
    "PH": "på Filippinerna",
    "MV": "på Maldiverna",
    "SC": "på Seychellerna",
    "BS": "på Bahamas",
    "CY": "på Cypern",
    "MT": "på Malta",
    "IS": "på Island",
    "IE": "på Irland",
    "MU": "på Mauritius",
    "JM": "på Jamaica",
    "CU": "på Kuba",
    "BB": "på Barbados",
    "MG": "på Madagaskar",
    "LK": "på Sri Lanka",
}


# Islands and regions a trip is named after, by their English name.
_HEADS = {
    "Crete": "på Kreta",
    "Cyprus": "på Cypern",
    "Rhodes": "på Rhodos",
    "Corfu": "på Korfu",
    "Santorini": "på Santorini",
    "Mykonos": "på Mykonos",
    "Zakynthos": "på Zakynthos",
    "Kos": "på Kos",
    "Mallorca": "på Mallorca",
    "Menorca": "på Menorca",
    "Ibiza": "på Ibiza",
    "Tenerife": "på Teneriffa",
    "Gran Canaria": "på Gran Canaria",
    "Lanzarote": "på Lanzarote",
    "Fuerteventura": "på Fuerteventura",
    "Madeira": "på Madeira",
    "Oléron": "på Oléron",
    "Bali": "på Bali",
    "Oahu": "på Oahu",
    "Maui": "på Maui",
    "Kauai": "på Kauai",
    "Sicily": "på Sicilien",
    "Sardinia": "på Sardinien",
    "Corsica": "på Korsika",
    "Apulia": "i Apulien",
    "Saxony": "i Sachsen",
    "Lower Saxony": "i Niedersachsen",
    "Bavaria": "i Bayern",
    "Tuscany": "i Toscana",
    "Lombardy": "i Lombardiet",
    "Piedmont": "i Piemonte",
    "Campania": "i Kampanien",
    "Calabria": "i Kalabrien",
    "Liguria": "i Ligurien",
    "Veneto": "i Veneto",
    "Catalonia": "i Katalonien",
    "Andalusia": "i Andalusien",
    "Balearic Islands": "på Balearerna",
    "Canary Islands": "på Kanarieöarna",
    "Brittany": "i Bretagne",
    "Normandy": "i Normandie",
    "Attica": "i Attika",
    "Scotland": "i Skottland",
    "England": "i England",
    "Wales": "i Wales",
    "Flanders": "i Flandern",
    "Wallonia": "i Vallonien",
    "Tyrol": "i Tyrolen",
    "California": "i Kalifornien",
    "Nevada": "i Nevada",
    "Utah": "i Utah",
    "Arizona": "i Arizona",
    "Florida": "i Florida",
    "Texas": "i Texas",
    "Colorado": "i Colorado",
    "Oregon": "i Oregon",
    "Hawaii": "på Hawaii",
}


def _country(name: str, code: str | None) -> str | None:
    return _COUNTRY.get(code or "", f"i {name}")


def _city(name: str) -> str:
    return f"i {name}"


_RULES = Rules("sv", _country, _city, _HEADS, "och")


def phrase(place: Place) -> str | None:
    """This language's phrase for the trip's place, or None when a piece is unknown."""
    return compose(place, _RULES)
