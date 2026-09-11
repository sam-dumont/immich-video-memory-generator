"""Complete date-range records do not depend on metadata page size."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from math import ceil
from unittest.mock import patch

import pytest

from immich_memories.api import search_service
from immich_memories.api.models import Asset
from immich_memories.api.search_service import SearchService
from immich_memories.timeperiod import DateRange

START = datetime(2030, 4, 1, tzinfo=UTC)
END = datetime(2030, 4, 30, 23, 59, 59, tzinfo=UTC)
WINDOW = DateRange(START, END)


def _record(index, kind, count):
    # Repeated timestamps exercise stable ordering across different page boundaries.
    taken = END if index == count - 1 else START + timedelta(minutes=index // 3)
    return {
        "id": f"asset-{index}",
        "type": kind,
        "fileCreatedAt": taken.isoformat(),
        "fileModifiedAt": END.isoformat(),
        "updatedAt": END.isoformat(),
        "originalFileName": f"source-{index}.jpg",
        "width": 2048,
        "height": 1536,
        "isFavorite": index % 7 == 0,
        "checksum": f"checksum-{index}",
        "livePhotoVideoId": f"motion-{index}" if kind == "IMAGE" and index % 2 else None,
        "people": [{"id": "subject", "name": "Subject"}]
        + ([{"id": "companion", "name": "Companion"}] if index % 2 else []),
        "exifInfo": {"description": f"metadata-{index}", "latitude": 50.1, "longitude": 4.2},
    }


class MetadataTransport:
    """A deterministic API fixture: filters first, then slices an ordered response."""

    def __init__(self, records):
        self.records = sorted(records, key=lambda row: row["fileCreatedAt"], reverse=True)
        self.requests = []

    def matching(self, payload):
        after = datetime.fromisoformat(payload["takenAfter"])
        before = datetime.fromisoformat(payload["takenBefore"])
        people = set(payload.get("personIds", []))
        return [
            row
            for row in self.records
            if row["type"] == payload["type"]
            and after <= datetime.fromisoformat(row["fileCreatedAt"]) <= before
            and people <= {person["id"] for person in row["people"]}
        ]

    async def __call__(self, method, path, *, json):
        assert (method, path) == ("POST", "/search/metadata")
        assert json["withExif"] is True and json["withPeople"] is True
        assert 1 <= json["size"] <= 1000
        self.requests.append(json.copy())
        rows = self.matching(json)
        start = (json["page"] - 1) * json["size"]
        end = start + json["size"]
        return {
            "assets": {
                "items": rows[start:end],
                "total": len(rows),
                "nextPage": str(json["page"] + 1) if end < len(rows) else None,
            }
        }


async def _fetch(service, route, window, progress):
    if route == "videos":
        return await service.get_videos_for_date_range(window, progress)
    if route == "person-videos":
        return await service.get_videos_for_person_and_date_range("subject", window, progress)
    if route == "person-photos":
        return await service.get_photos_for_date_range(window, progress, person_id="subject")
    return await service.get_photos_for_date_range(window, progress)


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["videos", "person-videos", "photos", "person-photos"])
@pytest.mark.parametrize("count", [0, 37, 1000, 1001, 2043])
async def test_old_and_large_pages_preserve_every_ordered_record_and_filter(route, count):
    kind = "VIDEO" if "videos" in route else "IMAGE"
    records = [_record(index, kind, count) for index in range(count)]
    noise = _record(count + 1, kind, count + 2)
    records += [
        dict(noise, id="before", fileCreatedAt=(START - timedelta(seconds=1)).isoformat()),
        dict(noise, id="after", fileCreatedAt=(END + timedelta(seconds=1)).isoformat()),
        dict(noise, id="other-type", type="IMAGE" if kind == "VIDEO" else "VIDEO"),
    ]
    if route.startswith("person-"):
        records.append(dict(noise, id="other-person", people=[{"id": "other", "name": "Other"}]))
    # Equal instants with a non-UTC offset must produce the original exact UTC filters.
    offset = timezone(timedelta(hours=2))
    window = DateRange(START.astimezone(offset), END.astimezone(offset))
    old, current = MetadataTransport(records), MetadataTransport(records)
    old_progress, new_progress = [], []
    with patch.object(search_service, "_DATE_RANGE_PAGE_SIZE", 100):
        before = await _fetch(
            SearchService(old), route, window, lambda n, _: old_progress.append(n)
        )
    after = await _fetch(SearchService(current), route, window, lambda n, _: new_progress.append(n))
    expected = [Asset(**row) for row in current.matching(current.requests[0])]
    expected.sort(key=lambda asset: asset.file_created_at)
    assert (
        [a.model_dump() for a in after]
        == [a.model_dump() for a in before]
        == [a.model_dump() for a in expected]
    )
    assert len(after) == count
    assert len(old.requests) == max(1, ceil(count / 100))
    assert len(current.requests) == max(1, ceil(count / 1000))
    assert [row["page"] for row in current.requests] == list(range(1, len(current.requests) + 1))
    expected_filters = {
        "withExif": True,
        "withPeople": True,
        "type": kind,
        "takenAfter": START.isoformat(),
        "takenBefore": END.isoformat(),
    }
    if route.startswith("person-"):
        expected_filters["personIds"] = ["subject"]
    for requests, size in ((old.requests, 100), (current.requests, 1000)):
        assert all(row["size"] == size for row in requests)
        assert all(
            {k: v for k, v in row.items() if k not in {"page", "size"}} == expected_filters
            for row in requests
        )
    assert old_progress[-1] == new_progress[-1] == count
    assert new_progress == sorted(new_progress)


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["videos", "person-videos", "photos", "person-photos"])
async def test_next_page_controls_traversal_even_after_partial_or_empty_page(route):
    kind = "VIDEO" if "videos" in route else "IMAGE"
    calls = []

    async def request(method, path, *, json):
        calls.append(json)
        page = json["page"]
        rows = [] if page == 2 else [_record(3 - page, kind, 4)]
        return {"assets": {"items": rows, "nextPage": str(page + 1) if page < 3 else None}}

    result = await _fetch(SearchService(request), route, WINDOW, None)
    assert [row["page"] for row in calls] == [1, 2, 3]
    assert all(row["size"] == 1000 for row in calls)
    assert {asset.id for asset in result} == {"asset-0", "asset-2"}


@pytest.mark.asyncio
async def test_all_person_intersection_survives_page_boundaries_and_retains_full_context():
    records = [_record(index, "IMAGE", 2103) for index in range(2103)]
    old, current = MetadataTransport(records), MetadataTransport(records)
    with patch.object(search_service, "_DATE_RANGE_PAGE_SIZE", 100):
        before = await SearchService(old).get_photos_for_date_range(
            WINDOW, person_ids=["subject", "companion"]
        )
    after = await SearchService(current).get_photos_for_date_range(
        WINDOW, person_ids=["subject", "companion"]
    )
    assert [a.model_dump() for a in after] == [a.model_dump() for a in before]
    assert len(after) == 1051
    assert all({p.id for p in asset.people} == {"subject", "companion"} for asset in after)
    # The unfiltered context remains broader than the co-presence selection.
    context = await SearchService(MetadataTransport(records)).get_photos_for_date_range(WINDOW)
    assert len(context) == 2103
    assert {a.id for a in after} < {a.id for a in context}
    assert len(current.requests) == 5
