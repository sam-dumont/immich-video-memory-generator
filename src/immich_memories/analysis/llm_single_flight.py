"""One paid answer per judgment key, however many readers ask for it at once.

Serial reading deduplicated identical prompts through the judgment bank without
anyone arranging it: the first asker banked its answer and the next one read it.
Overlapping readers lose that. Both miss the bank, both pay, both write, and each
consumer keeps the reply it happened to get -- so the same run replayed, which
collapses them onto the one banked answer, can give a different cut. Here the
second asker waits for the first and then finds the answer where it always was.

The wait is a plain lock rather than an async one because each reader runs its own
event loop on its own thread; there is no loop for the waiters to share.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager


class SingleFlight:
    """Serialise callers sharing a key. Callers with different keys never meet."""

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._held: dict[str, list] = {}

    @contextmanager
    def key(self, name: str) -> Iterator[None]:
        with self._guard:
            entry = self._held.setdefault(name, [threading.Lock(), 0])
            entry[1] += 1
            lock = entry[0]
        with lock:
            try:
                yield
            finally:
                with self._guard:
                    entry = self._held[name]
                    entry[1] -= 1
                    if not entry[1]:
                        del self._held[name]


# Every reader in this process shares one registry: the judgment key already names
# the model and the exact prompt, so two holders of the same key are the same call.
TEXT_JUDGMENTS = SingleFlight()
