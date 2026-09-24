"""Dutch: "in Italië", "in de Verenigde Staten", "op Kreta", "op de Filipijnen"."""

from __future__ import annotations

from immich_memories.place_phrases.compose import Rules, compose
from immich_memories.place_phrases.place import Place

_COUNTRY = {
    "US": "in de Verenigde Staten",
    "GB": "in het Verenigd Koninkrijk",
    "AE": "in de Verenigde Arabische Emiraten",
    "DO": "in de Dominicaanse Republiek",
    "CF": "in de Centraal-Afrikaanse Republiek",
    "PH": "op de Filipijnen",
    "MV": "op de Malediven",
    "SC": "op de Seychellen",
    "BS": "op de Bahama's",
    "CY": "op Cyprus",
    "MT": "op Malta",
    "IS": "in IJsland",
    "MU": "op Mauritius",
    "JM": "op Jamaica",
    "CU": "op Cuba",
    "BB": "op Barbados",
    "MG": "op Madagaskar",
    "VA": "in Vaticaanstad",
}


# Islands and regions a trip is named after, by their English name.
_HEADS = {
    "Crete": "op Kreta",
    "Cyprus": "op Cyprus",
    "Rhodes": "op Rhodos",
    "Corfu": "op Corfu",
    "Santorini": "op Santorini",
    "Mykonos": "op Mykonos",
    "Zakynthos": "op Zakynthos",
    "Kos": "op Kos",
    "Mallorca": "op Mallorca",
    "Menorca": "op Menorca",
    "Ibiza": "op Ibiza",
    "Tenerife": "op Tenerife",
    "Gran Canaria": "op Gran Canaria",
    "Lanzarote": "op Lanzarote",
    "Fuerteventura": "op Fuerteventura",
    "Madeira": "op Madeira",
    "Oléron": "op Oléron",
    "Bali": "op Bali",
    "Oahu": "op Oahu",
    "Maui": "op Maui",
    "Kauai": "op Kauai",
    "Sicily": "op Sicilië",
    "Sardinia": "op Sardinië",
    "Corsica": "op Corsica",
    "Apulia": "in Apulië",
    "Saxony": "in Saksen",
    "Lower Saxony": "in Nedersaksen",
    "Bavaria": "in Beieren",
    "Tuscany": "in Toscane",
    "Lombardy": "in Lombardije",
    "Piedmont": "in Piëmont",
    "Campania": "in Campanië",
    "Calabria": "in Calabrië",
    "Liguria": "in Ligurië",
    "Veneto": "in Veneto",
    "Catalonia": "in Catalonië",
    "Andalusia": "in Andalusië",
    "Balearic Islands": "op de Balearen",
    "Canary Islands": "op de Canarische Eilanden",
    "Brittany": "in Bretagne",
    "Normandy": "in Normandië",
    "Attica": "in Attica",
    "Scotland": "in Schotland",
    "England": "in Engeland",
    "Wales": "in Wales",
    "Flanders": "in Vlaanderen",
    "Wallonia": "in Wallonië",
    "Tyrol": "in Tirol",
    "California": "in Californië",
    "Nevada": "in Nevada",
    "Utah": "in Utah",
    "Arizona": "in Arizona",
    "Florida": "in Florida",
    "Texas": "in Texas",
    "Colorado": "in Colorado",
    "Oregon": "in Oregon",
    "Hawaii": "op Hawaï",
}


def _country(name: str, code: str | None) -> str | None:
    return _COUNTRY.get(code or "", f"in {name}")


def _city(name: str) -> str:
    return f"in {name}"


_RULES = Rules("nl", _country, _city, _HEADS, "en")


def phrase(place: Place) -> str | None:
    """This language's phrase for the trip's place, or None when a piece is unknown."""
    return compose(place, _RULES)
