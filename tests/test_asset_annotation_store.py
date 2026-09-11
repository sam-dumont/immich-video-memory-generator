"""The annotation fact repository reads one complete, producer-exact snapshot."""

from __future__ import annotations

import sqlite3
from dataclasses import FrozenInstanceError
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest


def _create_store(path: Path, *, include_motion: bool = True) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE asset_people (
                asset_id TEXT, person_name TEXT, person_id TEXT, birth_date TEXT
            );
            CREATE TABLE descriptions (asset_id TEXT, model TEXT, text TEXT);
            CREATE TABLE description_fields (
                asset_id TEXT, model TEXT, field TEXT, value TEXT
            );
            CREATE TABLE flags (asset_id TEXT, flag TEXT, evidence TEXT, source TEXT);
            CREATE TABLE head_facts (
                asset_id TEXT, head TEXT, version TEXT, label TEXT
            );
            CREATE TABLE pixel_facts (
                asset_id TEXT, producer_key TEXT, sharpness REAL, brightness REAL,
                contrast REAL, dark_fraction REAL, bright_fraction REAL,
                needs_rotation INTEGER
            );
            CREATE TABLE pixel_facts_thresholds (
                name TEXT, value REAL, producer_key TEXT
            );
            """
        )
        if include_motion:
            connection.execute(
                "CREATE TABLE motion_bursts ("
                "asset_id TEXT, burst_id TEXT, still_ids TEXT, "
                "duration_seconds REAL, beats_a_still INTEGER)"
            )


def test_description_selection_is_exact_and_preserves_requested_asset_order(
    tmp_path: Path,
) -> None:
    from immich_memories.store.asset_annotations import (
        AssetAnnotationFactRepository,
    )

    path = tmp_path / "annotations.sqlite"
    _create_store(path)
    with sqlite3.connect(path) as connection:
        connection.executemany(
            "INSERT INTO descriptions VALUES (?, ?, ?)",
            (
                ("asset-b", "wanted-model", "The selected description."),
                ("asset-b", "stale-model", "The stale description."),
            ),
        )

    batch = AssetAnnotationFactRepository(
        path,
        description_model="wanted-model",
        head_versions={"activity": "activity-v1"},
        pixel_producer_key="pixel-v1",
    ).facts_for(("asset-b", "asset-a"))

    assert batch.unavailable_asset_ids == ()
    assert tuple(batch.as_mapping()) == ("asset-b", "asset-a")
    assert batch.as_mapping()["asset-b"].description == "The selected description."
    assert batch.as_mapping()["asset-a"].description is None


def test_complete_snapshot_uses_only_the_selected_fact_producers(tmp_path: Path) -> None:
    from immich_memories.store.asset_annotations import (
        AssetAnnotationFactRepository,
        StoredFlagFact,
        StoredMotionBurstFact,
        StoredPersonFact,
        StoredPixelFacts,
    )

    path = tmp_path / "annotations.sqlite"
    _create_store(path)
    with sqlite3.connect(path) as connection:
        connection.executemany(
            "INSERT INTO asset_people VALUES (?, ?, ?, ?)",
            (
                ("asset-a", " Zoë  ", "person-z", "2001-02-03"),
                ("asset-a", "alex", "person-a", "not-a-date"),
            ),
        )
        connection.executemany(
            "INSERT INTO descriptions VALUES (?, ?, ?)",
            (
                ("asset-a", "wanted-model", " A  complete description. "),
                ("asset-a", "stale-model", "Stale description."),
            ),
        )
        connection.executemany(
            "INSERT INTO description_fields VALUES (?, ?, ?, ?)",
            (
                ("asset-a", "wanted-model", "setting", " city  street "),
                ("asset-a", "wanted-model", "exposure", "underexposed"),
                ("asset-a", "stale-model", "setting", "stale setting"),
            ),
        )
        connection.executemany(
            "INSERT INTO flags VALUES (?, ?, ?, ?)",
            (
                ("asset-a", "screen", '{"reason":"computer display"}', "docling"),
                ("asset-a", "dark", '{"reason":"low exposure"}', "Exposure"),
                (
                    "asset-a",
                    "review",
                    '{"reason":"legacy public exposure"}',
                    "public-exposure-v2",
                ),
            ),
        )
        connection.executemany(
            "INSERT INTO head_facts VALUES (?, ?, ?, ?)",
            (
                ("asset-a", "activity", "activity-v1", "sport-active"),
                ("asset-a", "activity", "activity-v0", "stale-label"),
                ("asset-a", "venue", "venue-v1", "stadium"),
            ),
        )
        connection.executemany(
            "INSERT INTO pixel_facts VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ("asset-a", "pixel-v1", 5.0, 20.0, 10.0, 0.7, 0.1, 1),
                ("asset-a", "pixel-v0", 99.0, 99.0, 99.0, 0.0, 0.0, 0),
            ),
        )
        connection.executemany(
            "INSERT INTO pixel_facts_thresholds VALUES (?, ?, ?)",
            (
                ("sharpness_p10", 7.5, "pixel-v1"),
                ("sharpness_p10", 70.0, "pixel-v0"),
            ),
        )
        connection.execute(
            "INSERT INTO motion_bursts VALUES (?, ?, ?, ?, ?)",
            ("asset-a", "burst-1", '["still-b","still-a"]', 4.4, 1),
        )

    facts = (
        AssetAnnotationFactRepository(
            path,
            description_model="wanted-model",
            head_versions={"venue": "venue-v1", "activity": "activity-v1"},
            pixel_producer_key="pixel-v1",
        )
        .facts_for(("asset-a",))
        .as_mapping()["asset-a"]
    )

    assert facts.people == (
        StoredPersonFact(person_id="person-a", name="alex", birth_date=None),
        StoredPersonFact(person_id="person-z", name="Zoë", birth_date=date(2001, 2, 3)),
    )
    assert facts.description == "A complete description."
    assert (facts.setting, facts.exposure) == ("city street", "underexposed")
    assert facts.heads == (("activity", "sport-active"), ("venue", "stadium"))
    assert facts.flags == (
        StoredFlagFact(flag="screen", reason="computer display", source="docling"),
    )
    assert facts.pixel == StoredPixelFacts(
        sharpness=5.0,
        brightness=20.0,
        contrast=10.0,
        dark_fraction=0.7,
        bright_fraction=0.1,
        needs_rotation=True,
        soft_below=7.5,
    )
    assert facts.motion == StoredMotionBurstFact(
        burst_id="burst-1",
        still_ids=("still-b", "still-a"),
        duration_seconds=4.4,
        beats_a_still=True,
    )


def test_store_without_legacy_motion_table_remains_a_complete_read(tmp_path: Path) -> None:
    from immich_memories.store.asset_annotations import (
        AssetAnnotationFactRepository,
    )

    path = tmp_path / "annotations.sqlite"
    _create_store(path, include_motion=False)

    batch = AssetAnnotationFactRepository(
        path,
        description_model="wanted-model",
        head_versions={},
        pixel_producer_key="pixel-v1",
    ).facts_for(("asset-a",))

    assert batch.unavailable_asset_ids == ()
    assert batch.warnings == ()
    assert batch.as_mapping()["asset-a"].motion is None


def test_malformed_legacy_motion_table_does_not_look_like_complete_evidence(
    tmp_path: Path,
) -> None:
    from immich_memories.store.asset_annotations import (
        AssetAnnotationFactRepository,
    )

    path = tmp_path / "annotations.sqlite"
    _create_store(path, include_motion=False)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE motion_bursts (asset_id TEXT)")

    batch = AssetAnnotationFactRepository(
        path,
        description_model="wanted-model",
        head_versions={},
        pixel_producer_key="pixel-v1",
    ).facts_for(("asset-a",))

    assert batch.facts == ()
    assert batch.unavailable_asset_ids == ("asset-a",)


def test_unreadable_store_fails_open_without_disclosing_requested_ids(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from immich_memories.store.asset_annotations import (
        AssetAnnotationFactRepository,
    )

    private_id = "private-asset-that-must-not-be-logged"
    batch = AssetAnnotationFactRepository(
        tmp_path / "missing.sqlite",
        description_model="wanted-model",
        head_versions={},
        pixel_producer_key="pixel-v1",
    ).facts_for((private_id,))

    assert batch.facts == ()
    assert batch.unavailable_asset_ids == (private_id,)
    assert batch.warnings == ("!! annotation fact store unavailable",)
    assert private_id not in caplog.text
    assert private_id not in " ".join(batch.warnings)


def test_large_read_uses_one_query_per_fact_family_below_sqlite_variable_limit(
    tmp_path: Path,
) -> None:
    from immich_memories.store.asset_annotations import (
        AssetAnnotationFactRepository,
    )

    path = tmp_path / "annotations.sqlite"
    _create_store(path)
    private_ids = tuple(f"private-asset-{index:03d}" for index in range(25))
    statements: list[str] = []
    real_connect = sqlite3.connect

    def limited_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        connection = real_connect(*args, **kwargs)
        connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 1)
        connection.set_trace_callback(statements.append)
        return connection

    # WHY: SQLite is the boundary; its real lowered limit proves IDs never become bind slots.
    with patch(
        "immich_memories.store.asset_annotations.sqlite3.connect",
        side_effect=limited_connect,
    ):
        batch = AssetAnnotationFactRepository(
            path,
            description_model="wanted-model",
            head_versions={"activity": "activity-v1"},
            pixel_producer_key="pixel-v1",
        ).facts_for(private_ids)

    selects = [
        statement for statement in statements if statement.lstrip().upper().startswith("SELECT")
    ]
    assert batch.unavailable_asset_ids == ()
    assert len(batch.facts) == 25
    assert len(selects) == 8


def test_returned_fact_records_are_immutable(tmp_path: Path) -> None:
    from immich_memories.store.asset_annotations import (
        AssetAnnotationFactRepository,
    )

    path = tmp_path / "annotations.sqlite"
    _create_store(path)
    facts = (
        AssetAnnotationFactRepository(
            path,
            description_model="wanted-model",
            head_versions={},
            pixel_producer_key="pixel-v1",
        )
        .facts_for(("asset-a",))
        .facts[0]
    )

    with pytest.raises(FrozenInstanceError):
        facts.description = "mutated"  # type: ignore[misc]
