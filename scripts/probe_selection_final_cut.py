"""Prototype text contract for the asset cut inside selected moment reservoirs."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from immich_memories.analysis.strict_json import bounded_model_text, final_json_object

FINAL_ASSET_CUT_SCHEMA = "description-final-asset-cut-v1"
_MAX_REASON_CHARS = 500


@dataclass(frozen=True)
class FineCutCandidate:
    """One described asset available to the final duration cut."""

    alias: str
    asset_id: str
    moment_id: str
    taken_at: datetime
    media_kind: str
    favourite: bool
    description: str
    context: tuple[str, ...] = ()

    def wall_line(self) -> str:
        favourite = " | FAVOURITE" if self.favourite else ""
        context = f" | context {' ; '.join(self.context)}" if self.context else ""
        return (
            f"{self.alias} | moment {self.moment_id} | {self.taken_at.isoformat()} | "
            f"{self.media_kind}{favourite}{context} | {self.description}"
        )


def final_asset_cut_prompt(
    candidates: Sequence[FineCutCandidate],
    *,
    memory_label: str,
    memory_type: str,
    editorial_brief: str,
    thesis: dict[str, Any],
    capacity: int,
    required_aliases: Sequence[str] = (),
) -> str:
    """Ask for the real asset cut, after the moment reservoirs were opened."""
    required = tuple(dict.fromkeys(required_aliases))
    valid = {candidate.alias for candidate in candidates}
    if not set(required) <= valid:
        raise ValueError("required final-cut assets must come from the candidate wall")
    remaining = capacity - len(required)
    if remaining < 0:
        raise ValueError("required final-cut assets exceed duration capacity")
    wall = "\n".join(candidate.wall_line() for candidate in candidates)
    shape = {
        "schema_version": FINAL_ASSET_CUT_SCHEMA,
        "keep": [{"asset_id": "A001", "reason": "why this visual earns runtime"}],
        "comparisons": [
            {
                "kept_asset_id": "A001",
                "rejected_asset_id": "A002",
                "reason": "why the retained visual is stronger",
            }
        ],
        "overall_reason": "how the actual visual sequence carries the thesis",
    }
    return f"""You are making {memory_label}, a {memory_type}.

This is the final ASSET cut. The moments were a tentative shortlist whose complete reservoirs are
now open. They are not a one-visual-per-moment quota. You may retain several genuinely different
assets from one rich moment and retain none from a weaker shortlisted moment. Choose at most
{capacity} assets total, including the {len(required)} assets already admitted below. Therefore
return at most {remaining} additional assets. Keep the sequence chronological.

REQUIRED ASSETS ALREADY ADMITTED
{json.dumps(required, separators=(",", ":"))}
They consume capacity. Do not return them again and do not compare against them as rejected.

EDITORIAL BRIEF
{editorial_brief}

THESIS
{json.dumps(thesis, ensure_ascii=False, separators=(",", ":"))}

Choose visible scenes that carry the thesis, relationships, change, place, action, expression, or
atmosphere. Reject near-duplicates, weaker framings, arbitrary objects, household inventory,
screens, documents, and setup evidence when a lived scene carries the same fact. A second asset
from one moment must add a genuinely different beat or useful visual progression. Do not fill a
quota merely because room exists. A closer view, alternate framing, readable title, or clearer
object detail does not create a second beat when the action and relationship are unchanged. A
shortfall is allowed when the remaining material is redundant or evidentiary. Before answering,
compare the weakest optional keep with the strongest rejected alternative.

A favourite is direct owner evidence, not automatic admission of its whole moment. You may drop a
shortlisted moment entirely. If you retain any asset from a moment that contains favourites, at
least one retained asset from that moment must be a favourite.

ASSET WALL
{wall}

