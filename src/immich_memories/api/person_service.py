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
        """Get all people from Immich."""
        params = {"withHidden": str(with_hidden).lower()}
        data = await self._request("GET", "/people", params=params)

        people_data = data.get("people", []) if isinstance(data, dict) else data
        return [Person(**p) for p in people_data]

    async def get_person(self, person_id: str) -> Person:
        """Get a specific person by ID."""
        data = await self._request("GET", f"/people/{person_id}")
        return Person(**data)

    async def get_person_by_name(self, name: str) -> Person | None:
        """Find a person by name (case-insensitive)."""
        people = await self.get_all_people(with_hidden=True)
        name_lower = name.lower()
        for person in people:
            if person.name.lower() == name_lower:
                return person
        return None
