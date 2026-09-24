"""Real-Immich gate: the product reads a seeded library whole, on v2 and v3.

Expectations come from the fixture script (tests/e2e/fake_library.py), never
from what the server happened to answer last time.
"""

from __future__ import annotations

import pytest

from immich_memories.api.models import AssetType
from tests.e2e.fake_library import CAST, LAKE, LIBRARY, STORIES, carriers_of
from tests.integration.immich_fixtures import requires_immich
from tests.integration.immich_gate.conftest import BULK_YEAR, FIXTURE_MONTH
from tests.integration.immich_gate.media import BULK_ALBUM, BULK_COUNT, CAMERA

pytestmark = [requires_immich]

_VIDEOS = sorted(picture.filename for picture in LIBRARY if picture.is_video)
_STILLS = sorted(picture.filename for picture in LIBRARY if not picture.is_video)


def test_connects_and_speaks_the_major_it_was_started_on(gate_client, gate_version):
    assert gate_client.validate_connection()
    assert f"v{gate_client.get_server_info().major}" == gate_version
    assert gate_client.get_api_version().value == gate_version


def test_the_fixture_month_reads_every_video_and_still(gate_client):
    videos = gate_client.get_videos_for_date_range(FIXTURE_MONTH)
    stills = gate_client.get_photos_for_date_range(FIXTURE_MONTH)

    assert sorted(asset.original_file_name for asset in videos) == _VIDEOS
    assert sorted(asset.original_file_name for asset in stills) == _STILLS


def test_a_still_carries_its_camera_and_a_video_its_place(gate_client):
    arrival = next(p for p in LIBRARY if p.scene == "trip-lake-arrival" and p.is_video)
    videos = {a.original_file_name: a for a in gate_client.get_videos_for_date_range(FIXTURE_MONTH)}
    stills = gate_client.get_photos_for_date_range(FIXTURE_MONTH)

    video = gate_client.get_asset(videos[arrival.filename].id)
    assert video.exif_info is not None
    assert video.exif_info.latitude == pytest.approx(LAKE.latitude, abs=1e-3)
    assert video.exif_info.longitude == pytest.approx(LAKE.longitude, abs=1e-3)
    assert {(s.exif_info.make, s.exif_info.model) for s in stills if s.exif_info} == {CAMERA}


def test_a_year_longer_than_one_search_page_reads_whole(gate_client):
    stills = gate_client.get_photos_for_date_range(BULK_YEAR)

    assert len({asset.id for asset in stills}) == BULK_COUNT


def test_an_album_longer_than_one_search_page_reads_whole(gate_client):
    album = gate_client.resolve_album(BULK_ALBUM)
    assets = gate_client.get_assets_for_album(album.id, asset_type=AssetType.IMAGE)

    assert album.asset_count == BULK_COUNT
    assert len({asset.id for asset in assets}) == BULK_COUNT


def test_people_tagged_by_hand_scope_the_videos_they_are_in(gate_client):
    assert set(CAST) <= {person.name for person in gate_client.get_all_people()}
    for name in CAST:
        person = gate_client.get_person_by_name(name)
        assert person is not None, name
        videos = gate_client.get_videos_for_person_and_date_range(person.id, FIXTURE_MONTH)
        expected = sorted(p.filename for p in LIBRARY if p.is_video and name in p.people)
        assert sorted(asset.original_file_name for asset in videos) == expected, name


def test_story_albums_resolve_by_name_with_their_pictures(gate_client):
    listed = {album.name: album for album in gate_client.list_albums()}
    for story in STORIES:
        carriers = carriers_of(story.key)
        if not carriers:
            continue
        album = gate_client.resolve_album(story.title)
        assert album.asset_count == len(carriers), story.title
        assert listed[story.title].id == album.id
        assert album.start is not None
        assert album.start.year == 2024
