"""The fixture library is exactly what CREDITS.md says it is.

Every photograph the demos and screenshots show ships with the repository, so
each one must be a public-domain file whose provenance is on record. This pins
the record to the bytes: every credited file exists and hashes as credited,
every file present is credited, every licence is CC0, and the set stays small
enough to clone without noticing.
"""

from __future__ import annotations

import hashlib
import re

from tests.e2e.fake_library import (
    CARRIERS,
    HOME,
    LAKE,
    LIBRARY,
    LIBRARY_DIR,
    SCENES,
    STORIES,
    STORY_OF,
)

_ROW = re.compile(
    r"^\| `([^`]+)` \| (.+?) \| (.+?) \| (\S+) \| (CC0 1\.0) \| (\d{4}-\d{2}-\d{2}) \| `([0-9a-f]{64})` \|$"
)
_MAX_LIBRARY_MB = 60


def _credited() -> dict[str, tuple[str, str, str, str, str]]:
    rows = {}
    for line in (LIBRARY_DIR / "CREDITS.md").read_text().splitlines():
        match = _ROW.match(line)
        if match:
            name, title, author, url, licence, fetched, digest = match.groups()
            rows[name] = (title, author, url, licence, digest)
    return rows


def test_every_credited_file_exists_and_hashes_as_credited() -> None:
    rows = _credited()
    assert rows, "CREDITS.md holds no rows"
    for name, (_title, _author, _url, _licence, digest) in rows.items():
        path = LIBRARY_DIR / name
        assert path.exists(), name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, name


def test_every_file_in_the_library_is_credited_as_cc0() -> None:
    rows = _credited()
    present = {path.name for path in LIBRARY_DIR.glob("*.jpg")}
    assert present == set(rows), present ^ set(rows)
    assert {row[3] for row in rows.values()} == {"CC0 1.0"}
    assert all(row[2].startswith("https://") for row in rows.values())


def test_the_library_stays_small_enough_to_clone() -> None:
    total = sum(path.stat().st_size for path in LIBRARY_DIR.glob("*.jpg"))
    assert total < _MAX_LIBRARY_MB * 1024 * 1024, f"{total / 1e6:.1f} MB"


def test_every_scene_has_a_file_and_the_month_reads_as_a_story() -> None:
    assert all(any(picture.scene == scene.key for picture in LIBRARY) for scene in SCENES)
    assert len(LIBRARY) >= 120
    assert sum(1 for picture in LIBRARY if picture.is_video) >= 10
    # Every story with a purpose carries at least one picture, in capture order.
    assert {story.key for story in STORIES} == {story.key for story in STORY_OF.values()}
    assert [picture.taken_at for picture in CARRIERS] == sorted(
        picture.taken_at for picture in CARRIERS
    )
    # The holiday sits far enough from home for trip detection (about 590 km).
    from immich_memories.analysis.trip_detection import haversine_km

    away = haversine_km(HOME.latitude, HOME.longitude, LAKE.latitude, LAKE.longitude)
    assert 500 < away < 700
    assert any(picture.place is LAKE for picture in CARRIERS)


def test_every_picture_has_a_reason_to_be_in_or_out() -> None:
    for picture in LIBRARY:
        assert picture.caption
        assert (picture.story_key is None) != (
            picture.drop_reason is None
        ) or picture.sequence > 1, picture.asset_id
