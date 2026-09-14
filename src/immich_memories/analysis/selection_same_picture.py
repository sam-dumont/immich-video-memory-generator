"""The same-picture question, asked the only way this model answers it.

One pair, two numbered tiles, judged in both arrangements -- only the two
orders agreeing counts as one picture. Shared by Pass 2 (chronological
neighbours) and the final-duplicates wall (nominated pairs): every caller
stamps the same request identity, so a verdict bought by one is a cache hit
for the other, and the verdict belongs to the two pictures rather than to
whichever scope happened to present them.

Perceptual distance is the second vote wherever the pixels already say what
the second arrangement would; each run calibrates that distance on its own
library and may only lower the measured cap.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING

from immich_memories.analysis.contact_sheets import build_contact_sheets
from immich_memories.analysis.editorial_contracts import EditorialCandidate
from immich_memories.analysis.provider_failure import ProviderCredentialRejected
from immich_memories.analysis.strict_json import final_json_object
from immich_memories.analysis.visual_request_planner import VisionRequestLimits

if TYPE_CHECKING:
    from immich_memories.analysis.editorial_gateway import EditorialGateway


SELECTS_PASS_NAME = "pass-2-selects"  # noqa: S105 - public editorial pass identity
SELECTS_PASS_VERSION = "pass-2-selects-v1"  # noqa: S105 - editorial pass identity
PAIR_PROMPT_VERSION = "pair-prompt-v3"  # noqa: S105 - wire contract identity
PAIR_SCHEMA_VERSION = "pair-v3"  # noqa: S105 - wire contract identity

# The answer moved between 150px and 400px in 4 of 4 moments and stopped moving
# above it, so this pass states the fidelity its own question needs rather than
# inheriting a 120-tile page's compromise.
SELECTS_TILE_PX = 400

# Perceptual distance is the SECOND VOTE, never the only one. Thein keeps what
# two independent passes both chose; one of those passes is free wherever the
# pixels already say what the second arrangement would.
#
# Measured on 653 real pairs: the model contradicted itself on 39, and only 4 of
# those were pixel-close -- its uncertainty lives on pixel-DISTANT pairs. At a
# corroboration distance of 10 the adaptive rule reproduced every one of 653
# decisions exactly while removing 30% of the calls. The first changed decision
# appears at 12.
#
# This is a CAP, not a tuned threshold. Each run derives its own from a
# calibration sample of its own library and may only lower it, so a library
# unlike the one this was measured on saves less rather than cutting more.
SELECTS_MAX_CORROBORATION = 10

_PAIR_SHAPE = json.dumps(
    {"schema_version": PAIR_SCHEMA_VERSION, "same": False, "reason": "what makes them one or two"},
    separators=(",", ":"),
)
# The question is the probe's, word for word. `same: false` is shown because
# false is the safe default -- a copied example merges nothing. More prose
# measurably makes this model worse, so none is added.
#
# The written reason is asked for even though the parser reads only `same`, and
# that is a QUALITY decision that reverses an earlier cost one. Dropping it
# halved the call, and a 30-pair sample said the verdict agreed 29 times in 30.
# Across the 650 pairs judged under both contracts it agrees 79% of the time,
# and the disagreement is one-directional: 126 pairs went from "same" to
# "different" and 10 the other way. Looked at, those pairs are the same picture
# -- one is a woman holding a newborn in the same chair in the same pose, twice.
# Writing the reason is not overhead on the answer; on this model it is part of
# how the answer is arrived at, and 30 pairs was too small a sample to see it.


_PAIR_PROMPT = (
    "Two numbered visuals. Are they two attempts at the same picture -- the same "
    "subject, framed the same way, moments apart -- or are they two different pictures? "
    "Return only one complete JSON object, using exactly these keys and no others:\n" + _PAIR_SHAPE
)


@dataclass(frozen=True)
class _PendingPair:
    """One chronological neighbour comparison whose calls can be scheduled."""

    scope_id: str
    earlier: EditorialCandidate
    later: EditorialCandidate
    distance: int | None


@dataclass(frozen=True)
class _PairBatchReader:
    """Run independent pair arrangements concurrently, preserving result order."""

    tasks: tuple[_PendingPair, ...]
    atlas: object
    requester: EditorialGateway
    sheet_output_dir: Path
    limits: VisionRequestLimits
    concurrency: int

    def ask(self, task: _PendingPair, arrangement: str) -> bool | None:
        pair = (task.earlier, task.later) if arrangement == "ab" else (task.later, task.earlier)
        return _ask_one_pair(
            task.scope_id,
            arrangement,
            pair,
            self.atlas,
            self.requester,
            self.sheet_output_dir,
            self.limits,
        )

    def ask_many(self, indices: Sequence[int], arrangement: str) -> tuple[bool | None, ...]:
        jobs = tuple((copy_context(), self.tasks[index]) for index in indices)
        with ThreadPoolExecutor(max_workers=self.concurrency) as executor:
            return tuple(
                executor.map(
                    lambda job: job[0].run(self.ask, job[1], arrangement),
                    jobs,
                )
            )


@dataclass(frozen=True)
class SamePicturePairDecision:
    """A fail-open two-order verdict for an explicitly nominated picture pair."""

    earlier_asset_id: str
    later_asset_id: str
    same: bool
    warning: str | None = None


def confirm_same_picture_pairs(
    pairs: Sequence[tuple[EditorialCandidate, EditorialCandidate]],
    *,
    atlas: object,
    requester: EditorialGateway,
    sheet_output_dir: Path,
    corroborating_distances: Sequence[int | None] | None = None,
    limits: VisionRequestLimits | None = None,
    concurrency: int = 1,
) -> tuple[SamePicturePairDecision, ...]:
    """Confirm arbitrary candidate pairs with the measured two-order contract.

    Pair nomination belongs to the caller. A supplied distance is an already
    qualified pixel second vote from final-wall nomination; arbitrary pairs
    omit it and retain the conservative two-order contract. Disagreement or an
    unreadable answer never permits removing a picture.
    """
    nominated = tuple(pairs)
    distances = _aligned_distances(nominated, corroborating_distances)
    if not nominated:
        return ()

    tasks = tuple(
        _PendingPair(
            scope_id=f"final-duplicate-{index:04d}",
            earlier=earlier,
            later=later,
            distance=distance,
        )
        for index, ((earlier, later), distance) in enumerate(
            zip(nominated, distances, strict=True), start=1
        )
    )
    reader = _PairBatchReader(
        tasks=tasks,
        atlas=atlas,
        requester=requester,
        sheet_output_dir=sheet_output_dir,
        limits=limits or VisionRequestLimits(),
        concurrency=max(1, concurrency),
    )
    indices = tuple(range(len(tasks)))
    forwards = reader.ask_many(indices, "ab")
    backward_indices = tuple(
        index
        for index, (task, answer) in enumerate(zip(tasks, forwards, strict=True))
        if answer is True and (task.distance is None or task.distance > SELECTS_MAX_CORROBORATION)
    )
    backwards = dict(zip(backward_indices, reader.ask_many(backward_indices, "ba"), strict=True))

    decisions: list[SamePicturePairDecision] = []
    for index, (task, forward) in enumerate(zip(tasks, forwards, strict=True)):
        same, warning = _two_order_verdict(task, forward, backwards.get(index))
        decisions.append(
            SamePicturePairDecision(
                earlier_asset_id=task.earlier.asset_id,
                later_asset_id=task.later.asset_id,
                same=same,
                warning=warning,
            )
        )
    return tuple(decisions)


def _aligned_distances(
    nominated: Sequence[tuple[EditorialCandidate, EditorialCandidate]],
    corroborating_distances: Sequence[int | None] | None,
) -> tuple[int | None, ...]:
    distances = (
        tuple(corroborating_distances)
        if corroborating_distances is not None
        else (None,) * len(nominated)
    )
    if len(distances) != len(nominated):
        raise ValueError("same-picture corroborating distances must align with pairs")
    pair_keys = tuple(
        tuple(sorted((earlier.asset_id, later.asset_id))) for earlier, later in nominated
    )
    if any(earlier.asset_id == later.asset_id for earlier, later in nominated):
        raise ValueError("same-picture confirmation needs two different assets")
    if len(set(pair_keys)) != len(pair_keys):
        raise ValueError("same-picture confirmation pairs must be unique")
    return distances


def _two_order_verdict(
    task: _PendingPair, forward: bool | None, backward: bool | None
) -> tuple[bool, str | None]:
    """A qualified pixel second vote replaces the reverse arrangement; a bad read keeps both."""
    unreadable = f"!! Unreadable final duplicate pair; both kept: {task.scope_id}"
    if forward is None:
        return False, unreadable
    if not forward:
        return False, None
    if task.distance is not None and task.distance <= SELECTS_MAX_CORROBORATION:
        return True, None
    if backward is None:
        return False, unreadable
    return backward, None


def _ask_one_pair(
    scope_id: str,
    arrangement: str,
    pair: tuple[EditorialCandidate, EditorialCandidate],
    atlas: object,
    requester: EditorialGateway,
    sheet_output_dir: Path,
    limits: VisionRequestLimits,
) -> bool | None:
    """One arrangement of one pair. `None` means no usable answer, never "different"."""
    from immich_memories.analysis.editorial_gateway import VisualEditorialRequest

    try:
        tiles = tuple(
            atlas.tile_for(candidate.asset_id)  # type: ignore[attr-defined]
            for candidate in pair
        )
        if any(getattr(tile, "kind", "") == "unavailable" for tile in tiles):
            return None
        evidence = [
            (candidate.asset_id, tile.sha256) for candidate, tile in zip(pair, tiles, strict=True)
        ]
        evidence_key = sha256(
            json.dumps(sorted(evidence), separators=(",", ":")).encode()
        ).hexdigest()
        arrangement_key = sha256(json.dumps(evidence, separators=(",", ":")).encode()).hexdigest()
        page = build_contact_sheets(
            tiles,
            scope_id=f"{scope_id}-{arrangement}",
            output_dir=sheet_output_dir / evidence_key / arrangement_key,
            tile_px=SELECTS_TILE_PX,
        )[0]
        answer = requester.ask(
            VisualEditorialRequest(
                pass_name=SELECTS_PASS_NAME,
                pass_version=SELECTS_PASS_VERSION,
                prompt=_PAIR_PROMPT,
                prompt_version=PAIR_PROMPT_VERSION,
                schema_version=PAIR_SCHEMA_VERSION,
                pages=(page,),
                ordered_input_ids=tuple(candidate.asset_id for candidate in pair),
                # Pair sameness belongs to these two pictures forever. The
                # moment id is only where this memory happened to present them;
                # including it strands the verdict when another scope adds or
                # removes a neighbouring frame.
                ordered_group_ids=(),
                grounded_annotations=(),
                upstream_material=(),
                render_version=f"selects/pair/{arrangement}",
                limits=limits,
                image_detail="high",
            )
        )
    except ProviderCredentialRejected:
        raise  # a rejected key would leave every pair unjudged without a word
    except Exception:  # noqa: BLE001 - one failed pair keeps both frames, it never cuts
        return None
    payload = final_json_object(answer.raw_text) or {}
    # Cache identity already stops a stale BANK being replayed against new
    # pixels. It cannot stop a live model answering the previous contract, and
    # answering the wrong question fluently is this model's documented failure.
    if payload.get("schema_version") != PAIR_SCHEMA_VERSION:
        return None
    same = payload.get("same")
    return same if isinstance(same, bool) else None
