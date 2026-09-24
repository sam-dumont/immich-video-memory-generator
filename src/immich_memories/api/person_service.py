"""Person-related API service."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from immich_memories.api.models import Person

RequestFn = Callable[..., Any]


class PersonService:
    """Person retrieval operations against the Immich API."""

    def __init__(self, request_fn: RequestFn) -> None:
        self._request = request_fn

    async def get_all_people(self, with_hidden: bool = False) -> list[Person]:
        """Every person Immich knows, following its pages until it says there are no more.

        /people answers 500 at a time by default (1000 at most), and a library
        with years of face recognition holds thousands.
        """
        people: list[Person] = []
        page = 1
        while True:
            params = {"withHidden": str(with_hidden).lower(), "page": page, "size": 1000}
            data = await self._request("GET", "/people", params=params)
            if not isinstance(data, dict):
                # WHY: a server answering a bare list has no pages to follow
                return people + [Person(**p) for p in data]
            people.extend(Person(**p) for p in data.get("people", []))
            if not data.get("hasNextPage"):
                return people
            page += 1

    async def get_person(self, person_id: str) -> Person:
        """Get a specific person by ID."""
        data = await self._request("GET", f"/people/{person_id}")
        return Person(**data)

    async def get_person_thumbnail(self, person_id: str) -> bytes:
        """The face crop Immich shows for this person."""
        return await self._request("GET", f"/people/{person_id}/thumbnail")

    async def get_person_asset_count(self, person_id: str) -> int:
        """Get total asset count for a person via /people/{id}/statistics."""
        data = await self._request("GET", f"/people/{person_id}/statistics")
        return data.get("assets", 0)

    async def get_person_by_name(self, name: str) -> Person | None:
        """Find a person by name (case-insensitive)."""
        people = await self.get_all_people(with_hidden=True)
        name_lower = name.lower()
        for person in people:
            if person.name.lower() == name_lower:
                return person
        return None
