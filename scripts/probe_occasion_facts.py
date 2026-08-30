#!/usr/bin/env python3
"""Per-occasion facts for the chapter allocation and the chapter cut.

An occasion is one run of photographed days a short rest gap cannot split. The run
grouper is imported from ``probe_selection_final_cut`` rather than restated, so the
allocation layer and the final cut always agree on where one occasion ends.

Every field is a raw count the run already holds. Nothing here names, ranks, or
interprets what an occasion was: the editorial model reads the numbers and decides how
much room each one earns.

Scene diversity reads the banked DINOv2 ViT-S/14 embeddings ``probe_pairhead_embed.py``
wrote (``embeddings.npy`` + ``ids.json``). The bank is optional in every direction:
assets it never saw are skipped and the coverage is stated beside the count, and with no
readable bank at all the field is dropped rather than guessed. numpy reads the saved
arrays; no model is loaded here.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import probe_description_moment_cut as prototype
from probe_selection_final_cut import FineCutCandidate, _occasion_day_runs

DEFAULT_MATRIX_DIR = Path.home() / ".immich-memories-matrix" / "pairhead-2026-08-30"

# Merge radius for the greedy leader clustering below, in cosine distance on the banked
# 384-d embeddings. Measured on the 6384 owner-answered pairs in the same bank: at 0.35,
# 97.3% of same-scene pairs fall inside the radius and 70.5% of different-scene pairs
# fall outside it (0.25 -> 92.3%/82.2%, 0.45 -> 98.9%/57.3%).
SCENE_CLUSTER_DISTANCE = 0.35

# One chapter's cards are one chapter by construction; the grouper only needs a key that
# never changes inside the call.
_ONE_CHAPTER = "chapter"

_BANKS: dict[Path, SceneBank | None] = {}


# eq=False: the fields are a numpy array and a dict, so generated equality and hashing
# would raise rather than answer.
@dataclass(frozen=True, eq=False)
class SceneBank:
    """L2-normalized banked embeddings addressed by asset ID."""

    vectors: np.ndarray
    row_by_asset: dict[str, int]

    def scene_diversity(self, asset_ids: Sequence[str]) -> str:
        """Report distinct visual clusters among the banked assets, with their coverage."""
        rows = [
            self.row_by_asset[asset_id]
            for asset_id in dict.fromkeys(asset_ids)
            if asset_id in self.row_by_asset
        ]
        coverage = round(100 * len(rows) / len(asset_ids)) if asset_ids else 0
        return f"{_leader_clusters(self.vectors[rows])} clusters/{coverage}% embedded"


def _leader_clusters(vectors: np.ndarray) -> int:
    """Greedy leader clustering: a vector outside every leader's radius becomes a leader."""
    leaders: list[np.ndarray] = []
    for vector in vectors:
        if all(1.0 - float(leader @ vector) > SCENE_CLUSTER_DISTANCE for leader in leaders):
            leaders.append(vector)
    return len(leaders)


def _load_scene_bank(directory: Path) -> SceneBank | None:
    try:
        raw = np.load(directory / "embeddings.npy")
        asset_ids = json.loads((directory / "ids.json").read_text())
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if raw.ndim != 2 or not isinstance(asset_ids, list) or len(asset_ids) != len(raw):
        return None
    norms = np.linalg.norm(raw, axis=1, keepdims=True)
    vectors = raw / np.where(norms == 0.0, 1.0, norms)
    return SceneBank(vectors, {str(asset_id): row for row, asset_id in enumerate(asset_ids)})


def scene_bank() -> SceneBank | None:
    """Read the banked embeddings once per directory; None whenever the bank is unusable."""
    directory = Path(os.environ.get("PAIRHEAD_MATRIX_DIR", DEFAULT_MATRIX_DIR)).expanduser()
    if directory not in _BANKS:
        _BANKS[directory] = _load_scene_bank(directory)
    return _BANKS[directory]


def _person_names(candidate: Any) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            name
            for person in (candidate.source.people or ())
            if (name := str(person.name or "").strip())
        )
    )


def _asset_rows(cards: Iterable[prototype.MomentCard]) -> tuple[FineCutCandidate, ...]:
    """Expand a chapter's moment cards into the per-asset rows the day grouper reads."""
    return tuple(
        FineCutCandidate(
            alias=candidate.asset_id,
            asset_id=candidate.asset_id,
            moment_id=card.moment.alias,
            taken_at=candidate.taken_at,
            media_kind=candidate.media_kind,
            favourite=candidate.favourite,
            description=card.summary,
            context=tuple(candidate.grounded_annotations),
            people_context=_person_names(candidate),
        )
        for card in cards
        for candidate in card.moment.group.candidates
    )


def _occasion_facts(
    occasion: tuple[FineCutCandidate, ...],
    *,
    bank: SceneBank | None,
) -> dict[str, Any]:
    days = sorted({row.taken_at.date() for row in occasion})
    facts: dict[str, Any] = {
        "first_day": days[0].isoformat(),
        "last_day": days[-1].isoformat(),
        "span_days": (days[-1] - days[0]).days + 1,
        "photographed_days": len(days),
        "moments": len({row.moment_id for row in occasion}),
        "assets": len(occasion),
        "favourites": sum(row.favourite for row in occasion),
        "people_breadth": len({name for row in occasion for name in row.people_context}),
    }
    if bank is not None:
        facts["scene_diversity"] = bank.scene_diversity([row.asset_id for row in occasion])
    return facts


def chapter_occasions(cards: Sequence[prototype.MomentCard]) -> list[dict[str, Any]]:
    """Return one raw fact row per occasion inside a single chapter's moment cards."""
    rows = _asset_rows(cards)
    if not rows:
        return []
    bank = scene_bank()
    return [
        _occasion_facts(occasion, bank=bank)
        for occasion in _occasion_day_runs(
            rows, chapter_by_moment=dict.fromkeys({row.moment_id for row in rows}, _ONE_CHAPTER)
        )
    ]


def occasion_facts_block(rows: Sequence[Any]) -> str:
    """Render the labelled block both editorial prompts carry, or nothing when empty."""
    if not rows:
        return ""
    return "OCCASION FACTS\n" + json.dumps(rows, ensure_ascii=False, separators=(",", ":")) + "\n"
