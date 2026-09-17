"""Localised place names for the places one cut shows, cached on disk.

Immich geocodes with GeoNames and stores English. babel translates the country
offline, but no offline table holds city names, so a film in French keeps
saying "Nicosia" until a geocoder is allowed to answer in French.

With `network.geocoding` on this asks Nominatim once per distinct place on the
CUT, never per asset and never over the library: a finished cut carries tens of
clips, and their coordinates collapse to a handful of places once rounded. The
rounding is 2 decimals, about a kilometre, which is also what keeps the cache
useful across runs and keeps a home address out of the request.

Nominatim's usage policy is one request a second from one machine, so the calls
go through geopy's RateLimiter. Anything that fails leaves the English name in
place; a film is never held up by a geocoder.
"""

from __future__ import annotations

import hashlib
import json
import logging
import tempfile
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)

# About 1.1 km at the equator: fine enough to name a town, coarse enough that
# two clips from the same afternoon ask one question between them.
_PRECISION = 2
_SCHEMA_VERSION = 1


def _rounded(latitude: float, longitude: float) -> tuple[float, float]:
    return round(latitude, _PRECISION), round(longitude, _PRECISION)


def nominatim_place_reader(locale: str) -> Callable[[float, float], str | None]:
    """A rate-limited reverse geocoder answering in `locale`, city level.

    Built only when the caller has already decided geocoding is allowed.
    """
    from geopy.extra.rate_limiter import RateLimiter
    from geopy.geocoders import Nominatim

    geolocator = Nominatim(user_agent="immich-memories")
    reverse = RateLimiter(geolocator.reverse, min_delay_seconds=1)

    def read(latitude: float, longitude: float) -> str | None:
        location = reverse(f"{latitude}, {longitude}", zoom=14, language=locale)
        if location is None:
            return None
        address = location.raw.get("address", {})
        town = (
            address.get("city")
            or address.get("town")
            or address.get("village")
            or address.get("municipality")
            or address.get("suburb")
        )
        country = address.get("country")
        if town and country:
            return f"{town}, {country}"
        return country or town

    return read


class PlaceNameCache:
    """Place names by rounded coordinate and locale, read once and kept.

    The store lives beside the familiar-place cache and is keyed on the locale
    as well as the coordinate, so switching a film to French does not serve it
    the English answer the last run paid for.
    """

    def __init__(
        self,
        cache_root: Path,
        locale: str,
        reader: Callable[[float, float], str | None] | None = None,
    ) -> None:
        self._reader = reader
        digest = hashlib.sha256(locale.encode()).hexdigest()[:16]
        self._path = cache_root / "place-names" / f"{digest}.json"
        self._known: dict[str, str] = self._read()
        self._dirty = False

    def _read(self) -> dict[str, str]:
        try:
            payload = json.loads(self._path.read_text())
            if payload.get("schema_version") != _SCHEMA_VERSION:
                return {}
            return {str(k): str(v) for k, v in payload["names"].items()}
        except (OSError, KeyError, ValueError, TypeError):
            return {}

    def flush(self) -> None:
        """Persist what this run learned. A write failure is not a render failure."""
        if not self._dirty:
            return
        payload = {"schema_version": _SCHEMA_VERSION, "names": self._known}
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with tempfile.NamedTemporaryFile("w", dir=self._path.parent, delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(payload, stream, separators=(",", ":"))
                stream.close()
                temporary.replace(self._path)
            self._dirty = False
        except OSError as error:
            logger.debug("Could not write the place-name cache: %s", error)

    def name_for(self, latitude: float, longitude: float, fallback: str | None) -> str | None:
        """The place's name in the film's language, or `fallback` when nobody knows."""
        latitude, longitude = _rounded(latitude, longitude)
        key = f"{latitude},{longitude}"
        if key in self._known:
            return self._known[key]
        if self._reader is None:
            return fallback
        try:
            answer = self._reader(latitude, longitude)
        except Exception as error:  # noqa: BLE001
            # WHY so broad: geopy raises its own hierarchy, and only part of it
            # inherits from OSError. A geocoder that is down, rate limiting or
            # refusing must cost the film its better names, never the film.
            logger.info("Reverse geocoding unavailable (%s); keeping stored names", error)
            self._reader = None
            return fallback
        if not answer:
            return fallback
        self._known[key] = answer
        self._dirty = True
        return answer
