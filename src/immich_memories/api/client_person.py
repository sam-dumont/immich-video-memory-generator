"""Person-related API methods mixin."""

from __future__ import annotations

from immich_memories.api.models import Person


class PersonMixin:
    """Mixin providing person-related API methods for ImmichClient."""

    async def get_all_people(self, with_hidden: bool = False) -> list[Person]:
        """Get all people from Immich.

        Args:
            with_hidden: Include hidden people.

        Returns:
            List of Person objects.
        """
        params = {"withHidden": str(with_hidden).lower()}
        data = await self._request("GET", "/people", params=params)

        # Handle both formats: {"people": [...]} or direct list
        people_data = data.get("people", []) if isinstance(data, dict) else data

        return [Person(**p) for p in people_data]

    async def get_person(self, person_id: str) -> Person:
        """Get a specific person by ID.

        Args:
            person_id: The person's ID.

        Returns:
            Person object.
        """
        data = await self._request("GET", f"/people/{person_id}")
        return Person(**data)

    async def get_person_by_name(self, name: str) -> Person | None:
        """Find a person by name.

        Args:
            name: Name to search for (case-insensitive).

        Returns:
            Person if found, None otherwise.
        """
        people = await self.get_all_people(with_hidden=True)
        name_lower = name.lower()
        for person in people:
            if person.name.lower() == name_lower:
                return person
        return None
