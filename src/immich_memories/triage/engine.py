"""Compose encoder, head bundle and fact store into one pass over a set of assets."""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from immich_memories.triage.contracts import AssetImageSource, FactStore, PackEncoder
from immich_memories.triage.heads import HeadBundle, HeadFact
from immich_memories.triage.preprocess import preprocess_image_bytes

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TriageRun:
    requested: int
    decided: int
    banked: int
    missing: int
    elapsed_seconds: float

    @property
    def ms_per_decided(self) -> float:
        return 1000.0 * self.elapsed_seconds / self.decided if self.decided else 0.0


class TriageEngine:
    def __init__(self, *, encoder: PackEncoder, bundle: HeadBundle, store: FactStore) -> None:
        if bundle.encoder_key != encoder.key:
            raise ValueError(
                "head bundle was trained on another encoder "
                f"({bundle.encoder_key[:12]} vs {encoder.key[:12]})"
            )
        self._encoder = encoder
        self._bundle = bundle
        self._store = store

    def _unbanked(self, asset_ids: Sequence[str]) -> list[str]:
        """Assets missing a fact for at least one head version in the bundle."""
        complete = set(asset_ids)
        for head in self._bundle.heads:
            banked = self._store.facts_for(asset_ids, head=head.name, version=head.version)
            complete &= set(banked)
        return [asset_id for asset_id in asset_ids if asset_id not in complete]

    def run(
        self, asset_ids: Sequence[str], images: AssetImageSource, *, batch_size: int = 32
    ) -> TriageRun:
        """Decide every head for each asset not already banked; skip assets with no preview."""
        started = time.monotonic()
        pending = self._unbanked(asset_ids)
        decided = missing = 0
        for start in range(0, len(pending), batch_size):
            chunk: list[tuple[str, np.ndarray]] = []
            for asset_id in pending[start : start + batch_size]:
                payload = images(asset_id)
                if payload is None:
                    missing += 1
                    continue
                chunk.append((asset_id, preprocess_image_bytes(payload)))
            if chunk:
                decided += self._decide_batch(chunk)
        run = TriageRun(
            requested=len(asset_ids),
            decided=decided,
            banked=len(asset_ids) - len(pending),
            missing=missing,
            elapsed_seconds=time.monotonic() - started,
        )
        logger.info(
            "triage: %d decided, %d banked, %d without preview, %.1f ms/image",
            run.decided,
            run.banked,
            run.missing,
            run.ms_per_decided,
        )
        return run

    def _decide_batch(self, chunk: list[tuple[str, np.ndarray]]) -> int:
        packs = self._encoder.embed(np.stack([pixels for _asset_id, pixels in chunk]))
        facts_by_head = self._bundle.decide(packs)
        for row, (asset_id, _pixels) in enumerate(chunk):
            facts: list[HeadFact] = [facts_by_head[head.name][row] for head in self._bundle.heads]
            self._store.remember_facts(asset_id, facts, encoder_key=self._encoder.key)
        return len(chunk)
