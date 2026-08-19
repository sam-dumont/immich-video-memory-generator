"""The vulture whitelist is a ratchet, not a parking lot.

`vulture --make-whitelist` regenerates the file wholesale, so a change that
leaves something dead behind can be absolved by rerunning the command: the new
entry joins the list and the gate goes green. This is the same shape as the
complexity snapshot, and it fails the same way — quietly.

The count below is the agreed size of the backlog. It may fall. It may not rise
without someone saying so in a commit.
"""

from __future__ import annotations

from pathlib import Path

WHITELIST = Path(__file__).resolve().parent.parent / "vulture-whitelist.py"

# Lower this as entries are cleared. Raise it only for a false positive vulture
# cannot see through -- MemoryType.ALBUM is reached as the string "album" -- and
# never to silence something genuinely dead.
MAX_WHITELISTED_SYMBOLS = 323


def test_the_dead_code_whitelist_never_grows() -> None:
    entries = [
        line
        for line in WHITELIST.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    assert len(entries) <= MAX_WHITELISTED_SYMBOLS, (
        f"{len(entries)} symbols are whitelisted as dead, above the agreed "
        f"{MAX_WHITELISTED_SYMBOLS}. Regenerating the whitelist hides new dead "
        f"code rather than removing it — delete the symbol, or raise "
        f"MAX_WHITELISTED_SYMBOLS deliberately."
    )
