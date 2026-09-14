"""Seeding hands on preparation and nothing a reader decided.

Per-cell caches exist because the first real Mac run shared one and `mac-rules`
published `mac-local`'s verdicts as its own losses. Seeding a bank into cells that
exist to compare readers has to keep that rule: the captions, heads and pixel
facts cross, the judgements do not.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from setup_matrix_seed import ANNOTATION_STORE, seed_cache  # noqa: E402

from immich_memories.cache.judgment_cache import JudgmentCache  # noqa: E402
from immich_memories.store.editorial_preparation import initialize, now  # noqa: E402


def _prepared_bank(directory: Path) -> Path:
    """A bank holding one picture's caption and one banked model answer.

    The schema is the product's own, and the judgement goes in through the cache
    the reader uses, so moving either one breaks this rather than leaving the seed
    quietly copying a table that is no longer there.
    """
    directory.mkdir(parents=True, exist_ok=True)
    store = directory / ANNOTATION_STORE
    with sqlite3.connect(store) as conn:
        initialize(conn)
        conn.execute(
            "INSERT INTO descriptions (asset_id,model,text,source,written_at) VALUES (?,?,?,?,?)",
            ("asset-1", "smolvlm2-500m", "a child on a beach", "caption", now()),
        )
        conn.commit()
    banked = JudgmentCache(store)
    banked.remember("a-judgment-key", '{"keep": ["M01"]}')
    banked.close()
    return store


def _rows(store: Path, table: str) -> int:
    with sqlite3.connect(store) as conn:
        return int(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0])  # noqa: S608


def test_seeding_keeps_the_preparation_and_drops_the_judgements(tmp_path: Path) -> None:
    source = _prepared_bank(tmp_path / "mac-local" / "cache")
    destination = tmp_path / "mac-hosted-melious-gemma-4-31b" / "cache"

    emptied = seed_cache(source.parent, destination)

    seeded = destination / ANNOTATION_STORE
    assert _rows(seeded, "descriptions") == 1, "the captions are what seeding is for"
    assert _rows(seeded, "judgments") == 0, "a reader must never inherit another reader's answer"
    assert "judgments" in emptied


def test_a_second_seed_replaces_what_the_last_run_banked(tmp_path: Path) -> None:
    """A re-run keeping its own verdicts would replay a reading nobody asked for."""
    source = _prepared_bank(tmp_path / "mac-local" / "cache")
    destination = tmp_path / "mac-local-gemma4" / "cache"
    seed_cache(source.parent, destination)
    own = JudgmentCache(destination / ANNOTATION_STORE)
    own.remember("answered-during-the-first-run", "{}")
    own.close()

    seed_cache(source.parent, destination)

    assert _rows(destination / ANNOTATION_STORE, "judgments") == 0


def test_a_judgment_database_beside_the_bank_does_not_cross(tmp_path: Path) -> None:
    """`verdicts_beside` puts them in a file of their own, which a copy carries whole."""
    source = tmp_path / "mac-local" / "cache"
    _prepared_bank(source)
    JudgmentCache(source / "judgments.db").close()
    destination = tmp_path / "mac-hosted-melious-muse-glimmer-30b" / "cache"

    seed_cache(source, destination)

    assert not (destination / "judgments.db").exists()


def test_a_bank_that_is_not_there_is_named_rather_than_half_copied(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as refused:
        seed_cache(tmp_path / "never-prepared" / "cache", tmp_path / "target")
    assert "never-prepared" in str(refused.value)
