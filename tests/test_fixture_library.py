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
import shutil
import subprocess
import sys
from pathlib import Path

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
    assert any(picture.place is LAKE for picture in CARRIERS)


def test_trip_detection_finds_exactly_the_lake_week() -> None:
    from immich_memories.analysis.trip_detection import detect_trips
    from immich_memories.api.models import Asset
    from tests.e2e.fake_immich import _asset_payload

    assets = [Asset.model_validate(_asset_payload(picture)) for picture in LIBRARY]
    trips = detect_trips(assets, HOME.latitude, HOME.longitude)
    assert [(trip.start_date.isoformat(), trip.end_date.isoformat()) for trip in trips] == [
        ("2024-06-21", "2024-06-27")
    ]
    # The Saturday in the woods is a day out, not a trip: it is under 50 km and one day long.
    assert trips[0].asset_count == sum(
        1 for picture in LIBRARY if picture.place is not HOME and picture.place.country == "France"
    )


def test_every_picture_has_a_reason_to_be_in_or_out() -> None:
    for picture in LIBRARY:
        assert picture.caption
        assert (picture.story_key is None) != (
            picture.drop_reason is None
        ) or picture.sequence > 1, picture.asset_id


def test_the_module_refuses_to_stand_apart_from_its_pictures(tmp_path: Path) -> None:
    """A copy without the directory must fail loudly, not read as an empty month (#881)."""
    package = tmp_path / "tests" / "e2e"
    package.mkdir(parents=True)
    (package.parent / "__init__.py").touch()
    (package / "__init__.py").touch()
    shutil.copy(LIBRARY_DIR.parent.parent / "fake_library.py", package / "fake_library.py")

    proc = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            "import sys; sys.path.insert(0, sys.argv[1]); import tests.e2e.fake_library",
            str(tmp_path),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert proc.returncode != 0
    assert "the library is its files" in proc.stderr
