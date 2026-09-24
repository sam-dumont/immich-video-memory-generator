"""French: the preposition and article each place takes.

A country follows the rules in `i18n_places.french_preposition` ("en Italie",
"au Portugal", "aux États-Unis", "à Chypre"). A city takes "à". An island or a
region has no rule that holds ("en Crète" but "à Majorque", "en Saxe" but
"dans les Pouilles", "au Nevada" but "dans l'Utah"), so each one a trip can be
named after is listed with its whole phrase, and one that is not listed gets
no phrase: the title then goes without a preposition rather than guess.
"""

from __future__ import annotations

from immich_memories.i18n_places import french_preposition, localise_country, localise_place
from immich_memories.place_phrases.place import Place

# English island or region name -> how a French title says "in" it.
_PHRASES = {
    # Islands
    "Crete": "en Crète",
    "Cyprus": "à Chypre",
    "Rhodes": "à Rhodes",
    "Corfu": "à Corfou",
    "Santorini": "à Santorin",
    "Mykonos": "à Mykonos",
    "Zakynthos": "à Zante",
    "Kos": "à Kos",
    "Mallorca": "à Majorque",
    "Menorca": "à Minorque",
    "Ibiza": "à Ibiza",
    "Tenerife": "à Tenerife",
    "Gran Canaria": "à la Grande Canarie",
    "Lanzarote": "à Lanzarote",
    "Fuerteventura": "à Fuerteventura",
    "Madeira": "à Madère",
    "Oléron": "à Oléron",
    "Bali": "à Bali",
    "Oahu": "à Oahu",
    "Maui": "à Maui",
    "Kauai": "à Kauai",
    "Big Island": "sur la Grande Île d'Hawaï",
    "Sicily": "en Sicile",
    "Sardinia": "en Sardaigne",
    "Corsica": "en Corse",
    # Regions
    "Apulia": "dans les Pouilles",
    "Saxony": "en Saxe",
    "Lower Saxony": "en Basse-Saxe",
    "Bavaria": "en Bavière",
    "Tuscany": "en Toscane",
    "Lombardy": "en Lombardie",
    "Piedmont": "dans le Piémont",
    "Campania": "en Campanie",
    "Calabria": "en Calabre",
    "Liguria": "en Ligurie",
    "Veneto": "en Vénétie",
    "Catalonia": "en Catalogne",
    "Andalusia": "en Andalousie",
    "Balearic Islands": "aux Baléares",
    "Canary Islands": "aux Canaries",
    "Brittany": "en Bretagne",
    "Normandy": "en Normandie",
    "Attica": "en Attique",
    "South Aegean": "en Égée-Méridionale",
    "Ionian Islands": "dans les îles Ioniennes",
    "Scotland": "en Écosse",
    "England": "en Angleterre",
    "Wales": "au pays de Galles",
    "Flanders": "en Flandre",
    "Wallonia": "en Wallonie",
    "Tyrol": "au Tyrol",
    "California": "en Californie",
    "Nevada": "au Nevada",
    "Utah": "dans l'Utah",
    "Arizona": "en Arizona",
    "Florida": "en Floride",
    "Texas": "au Texas",
    "Colorado": "au Colorado",
    "Oregon": "en Oregon",
    "Hawaii": "à Hawaï",
    "New York": "dans l'État de New York",
}


def _country_phrase(english: str) -> str:
    return f"{french_preposition(english)} {localise_country(english, 'fr')}"


def phrase(place: Place) -> str | None:
    """ "en Crète, Grèce", "aux États-Unis", "à Las Vegas, États-Unis", or None."""
    if place.kind == "countries":
        return None
    if not place.heads:
        return _country_phrase(place.english)
    if place.kind == "city":
        return f"à {localise_place(place.english, 'fr')}"
    parts = [_PHRASES.get(head) for head in place.heads]
    if not all(parts):
        return None
    heads = " et ".join(p for p in parts if p)
    return f"{heads}, {localise_country(place.country, 'fr')}" if place.country else heads
