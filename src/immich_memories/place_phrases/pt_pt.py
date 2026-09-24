"""Portuguese (Portugal): as Brazil, but no article before a few European countries ("em França")."""

from __future__ import annotations

from immich_memories.place_phrases.compose import Rules, compose
from immich_memories.place_phrases.place import Place
from immich_memories.place_phrases.pt_br import _NO_ARTICLE, city_phrase, country_phrase

_NO_ARTICLE_PT = _NO_ARTICLE | {"FR", "ES", "IT", "CY", "MA"}

_HEADS = {
    "Crete": "em Creta",
    "Cyprus": "em Chipre",
    "Rhodes": "em Rodes",
    "Corfu": "em Corfu",
    "Santorini": "em Santorini",
    "Mykonos": "em Míconos",
    "Kos": "em Cós",
    "Mallorca": "em Maiorca",
    "Menorca": "em Menorca",
    "Ibiza": "em Ibiza",
    "Tenerife": "em Tenerife",
    "Gran Canaria": "na Grã-Canária",
    "Lanzarote": "em Lanzarote",
    "Fuerteventura": "em Fuerteventura",
    "Madeira": "na Madeira",
    "Oléron": "em Oléron",
    "Bali": "em Bali",
    "Oahu": "em Oahu",
    "Maui": "em Maui",
    "Kauai": "em Kauai",
    "Sicily": "na Sicília",
    "Sardinia": "na Sardenha",
    "Corsica": "na Córsega",
    "Apulia": "na Apúlia",
    "Bavaria": "na Baviera",
    "Tuscany": "na Toscana",
    "Lombardy": "na Lombardia",
    "Piedmont": "no Piemonte",
    "Catalonia": "na Catalunha",
    "Andalusia": "na Andaluzia",
    "Balearic Islands": "nas Baleares",
    "Canary Islands": "nas Canárias",
    "Brittany": "na Bretanha",
    "Normandy": "na Normandia",
    "Scotland": "na Escócia",
    "England": "em Inglaterra",
    "Wales": "no País de Gales",
    "Flanders": "na Flandres",
    "Tyrol": "no Tirol",
    "California": "na Califórnia",
    "Florida": "na Flórida",
    "Texas": "no Texas",
    "Hawaii": "no Havaí",
    "Saxony": "na Saxónia",
    "Lower Saxony": "na Baixa Saxónia",
    "Campania": "na Campânia",
    "Calabria": "na Calábria",
    "Liguria": "na Ligúria",
    "Veneto": "no Véneto",
}


_RULES = Rules(
    "pt-PT", lambda name, code: country_phrase(name, code, _NO_ARTICLE_PT), city_phrase, _HEADS, "e"
)


def phrase(place: Place) -> str | None:
    """The European Portuguese phrase for the trip's place, or None when a piece is unknown."""
    return compose(place, _RULES)
