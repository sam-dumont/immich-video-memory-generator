"""Bank literal 400px asset descriptions without making an editorial decision."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import dataclass
from functools import partial
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING

from immich_memories.analysis.contact_sheets import ContactSheetPage, build_contact_sheets
from immich_memories.analysis.editorial_contracts import DecisionProvenance, EditorialCandidate
from immich_memories.analysis.editorial_gateway import VisualEditorialRequest
from immich_memories.analysis.selection_source import PreparedEditorialSource
from immich_memories.analysis.strict_json import bounded_model_text, final_json_object
from immich_memories.analysis.visual_atlas import build_visual_atlas
from immich_memories.analysis.visual_request_planner import VisionRequestLimits

if TYPE_CHECKING:
    from immich_memories.analysis.editorial_gateway import EditorialGateway
    from immich_memories.analysis.visual_atlas import VisualAtlas

__all__ = ["AssetDescription", "AssetDescriptionResult", "describe_editorial_assets"]

ASSET_DESCRIPTION_SCHEMA_VERSION = "asset-description-v1"
ASSET_DESCRIPTION_PASS_VERSION = "asset-description-v1"  # noqa: S105
ASSET_DESCRIPTION_PROMPT_VERSION = "asset-description-prompt-v1"
ASSET_DESCRIPTION_TILE_PX = 400
ASSET_DESCRIPTION_MAX_CHARS = 240
_RENDER_VERSION = "visual-atlas-v1/contact-sheet-v1/asset-400px"
_DEFAULT_LIMITS = VisionRequestLimits(max_output_tokens=4000, timeout_seconds=120)
_SHAPE = json.dumps(
    {
        "schema_version": ASSET_DESCRIPTION_SCHEMA_VERSION,
        "description": "what is visibly shown",
    },
    separators=(",", ":"),
)
_PROMPT = (
    "What does this one numbered visual show? Return one short factual description. "
    "Use one line without double quotes or backslashes. Return only one complete JSON "
    "object with exactly these keys:\n" + _SHAPE
)


@dataclass(frozen=True)
class AssetDescription:
    """Literal visible evidence banked at the lifetime of one asset."""

    asset_id: str
    text: str
    provenance: DecisionProvenance


@dataclass(frozen=True)
class AssetDescriptionResult:
    """Descriptions that succeeded; missing evidence never becomes a verdict."""

    descriptions: tuple[AssetDescription, ...]
    warnings: tuple[str, ...] = ()


def describe_editorial_assets(
    prepared: PreparedEditorialSource,
    *,
    requester: EditorialGateway,
    output_dir: Path,
    frame_cache_dir: Path | None,
    limits: VisionRequestLimits | None = None,
    atlas: VisualAtlas | None = None,
    concurrency: int = 1,
) -> AssetDescriptionResult:
    """Describe each viewable asset once, independent of memory scope.

    Instrument ladder: metadata, Immich-derived facts, arithmetic and classical
    CV can say where, when, who, text coverage and technical quality. None can
    name an otherwise unseen object. This rung-6 probe supplies only those
    literal words; it cannot keep or reject an asset.
    """
    if not 1 <= concurrency <= 8:
        raise ValueError("asset description concurrency must be between 1 and 8")
    visual_atlas = atlas or build_visual_atlas(
        prepared.visual_sources, frame_cache_dir=frame_cache_dir
    )
    describe = partial(
        _describe_candidate,
        atlas=visual_atlas,
        output_dir=output_dir,
        requester=requester,
        limits=limits or _DEFAULT_LIMITS,
    )
    # ContextVars do not cross executor boundaries. Capture one independent
    # context per task so run-level LLM usage collection sees every paid image
    # call even when descriptions run concurrently.
    contextual_candidates = tuple((copy_context(), candidate) for candidate in prepared.candidates)
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        outcomes = tuple(
            executor.map(
                lambda item: item[0].run(describe, item[1]),
                contextual_candidates,
            )
        )

    descriptions: list[AssetDescription] = []
    warnings: list[str] = []
    for outcome in outcomes:
        if isinstance(outcome, AssetDescription):
            descriptions.append(outcome)
        elif isinstance(outcome, str):
            _warn(prepared, warnings, outcome)
    return AssetDescriptionResult(tuple(descriptions), tuple(warnings))


def _describe_candidate(
    candidate: EditorialCandidate,
    *,
    atlas: VisualAtlas,
    output_dir: Path,
    requester: EditorialGateway,
    limits: VisionRequestLimits,
) -> AssetDescription | str:
    tile = atlas.tile_for(candidate.asset_id)
    if tile.kind == "unavailable":
        return f"!! asset description unavailable: {candidate.asset_id}"
    page = build_contact_sheets(
        (tile,),
        scope_id=_scope_id(candidate.asset_id),
        output_dir=output_dir,
        per_sheet=1,
        tile_px=ASSET_DESCRIPTION_TILE_PX,
    )[0]
    return _describe_one(candidate.asset_id, page, requester, limits)


def _describe_one(
    asset_id: str,
    page: ContactSheetPage,
    requester: EditorialGateway,
    limits: VisionRequestLimits,
) -> AssetDescription | str:
    """Describe one rendered asset; callers restore chronological result order."""
    try:
        answer = requester.ask(
            VisualEditorialRequest(
                pass_name="asset-description",  # noqa: S106
                pass_version=ASSET_DESCRIPTION_PASS_VERSION,
                prompt=_PROMPT,
                prompt_version=ASSET_DESCRIPTION_PROMPT_VERSION,
                schema_version=ASSET_DESCRIPTION_SCHEMA_VERSION,
                pages=(page,),
                ordered_input_ids=(asset_id,),
                ordered_group_ids=(),
                grounded_annotations=(),
                upstream_material=(),
                render_version=_RENDER_VERSION,
                limits=limits,
                image_detail="high",
            )
        )
    except Exception:  # noqa: BLE001 - missing evidence cannot change membership
        return f"!! asset description failed: {asset_id}"
    text = _read_description(answer.raw_text)
    if text is None:
        return f"!! asset description unreadable: {asset_id}"
    return AssetDescription(asset_id, text, answer.provenance)


def _read_description(raw: str) -> str | None:
    payload = final_json_object(raw)
    if payload is None or set(payload) != {"schema_version", "description"}:
        return None
    if payload.get("schema_version") != ASSET_DESCRIPTION_SCHEMA_VERSION:
        return None
    return bounded_model_text(payload.get("description"), max_chars=ASSET_DESCRIPTION_MAX_CHARS)


def _scope_id(asset_id: str) -> str:
    digest = sha256(asset_id.encode()).hexdigest()[:20]
    return f"asset-description-{digest}"


def _warn(
    prepared: PreparedEditorialSource,
    warnings: list[str],
    warning: str,
) -> None:
    warnings.append(warning)
    if warning not in prepared.trace.warnings:
        prepared.trace.warnings.append(warning)
