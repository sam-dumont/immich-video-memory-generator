"""Give a cell another cell's preparation, and none of its judgement.

Preparation depends on the host, the preparation tier and where the picture facts
come from. It does not depend on who reads afterwards, so the Mac cells that vary
only the reader would each pay for the same captions, heads and pixel facts and
publish that one number under every one of their names. `mac-local` measures it
once and the rest are seeded from its bank.

What must NOT come across is anything a reader decided. Per-cell caches exist
because the first real Mac run shared one and `mac-rules` published `mac-local`'s
verdicts as its own losses; seeding a reader's answers into a cell that exists to
compare readers would be that failure with extra steps. So the copy is followed
by a delete: every table in the annotation store that holds a model's answer is
emptied, and the separate judgment database beside it is removed outright.

    uv run python scripts/setup_matrix_seed.py <source cache> <destination cache>
"""

from __future__ import annotations

import shutil
import sqlite3
import sys
from pathlib import Path

# Where a bank keeps what a model said. `judgments` and its failure tables are
# the text gateway's; the episode and period tables are whole readings; the
# verdicts table is the cull's remembered buckets. Anything not listed here is a
# fact about a picture, which is the whole point of seeding.
VERDICT_TABLES = (
    "judgments",
    "text_completion_failures",
    "visual_judgments",
    "visual_completion_failures",
    "editorial_episode_readings",
    "editorial_period_insights",
    "editorial_verdicts",
)

# The judgment cache can also live in a file of its own beside the bank
# (`verdicts_beside`), and a file is easier to delete than to empty.
VERDICT_FILES = ("judgments.db", "judgments.db-wal", "judgments.db-shm")

ANNOTATION_STORE = "annotations.sqlite"


def strip_judgements(store: Path) -> list[str]:
    """Empty every verdict table an annotation store happens to carry, and name them.

    A table that was never created is not an error: which of them exist depends
    on what the source cell actually ran.
    """
    if not store.is_file():
        return []
    emptied = []
    with sqlite3.connect(store) as conn:
        present = {
            str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        for table in VERDICT_TABLES:
            if table in present:
                conn.execute(f"DELETE FROM {table}")  # noqa: S608 - a name from VERDICT_TABLES
                emptied.append(table)
        conn.commit()
    return emptied


def seed_cache(source: Path, destination: Path) -> list[str]:
    """Copy `source` over `destination` and take every model answer back out of it.

    The destination is replaced rather than merged: a cell re-run has to start
    from the same preparation and no verdicts, or its second run would replay its
    first run's reading and report a selection that was never asked for.
    """
    if not source.is_dir():
        raise SystemExit(
            f"{source} does not exist, so this cell has no bank to seed from. The seed is the"
            " reference cell's cache for THIS library, under that library's own output"
            " directory: run that cell over this library once, or point --out at the run that"
            " already has it."
        )
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination)
    for name in VERDICT_FILES:
        (destination / name).unlink(missing_ok=True)
    return strip_judgements(destination / ANNOTATION_STORE)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    emptied = seed_cache(Path(argv[0]).expanduser(), Path(argv[1]).expanduser())
    print(f"seeded {argv[1]} from {argv[0]}; emptied {', '.join(emptied) or 'nothing'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
