"""Name a trip at the scale its pictures cover.

A trip is named after the smallest place that holds (almost) all of its located
pictures: the city, else the island, else the region, else two regions, else
the country. "Almost" is `COVERING_SHARE` of the pictures, counted per picture
rather than per day: a day with forty pictures in one town weighs more than a
day in transit with two, and that is what the film will show.

The places come from Immich's own reverse geocoding, which every asset carries
(`exifInfo.city/state/country`, from its offline GeoNames data), so this makes
no outside call. Islands come from a small bundled table (`place_names`),
because GeoNames files most islands under an administrative region.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

from immich_memories.api.models import Asset
from immich_memories.place_names import island_at, short_place_name

# The share of a trip's located pictures a place must hold to name the trip.
COVERING_SHARE = 0.85
# In a two-region trip, the smaller region still has to be a real part of it.
_SECOND_REGION_SHARE = 0.15


@dataclass(frozen=True)
class TripPlace:
    """A trip's name and the scale it was chosen at."""

    name: str
    scale: str  # "city" | "island" | "region" | "regions" | "country" | "countries"


@dataclass(frozen=True)
class _Located:
    city: str | None
    region: str | None
    country: str
    island: str | None


def _located(assets: Iterable[Asset]) -> list[_Located]:
    located = []
    for asset in assets:
        exif = asset.exif_info
        if exif is None or not exif.country:
            continue
        island = None
        if exif.latitude is not None and exif.longitude is not None:
            island = island_at(exif.latitude, exif.longitude, exif.country)
        region = short_place_name(exif.state)
        located.append(_Located(short_place_name(exif.city), region, exif.country, island))
    return located


def _covering(keys: list[str | None], total: int) -> str | None:
    """The most common key, when it holds at least COVERING_SHARE of `total`."""
    counts = Counter(k for k in keys if k is not None)
    if not counts:
        return None
    top, count = counts.most_common(1)[0]
    return top if count / total >= COVERING_SHARE else None


def _with_country(place: str, country: str) -> str:
    return country if place == country else f"{place}, {country}"


def _two_regions(places: list[_Located], country: str) -> str | None:
    counts = Counter(p.region for p in places if p.region and p.country == country)
    if len(counts) < 2:
        return None
    (first, n1), (second, n2) = counts.most_common(2)
    total = len(places)
    if (n1 + n2) / total >= COVERING_SHARE and n2 / total >= _SECOND_REGION_SHARE:
        return f"{first} and {second}, {country}"
    return None


def _countries_in_order(places: list[_Located]) -> list[str]:
    return list(dict.fromkeys(p.country for p in places))


def trip_place(assets: Iterable[Asset]) -> TripPlace | None:
    """The smallest place holding (almost) all of the trip's located pictures.

    None when no picture carries a country: Immich has not reverse geocoded
    them, and the caller falls back to whatever else it has.
    """
    places = _located(assets)
    if not places:
        return None
    total = len(places)
    country = _covering([p.country for p in places], total)
    if country is None:
        return TripPlace(" → ".join(_countries_in_order(places)), "countries")
    city = _covering([p.city for p in places], total)
    if city is not None:
        return TripPlace(_with_country(city, country), "city")
    island = _covering([p.island for p in places], total)
    if island is not None:
        return TripPlace(_with_country(island, country), "island")
    region = _covering([p.region for p in places], total)
    if region is not None:
        return TripPlace(_with_country(region, country), "region")
    if two := _two_regions(places, country):
        return TripPlace(two, "regions")
    return TripPlace(country, "country")
