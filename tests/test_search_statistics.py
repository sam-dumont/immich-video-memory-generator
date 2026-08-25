"""Counting how many assets hold a set of people, without fetching any."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from immich_memories.api.search_service import SearchService


class TestCountAssetsWithPeople:
    @pytest.mark.asyncio
    async def test_it_asks_for_a_count_rather_than_paging_the_assets(self):
        # WHY: the Immich HTTP API. The point of the endpoint is that a pair
        # costs one small answer instead of every asset both people are in.
        request = AsyncMock(return_value={"total": 42})

        total = await SearchService(request).count_assets_with_people(["p1", "p2"])

        assert total == 42
        request.assert_awaited_once_with(
            "POST", "/search/statistics", json={"personIds": ["p1", "p2"]}
        )

    @pytest.mark.asyncio
    async def test_an_answer_without_a_total_counts_as_nothing_shared(self):
        # WHY: the Immich HTTP API, standing in for a server whose statistics
        # response shape differs from the one this was written against.
        request = AsyncMock(return_value={})

        assert await SearchService(request).count_assets_with_people(["p1", "p2"]) == 0
