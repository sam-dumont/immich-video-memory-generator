"""Polish: the preposition and the locative case ("we Włoszech", "na Krecie").

The locative cannot be derived from a name safely, so only the places listed
here get a phrase; any other place returns None and the title prints it
without one.
"""

from __future__ import annotations

from immich_memories.place_phrases.compose import Rules, compose
from immich_memories.place_phrases.place import Place

_COUNTRY = {
    "AT": "w Austrii", "BE": "w Belgii", "BG": "w Bułgarii", "HR": "w Chorwacji",
    "CY": "na Cyprze", "CZ": "w Czechach", "DK": "w Danii", "EE": "w Estonii",
    "FI": "w Finlandii", "FR": "we Francji", "DE": "w Niemczech", "GR": "w Grecji",
    "HU": "na Węgrzech", "IS": "na Islandii", "IE": "w Irlandii", "IT": "we Włoszech",
    "LV": "na Łotwie", "LT": "na Litwie", "LU": "w Luksemburgu", "MT": "na Malcie",
    "NL": "w Holandii", "NO": "w Norwegii", "PL": "w Polsce", "PT": "w Portugalii",
    "RO": "w Rumunii", "SK": "na Słowacji", "SI": "w Słowenii", "ES": "w Hiszpanii",
    "SE": "w Szwecji", "CH": "w Szwajcarii", "GB": "w Wielkiej Brytanii", "UA": "w Ukrainie",
    "TR": "w Turcji", "US": "w Stanach Zjednoczonych", "CA": "w Kanadzie", "MX": "w Meksyku",
    "BR": "w Brazylii", "AR": "w Argentynie", "JP": "w Japonii", "CN": "w Chinach",
    "TH": "w Tajlandii", "VN": "w Wietnamie", "ID": "w Indonezji", "IN": "w Indiach",
    "EG": "w Egipcie", "MA": "w Maroku", "TN": "w Tunezji", "AU": "w Australii",
    "NZ": "w Nowej Zelandii", "AE": "w Zjednoczonych Emiratach Arabskich", "IL": "w Izraelu",
    "ME": "w Czarnogórze", "AL": "w Albanii", "RS": "w Serbii", "GE": "w Gruzji",
    "AM": "w Armenii", "MV": "na Malediwach", "PH": "na Filipinach", "LK": "na Sri Lance",
    "CU": "na Kubie", "DO": "na Dominikanie", "KE": "w Kenii", "TZ": "w Tanzanii",
    "PE": "w Peru", "CL": "w Chile", "CO": "w Kolumbii", "JO": "w Jordanii",
    "KR": "w Korei Południowej", "SG": "w Singapurze", "MU": "na Mauritiusie",
    "SC": "na Seszelach", "ZA": "w Republice Południowej Afryki",
}  # fmt: skip


# Islands and regions a trip is named after, by their English name.
_HEADS = {
    "Crete": "na Krecie",
    "Cyprus": "na Cyprze",
    "Rhodes": "na Rodos",
    "Corfu": "na Korfu",
    "Santorini": "na Santorini",
    "Mykonos": "na Mykonos",
    "Kos": "na Kos",
    "Mallorca": "na Majorce",
    "Menorca": "na Minorce",
    "Ibiza": "na Ibizie",
    "Tenerife": "na Teneryfie",
    "Gran Canaria": "na Gran Canarii",
    "Lanzarote": "na Lanzarote",
    "Fuerteventura": "na Fuerteventurze",
    "Madeira": "na Maderze",
    "Bali": "na Bali",
    "Sicily": "na Sycylii",
    "Sardinia": "na Sardynii",
    "Corsica": "na Korsyce",
    "Apulia": "w Apulii",
    "Saxony": "w Saksonii",
    "Lower Saxony": "w Dolnej Saksonii",
    "Bavaria": "w Bawarii",
    "Tuscany": "w Toskanii",
    "Lombardy": "w Lombardii",
    "Piedmont": "w Piemoncie",
    "Campania": "w Kampanii",
    "Calabria": "w Kalabrii",
    "Liguria": "w Ligurii",
    "Catalonia": "w Katalonii",
    "Andalusia": "w Andaluzji",
    "Balearic Islands": "na Balearach",
    "Canary Islands": "na Wyspach Kanaryjskich",
    "Brittany": "w Bretanii",
    "Normandy": "w Normandii",
    "Attica": "w Attyce",
    "Scotland": "w Szkocji",
    "England": "w Anglii",
    "Wales": "w Walii",
    "Flanders": "we Flandrii",
    "Wallonia": "w Walonii",
    "Tyrol": "w Tyrolu",
    "California": "w Kalifornii",
    "Nevada": "w Newadzie",
    "Utah": "w Utah",
    "Arizona": "w Arizonie",
    "Florida": "na Florydzie",
    "Texas": "w Teksasie",
    "Colorado": "w Kolorado",
    "Oregon": "w Oregonie",
    "Hawaii": "na Hawajach",
}

# Cities by their English name: the locative cannot be derived, so only these.
_CITIES = {
    "Paris": "w Paryżu",
    "Rome": "w Rzymie",
    "London": "w Londynie",
    "Berlin": "w Berlinie",
    "Barcelona": "w Barcelonie",
    "Las Vegas": "w Las Vegas",
    "New York": "w Nowym Jorku",
    "Vienna": "w Wiedniu",
    "Prague": "w Pradze",
    "Amsterdam": "w Amsterdamie",
    "Lisbon": "w Lizbonie",
    "Madrid": "w Madrycie",
    "Brussels": "w Brukseli",
    "Venice": "w Wenecji",
    "Florence": "we Florencji",
    "Krakow": "w Krakowie",
    "Warsaw": "w Warszawie",
    "Gdansk": "w Gdańsku",
}


def _country(_name: str, code: str | None) -> str | None:
    return _COUNTRY.get(code or "")


_RULES = Rules("pl", _country, _CITIES.get, _HEADS, "i")


def phrase(place: Place) -> str | None:
    """ "na Krecie, Grecja", "we Włoszech", "w Las Vegas, Stany Zjednoczone", or None."""
    return compose(place, _RULES)
