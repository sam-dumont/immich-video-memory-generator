"""Spanish: always "en", with the article a few country names keep."""

from __future__ import annotations

from immich_memories.place_phrases.compose import Rules, compose
from immich_memories.place_phrases.place import Place

_COUNTRY = {
    "GB": "en el Reino Unido",
    "IN": "en la India",
    "NL": "en los Países Bajos",
    "DO": "en la República Dominicana",
    "CF": "en la República Centroafricana",
    "AE": "en los Emiratos Árabes Unidos",
    "SV": "en El Salvador",
}


# Islands and regions a trip is named after, by their English name.
_HEADS = {
    "Crete": "en Creta",
    "Cyprus": "en Chipre",
    "Rhodes": "en Rodas",
    "Corfu": "en Corfú",
    "Santorini": "en Santorini",
    "Mykonos": "en Mykonos",
    "Zakynthos": "en Zante",
    "Kos": "en Cos",
    "Mallorca": "en Mallorca",
    "Menorca": "en Menorca",
    "Ibiza": "en Ibiza",
    "Tenerife": "en Tenerife",
    "Gran Canaria": "en Gran Canaria",
    "Lanzarote": "en Lanzarote",
    "Fuerteventura": "en Fuerteventura",
    "Madeira": "en Madeira",
    "Oléron": "en Oléron",
    "Bali": "en Bali",
    "Oahu": "en Oahu",
    "Maui": "en Maui",
    "Kauai": "en Kauai",
    "Sicily": "en Sicilia",
    "Sardinia": "en Cerdeña",
    "Corsica": "en Córcega",
    "Apulia": "en Apulia",
    "Saxony": "en Sajonia",
    "Lower Saxony": "en Baja Sajonia",
    "Bavaria": "en Baviera",
    "Tuscany": "en la Toscana",
    "Lombardy": "en Lombardía",
    "Piedmont": "en el Piamonte",
    "Campania": "en Campania",
    "Calabria": "en Calabria",
    "Liguria": "en Liguria",
    "Veneto": "en el Véneto",
    "Catalonia": "en Cataluña",
    "Andalusia": "en Andalucía",
    "Balearic Islands": "en las Islas Baleares",
    "Canary Islands": "en las Islas Canarias",
    "Brittany": "en Bretaña",
    "Normandy": "en Normandía",
    "Attica": "en el Ática",
    "Scotland": "en Escocia",
    "England": "en Inglaterra",
    "Wales": "en Gales",
    "Flanders": "en Flandes",
    "Wallonia": "en Valonia",
    "Tyrol": "en el Tirol",
    "California": "en California",
    "Nevada": "en Nevada",
    "Utah": "en Utah",
    "Arizona": "en Arizona",
    "Florida": "en Florida",
    "Texas": "en Texas",
    "Colorado": "en Colorado",
    "Oregon": "en Oregón",
    "Hawaii": "en Hawái",
}


def _country(name: str, code: str | None) -> str | None:
    return _COUNTRY.get(code or "", f"en {name}")


def _city(name: str) -> str:
    return f"en {name}"


_RULES = Rules("es", _country, _city, _HEADS, "y")


def phrase(place: Place) -> str | None:
    """This language's phrase for the trip's place, or None when a piece is unknown."""
    return compose(place, _RULES)
