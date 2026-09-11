"""The seams the triage engine is composed across."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Protocol

import numpy as np

from immich_memories.triage.heads import HeadFact

# One preview per asset id, or None when the asset has no usable image.
AssetImageSource = Callable[[str], bytes | None]


class PackEncoder(Protocol):
    @property
    def key(self) -> str: ...

    def embed(self, batch: np.ndarray) -> np.ndarray: ...


class FactStore(Protocol):
    def remember_facts(
        self, asset_id: str, facts: Sequence[HeadFact], *, encoder_key: str
    ) -> None: ...

    def facts_for(
        self, asset_ids: Sequence[str], *, head: str, version: str
    ) -> dict[str, HeadFact]: ...
