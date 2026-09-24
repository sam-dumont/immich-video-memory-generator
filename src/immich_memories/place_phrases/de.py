"""German: "in Italien", "in der Schweiz", "im Iran", "in den USA", "auf Kreta"."""

from __future__ import annotations

from immich_memories.place_phrases.compose import Rules, compose
from immich_memories.place_phrases.place import Place

# Countries that take an article, by CLDR code, as the dative after "in"/"auf".
_COUNTRY = {
    "CH": "in der Schweiz",
    "TR": "in der Türkei",
    "SK": "in der Slowakei",
    "MN": "in der Mongolei",
    "UA": "in der Ukraine",
    "DO": "in der Dominikanischen Republik",
    "CF": "in der Zentralafrikanischen Republik",
    "US": "in den USA",
    "NL": "in den Niederlanden",
    "AE": "in den Vereinigten Arabischen Emiraten",
    "GB": "im Vereinigten Königreich",
    "IR": "im Iran",
    "IQ": "im Irak",
    "LB": "im Libanon",
    "YE": "im Jemen",
    "SD": "im Sudan",
    "TD": "im Tschad",
    "SN": "im Senegal",
    "NE": "im Niger",
    "CG": "im Kongo",
    "VA": "im Vatikan",
    "PH": "auf den Philippinen",
    "MV": "auf den Malediven",
    "SC": "auf den Seychellen",
    "BS": "auf den Bahamas",
    "CY": "auf Zypern",
    "MT": "auf Malta",
    "IS": "auf Island",
    "MU": "auf Mauritius",
    "JM": "auf Jamaika",
    "CU": "auf Kuba",
    "BB": "auf Barbados",
    "MG": "auf Madagaskar",
}


# Islands and regions a trip is named after, by their English name.
_HEADS = {
    "Crete": "auf Kreta",
    "Cyprus": "auf Zypern",
    "Rhodes": "auf Rhodos",
    "Corfu": "auf Korfu",
    "Santorini": "auf Santorin",
    "Mykonos": "auf Mykonos",
    "Zakynthos": "auf Zakynthos",
    "Kos": "auf Kos",
    "Mallorca": "auf Mallorca",
    "Menorca": "auf Menorca",
    "Ibiza": "auf Ibiza",
    "Tenerife": "auf Teneriffa",
    "Gran Canaria": "auf Gran Canaria",
    "Lanzarote": "auf Lanzarote",
    "Fuerteventura": "auf Fuerteventura",
    "Madeira": "auf Madeira",
    "Oléron": "auf Oléron",
    "Bali": "auf Bali",
    "Oahu": "auf Oahu",
    "Maui": "auf Maui",
    "Kauai": "auf Kauai",
    "Sicily": "auf Sizilien",
    "Sardinia": "auf Sardinien",
    "Corsica": "auf Korsika",
    "Apulia": "in Apulien",
    "Saxony": "in Sachsen",
    "Lower Saxony": "in Niedersachsen",
    "Bavaria": "in Bayern",
    "Tuscany": "in der Toskana",
    "Lombardy": "in der Lombardei",
    "Piedmont": "im Piemont",
    "Campania": "in Kampanien",
    "Calabria": "in Kalabrien",
    "Liguria": "in Ligurien",
    "Veneto": "in Venetien",
    "Catalonia": "in Katalonien",
    "Andalusia": "in Andalusien",
    "Balearic Islands": "auf den Balearen",
    "Canary Islands": "auf den Kanaren",
    "Brittany": "in der Bretagne",
    "Normandy": "in der Normandie",
    "Attica": "in Attika",
    "Scotland": "in Schottland",
    "England": "in England",
    "Wales": "in Wales",
    "Flanders": "in Flandern",
    "Wallonia": "in Wallonien",
    "Tyrol": "in Tirol",
    "California": "in Kalifornien",
    "Nevada": "in Nevada",
    "Utah": "in Utah",
    "Arizona": "in Arizona",
    "Florida": "in Florida",
    "Texas": "in Texas",
    "Colorado": "in Colorado",
    "Oregon": "in Oregon",
    "Hawaii": "auf Hawaii",
}


def _country(name: str, code: str | None) -> str | None:
    return _COUNTRY.get(code or "", f"in {name}")


def _city(name: str) -> str:
    return f"in {name}"


_RULES = Rules("de", _country, _city, _HEADS, "und")


def phrase(place: Place) -> str | None:
    """This language's phrase for the trip's place, or None when a piece is unknown."""
    return compose(place, _RULES)
