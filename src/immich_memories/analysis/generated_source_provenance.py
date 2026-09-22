"""The films this app made and put back in the library, so it never films them again.

Two independent records, because neither is complete on its own: the provenance tag
Immich holds (which a library restored from a backup keeps, but which an older upload
never got), and this install's own upload receipts (which cover every upload it made,
but not one made from another machine).
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable, Iterable
from pathlib import Path

logger = logging.getLogger(__name__)


def recorded_generated_ids(cache_database: Path) -> frozenset[str]:
    """The assets this install recorded when it uploaded a finished film.

    Read-only and migration-free: a pool builder must never initialise or upgrade the
    run database, and a library that has never delivered a film has no table to read.
    """
    if not Path(cache_database).exists():
        return frozenset()
    uri = Path(cache_database).resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as db:
        if not db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pipeline_runs'"
        ).fetchone():
            return frozenset()
        return frozenset(
            row[0]
            for row in db.execute(
                "SELECT immich_asset_id FROM pipeline_runs "
                "WHERE immich_asset_id IS NOT NULL AND trim(immich_asset_id) != ''"
            )
        )


def generated_source_ids(
    *, tagged: Callable[[], Iterable[str]], cache_database: Path
) -> frozenset[str]:
    """Every id either record calls one of ours.

    A server that refuses the tag query (an API key without tag scope, a version that
    predates tags) leaves the receipts answering rather than failing the run.
    """
    try:
        by_tag = frozenset(tagged())
    except (OSError, ValueError, KeyError) as error:
        logger.warning("Could not read the generated-film tag back from Immich: %s", error)
        by_tag = frozenset()
    return by_tag | recorded_generated_ids(cache_database)
