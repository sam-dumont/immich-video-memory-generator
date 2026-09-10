"""Stop marginal reserve search using the duration of already retained pictures."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

# The owner accepts a 15–20% shortfall to avoid expensive marginal additions.
ACCEPTED_SHORTFALL_FRACTION = 0.15


class RetainedMotion:
    """Resolve each retained unit once per run, including unavailable-motion fallback.

    Later completion pages only add pictures. Reusing motion fields must not restore
    stale editorial metadata or count repeated cache reads as new measured work.
    """

    def __init__(self, resolve: Callable | None) -> None:
        self.resolve = resolve
        self.updates: dict[tuple, dict[str, Any]] = {}
        self.metrics: dict[str, Any] = {
            "scope": "captured motion evidence",
            "new_motion_downloads": 0,
        }

    @staticmethod
    def _key(carrier: dict) -> tuple:
        return (
            carrier["asset_id"],
            tuple(carrier.get("members", ())),
            carrier.get("raw_seconds"),
            bool(carrier.get("motion_candidate")),
        )

    def _record_updates(self, original: list[dict], resolved: list[dict]) -> None:
        for before, after in zip(original, resolved, strict=True):
            self.updates[self._key(before)] = {
                key: value
                for key, value in after.items()
                if key not in before or before[key] != value
            }

    def _merge_metrics(self, metrics: dict[str, Any]) -> None:
        for key, value in metrics.items():
            if isinstance(value, (int, float)) and key != "sample_limit_per_carrier":
                self.metrics[key] = self.metrics.get(key, 0) + value
            else:
                if key in self.metrics and self.metrics[key] != value:
                    raise ValueError(f"motion measurement changed its {key}")
                self.metrics[key] = value

    def __call__(self, carriers: list[dict]) -> list[dict]:
        if self.resolve is None:
            return carriers.copy()
        pending = [c for c in carriers if self._key(c) not in self.updates]
        if pending:
            original = [c.copy() for c in pending]
            resolved, metrics = self.resolve([c.copy() for c in pending])
            if [self._key(c) for c in resolved] != [self._key(c) for c in original]:
                raise ValueError(
                    "motion resolution must preserve retained unit membership and order"
                )
            self._record_updates(original, resolved)
            self._merge_metrics(metrics)
        return [c | self.updates[self._key(c)] for c in carriers]