Return only one complete JSON object with exactly these keys:
{json.dumps(shape, separators=(",", ":"))}
The schema_version value must be exactly {FINAL_ASSET_CUT_SCHEMA}."""


def read_final_asset_cut(
    raw: str,
    candidates: Sequence[FineCutCandidate],
    *,
    capacity: int,
    required_aliases: Sequence[str] = (),
) -> dict[str, Any]:
    """Validate and merge the model's optional assets with runtime admissions."""
    payload = final_json_object(raw)
    if payload is None or set(payload) != {
        "schema_version",
        "keep",
        "comparisons",
        "overall_reason",
    }:
        raise ValueError("final asset cut has the wrong envelope")
    if payload.get("schema_version") != FINAL_ASSET_CUT_SCHEMA:
        raise ValueError("final asset cut has the wrong schema version")

    by_alias = {candidate.alias: candidate for candidate in candidates}
    if len(by_alias) != len(candidates):
        raise ValueError("final asset cut aliases must be unique")
    required = tuple(dict.fromkeys(required_aliases))
    if not set(required) <= set(by_alias):
        raise ValueError("required final-cut assets are not in the wall")
    room = capacity - len(required)
    if room < 0:
        raise ValueError("required final-cut assets exceed duration capacity")

    raw_keep = payload.get("keep")
    if not isinstance(raw_keep, list):
        raise ValueError("final asset keep rows must be a list")
    optional: list[dict[str, str]] = []
    seen = set(required)
    discarded_required_echoes = 0
    discarded_duplicate_keeps = 0
    for row in raw_keep:
        if not isinstance(row, dict) or set(row) != {"asset_id", "reason"}:
            raise ValueError("final asset keep row has the wrong shape")
        alias = row.get("asset_id")
        reason = bounded_model_text(row.get("reason"), max_chars=_MAX_REASON_CHARS)
        if alias not in by_alias or reason is None:
            raise ValueError("final asset keep row is not grounded")
        if alias in required:
            discarded_required_echoes += 1
            continue
        if alias in seen:
            discarded_duplicate_keeps += 1
            continue
        seen.add(alias)
        optional.append({"asset_id": alias, "reason": reason})
    if len(optional) > room:
        raise ValueError("final asset cut exceeds remaining duration capacity")

    _require_favourite_representation(by_alias, seen)

    comparisons, discarded_comparisons = _read_comparisons(
        payload.get("comparisons"), by_alias, seen, set(required)
    )
    overall = bounded_model_text(payload.get("overall_reason"), max_chars=_MAX_REASON_CHARS)
    if overall is None:
        raise ValueError("final asset cut overall reason is unsafe")

    reasons = {row["asset_id"]: row["reason"] for row in optional}
    reasons.update(
        dict.fromkeys(required, "Admitted by the runtime before the optional asset cut.")
    )
    ordered = [
        {"asset_id": candidate.alias, "reason": reasons[candidate.alias]}
        for candidate in candidates
        if candidate.alias in seen
    ]
    return {
        "keep": ordered,
        "required_asset_ids": list(required),
        "discarded_required_echoes": discarded_required_echoes,
        "discarded_duplicate_keeps": discarded_duplicate_keeps,
        "comparisons": comparisons,
        "discarded_comparisons": discarded_comparisons,
        "overall_reason": overall,
    }


def _require_favourite_representation(
    by_alias: dict[str, FineCutCandidate],
    kept: set[str],
) -> None:
    favourite_moments = {
        candidate.moment_id for candidate in by_alias.values() if candidate.favourite
    }
    selected_by_moment = {
        moment_id: [
            candidate
            for candidate in by_alias.values()
            if candidate.moment_id == moment_id and candidate.alias in kept
        ]
        for moment_id in {
            candidate.moment_id for candidate in by_alias.values() if candidate.alias in kept
        }
    }
    for moment_id, selected in selected_by_moment.items():
        if moment_id in favourite_moments and not any(
            candidate.favourite for candidate in selected
        ):
            raise ValueError("a retained favourite-bearing moment has no favourite asset")


def _read_comparisons(
    rows: object,
    by_alias: dict[str, FineCutCandidate],
    kept: set[str],
    required: set[str],
) -> tuple[list[dict[str, str]], int]:
    if not isinstance(rows, list):
        raise ValueError("final asset comparisons must be a list")
    comparisons: list[dict[str, str]] = []
    discarded = 0
    for row in rows:
        if not isinstance(row, dict) or set(row) != {
            "kept_asset_id",
            "rejected_asset_id",
            "reason",
        }:
            discarded += 1
            continue
        kept_id = row.get("kept_asset_id")
        rejected_id = row.get("rejected_asset_id")
        reason = bounded_model_text(row.get("reason"), max_chars=_MAX_REASON_CHARS)
        if (
            kept_id not in kept
            or kept_id in required
            or rejected_id not in by_alias
            or rejected_id in kept
            or kept_id == rejected_id
            or reason is None
        ):
            discarded += 1
            continue
        comparisons.append(
            {
                "kept_asset_id": kept_id,
                "rejected_asset_id": rejected_id,
                "reason": reason,
            }
        )
    return comparisons, discarded
