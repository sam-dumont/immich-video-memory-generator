"""Portuguese (Brazil): "em" contracted with the name's article ("na Itália", "no Japão")."""

from __future__ import annotations

from immich_memories.place_phrases.compose import Rules, compose
from immich_memories.place_phrases.place import Place

_NO_ARTICLE = frozenset(
    {"PT", "AO", "MZ", "CV", "TL", "ST", "IL", "CU", "MT", "SG", "HK", "MO", "MC", "AD", "SM"}
)
_NO_ARTICLE_PT = _NO_ARTICLE | {"FR", "ES", "IT", "CY", "MA"}
_PLURAL = {
    "US": "nos",
    "NL": "nos",
    "AE": "nos",
    "PH": "nas",
    "MV": "nas",
    "SC": "nas",
    "BS": "nas",
}
_CITIES_WITH_ARTICLE = {
    "Porto": "no Porto",
    "Rio de Janeiro": "no Rio de Janeiro",
    "Cairo": "no Cairo",
}


def _contracted(name: str) -> str:
    feminine = name.endswith("a") and not name.endswith("á")
    return f"{'na' if feminine else 'no'} {name}"


def country_phrase(name: str, code: str | None, no_article: frozenset[str]) -> str:
    """ "na Itália", "nos Estados Unidos", "em Portugal"."""
    if code in _PLURAL:
        return f"{_PLURAL[code]} {name}"
    return f"em {name}" if code in no_article else _contracted(name)


def city_phrase(name: str) -> str:
    """ "em Lisboa", "no Porto"."""
    return _CITIES_WITH_ARTICLE.get(name, f"em {name}")


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
    "England": "na Inglaterra",
    "Wales": "no País de Gales",
    "Flanders": "na Flandres",
    "Tyrol": "no Tirol",
    "California": "na Califórnia",
    "Florida": "na Flórida",
    "Texas": "no Texas",
    "Hawaii": "no Havaí",
    "Saxony": "na Saxônia",
    "Lower Saxony": "na Baixa Saxônia",
    "Campania": "na Campânia",
    "Calabria": "na Calábria",
    "Liguria": "na Ligúria",
    "Veneto": "no Vêneto",
}


_RULES = Rules(
    "pt-BR", lambda name, code: country_phrase(name, code, _NO_ARTICLE), city_phrase, _HEADS, "e"
)


def phrase(place: Place) -> str | None:
    """The Brazilian Portuguese phrase for the trip's place, or None when a piece is unknown."""
    return compose(place, _RULES)
