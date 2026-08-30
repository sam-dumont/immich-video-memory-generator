"""Bank literal 400px asset descriptions without making an editorial decision."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import dataclass
from functools import partial
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from immich_memories.analysis.contact_sheets import ContactSheetPage, build_contact_sheets
from immich_memories.analysis.editorial_contracts import DecisionProvenance, EditorialCandidate
from immich_memories.analysis.editorial_gateway import VisualEditorialRequest
from immich_memories.analysis.selection_source import PreparedEditorialSource
from immich_memories.analysis.strict_json import bounded_model_text, final_json_object
from immich_memories.analysis.visual_atlas import build_visual_atlas
from immich_memories.analysis.visual_request_planner import VisionRequestLimits
from immich_memories.cache.judgment_cache import VisualJudgmentIdentity

if TYPE_CHECKING:
    from immich_memories.analysis.editorial_gateway import EditorialGateway
    from immich_memories.analysis.visual_atlas import VisualAtlas

__all__ = [
    "AssetDescription",
    "AssetDescriptionResult",
    "SETTING_HEDGE",
    "describe_editorial_assets",
    "setting_suffix",
]

ASSET_DESCRIPTION_SCHEMA_VERSION = "asset-description-v1"
ASSET_DESCRIPTION_PASS_VERSION = "asset-description-v2"  # noqa: S105
ASSET_DESCRIPTION_PROMPT_VERSION = "asset-description-prompt-v2"
ASSET_DESCRIPTION_TILE_PX = 400
ASSET_DESCRIPTION_MAX_CHARS = 240
ASSET_MOTION_DESCRIPTION_SCHEMA_VERSION = "asset-motion-description-v1"
ASSET_MOTION_DESCRIPTION_PASS_VERSION = "asset-motion-description-v2"  # noqa: S105
ASSET_MOTION_DESCRIPTION_PROMPT_VERSION = "asset-motion-description-prompt-v2"
ASSET_MOTION_REASON_MAX_CHARS = 240
# Measured safe for descriptions only (~4x fewer requests). NEVER apply packing
# to pair verdicts -- that variant was measured changing ~20% of decisions.
DESCRIPTION_PACK_SIZE = 4
ASSET_DESCRIPTION_PACKED_SCHEMA_VERSION = "asset-description-packed-v1"
ASSET_DESCRIPTION_PACKED_PASS_VERSION = "asset-description-packed-v2"  # noqa: S105
ASSET_DESCRIPTION_PACKED_PROMPT_VERSION = "asset-description-packed-prompt-v2"
# Matches probe_motion_description_packing.py's validated SCHEMA constant exactly.
ASSET_MOTION_DESCRIPTION_PACKED_SCHEMA_VERSION = "asset-motion-description-packed-v1"
ASSET_MOTION_DESCRIPTION_PACKED_PASS_VERSION = "asset-motion-description-packed-v2"  # noqa: S105
ASSET_MOTION_DESCRIPTION_PACKED_PROMPT_VERSION = "asset-motion-description-packed-prompt-v2"
_RENDER_VERSION = "visual-atlas-v1/contact-sheet-v1/asset-400px"
_MOTION_RENDER_VERSION = "visual-atlas-v1/contact-sheet-v1/asset-motion-400px"
_DEFAULT_LIMITS = VisionRequestLimits(max_output_tokens=4000, timeout_seconds=120)
# One 240-char factual line reduced a ski station to "a man in a red and black jacket" and
# erased the mountain behind him; only 21% of outdoor answers named any vista. The setting
# gets its own slot so a place inside a people-photo still registers, hedged the same way
# the card's people slots are so an indoor scene can decline instead of inventing one.
SETTING_HEDGE = "insufficient evidence"
ASSET_SETTING_MAX_CHARS = 160


def setting_suffix(setting: str | None) -> str:
    """The compact setting cell a wall row or card appends, empty when the place is hedged."""
    text = (setting or "").strip().rstrip(".")
    if not text or text.casefold() == SETTING_HEDGE:
        return ""
    return f" — setting: {text}"


_SHAPE = json.dumps(
    {
        "schema_version": ASSET_DESCRIPTION_SCHEMA_VERSION,
        "description": "what is visibly shown",
        "setting": "where this is, in a few words, or insufficient evidence",
    },
    separators=(",", ":"),
)
_PROMPT = (
    "What does this one numbered visual show? Return one short factual description. "
    "Use one line without double quotes or backslashes. Return only one complete JSON "
    "object with exactly these keys:\n" + _SHAPE
)
_MOTION_SHAPE = json.dumps(
    {
        "schema_version": ASSET_MOTION_DESCRIPTION_SCHEMA_VERSION,
        "description": "what is visibly shown across the chronological frames",
        "setting": "where this is, in a few words, or insufficient evidence",
        "motion_contribution": "meaningful or still_sufficient",
        "motion_reason": "what temporal change adds, or why one still carries the same content",
    },
    separators=(",", ":"),
)
_MOTION_PROMPT = (
    "What does this one numbered chronological filmstrip show? Describe only visible evidence. "
    "Call motion meaningful when the sequence adds action, interaction, expression change, "
    "reveal, route, atmosphere, or progression that one still would lose; quiet temporal change "
    "counts. Use still_sufficient when the frames carry the same contribution, or differ only by "
    "camera movement, pose jitter, or repetition. Return short factual text without double quotes "
    "or backslashes. Return only one complete JSON object with exactly these keys:\n"
    + _MOTION_SHAPE
)
_PACKED_SHAPE = json.dumps(
    {
        "schema_version": ASSET_DESCRIPTION_PACKED_SCHEMA_VERSION,
        "assets": [
            {
                "asset_id": "ALIAS",
                "description": "what is visibly shown",
                "setting": "where this is, in a few words, or insufficient evidence",
            }
        ],
    },
    separators=(",", ":"),
)
_PACKED_MOTION_SHAPE = json.dumps(
    {
        "schema_version": ASSET_MOTION_DESCRIPTION_PACKED_SCHEMA_VERSION,
        "assets": [
            {
                "asset_id": "ALIAS",
                "description": "visible evidence",
                "setting": "where this is, in a few words, or insufficient evidence",
                "motion_contribution": "meaningful or still_sufficient",
                "motion_reason": "temporal contribution",
            }
        ],
    },
    separators=(",", ":"),
)


def _packed_prompt(aliases: tuple[str, ...], *, motion: bool) -> str:
    """The validated pack-four request shape (probe_motion_description_packing.py)."""
    aliases_json = json.dumps(list(aliases), separators=(",", ":"))
    if motion:
        return (
            "Each attached image is one chronological filmstrip for one media asset. "
            f"The attachments correspond in order to these aliases: {aliases_json}\n\n"
            "Judge every asset independently. Describe only visible evidence across its frames. "
            "Call motion meaningful when the sequence adds action, interaction, expression "
            "change, reveal, route, atmosphere, or progression that one still would lose; quiet "
            "temporal change counts. Use still_sufficient when the frames carry the same "
            "contribution, or differ only by camera movement, pose jitter, or repetition. Do not "
            "copy a judgment from a neighboring attachment.\n\n"
            "Return exactly one row per alias in the same order. A short motion_reason is "
            "optional; its absence must not prevent the verdict. Return only one complete JSON "
            f"object with exactly these top-level keys:\n{_PACKED_MOTION_SHAPE}"
        )
    return (
        "Each attached image is one visual for one media asset. The attachments correspond in "
        f"order to these aliases: {aliases_json}\n\n"
        "Describe only what is visibly shown in each, independently. Use one short factual line "
        "per asset without double quotes or backslashes. Return exactly one row per alias in the "
        f"same order. Return only one complete JSON object with exactly these top-level keys:\n"
        f"{_PACKED_SHAPE}"
    )


def _read_packed_row(item: Any, *, motion: bool) -> dict[str, Any] | None:
    """Validate one packed row; a bad row returns None so only that asset falls back."""
    if motion:
        allowed = {
            "asset_id",
            "description",
            "setting",
            "motion_contribution",
            "motion_reason",
        }
        required = allowed - {"motion_reason", "setting"}
    else:
        allowed = {"asset_id", "description", "setting"}
        required = allowed - {"setting"}
    if not isinstance(item, dict) or not required <= set(item) or not set(item) <= allowed:
        return None
    description = bounded_model_text(item.get("description"), max_chars=ASSET_DESCRIPTION_MAX_CHARS)
    if description is None:
        return None
    setting = bounded_model_text(item.get("setting"), max_chars=ASSET_SETTING_MAX_CHARS)
    if not motion:
        return {"description": description, "setting": setting}
    contribution = item.get("motion_contribution")
    if contribution not in {"meaningful", "still_sufficient"}:
        return None
    reason = bounded_model_text(item.get("motion_reason"), max_chars=ASSET_MOTION_REASON_MAX_CHARS)
    return {
        "description": description,
        "setting": setting,
        "motion_contribution": contribution,
        "motion_reason": reason,
    }


def _read_packed_descriptions(
    raw: str, *, aliases: tuple[str, ...], motion: bool
) -> dict[str, dict[str, Any]]:
    """Return whichever aliases parsed and validated; a missing alias falls back solo."""
    schema = (
        ASSET_MOTION_DESCRIPTION_PACKED_SCHEMA_VERSION
        if motion
        else ASSET_DESCRIPTION_PACKED_SCHEMA_VERSION
    )
    payload = final_json_object(raw)
    if payload is None or set(payload) != {"schema_version", "assets"}:
        return {}
    if payload.get("schema_version") != schema or not isinstance(payload.get("assets"), list):
        return {}
    valid: dict[str, dict[str, Any]] = {}
    for item in payload["assets"]:
        row = _read_packed_row(item, motion=motion)
        if row is None:
            continue
        alias = item.get("asset_id") if isinstance(item, dict) else None
        if isinstance(alias, str) and alias in aliases and alias not in valid:
            valid[alias] = row
    return valid


def _solo_raw_text(parsed: dict[str, Any], *, motion: bool) -> str:
    """Re-encode one packed member's extract as the JSON a solo answer would be."""
    if motion:
        payload: dict[str, Any] = {
            "schema_version": ASSET_MOTION_DESCRIPTION_SCHEMA_VERSION,
            "description": parsed["description"],
            "motion_contribution": parsed["motion_contribution"],
        }
        if parsed.get("motion_reason") is not None:
            payload["motion_reason"] = parsed["motion_reason"]
    else:
        payload = {
            "schema_version": ASSET_DESCRIPTION_SCHEMA_VERSION,
            "description": parsed["description"],
        }
    if parsed.get("setting") is not None:
        payload["setting"] = parsed["setting"]
    return json.dumps(payload, separators=(",", ":"))


@dataclass(frozen=True)
class AssetDescription:
    """Literal visible evidence banked at the lifetime of one asset."""

    asset_id: str
    text: str
    provenance: DecisionProvenance
    motion_contribution: Literal["meaningful", "still_sufficient", "not_observed"] = "not_observed"
    motion_reason: str | None = None
    setting: str | None = None


@dataclass(frozen=True)
class AssetDescriptionResult:
    """Descriptions that succeeded; missing evidence never becomes a verdict."""

    descriptions: tuple[AssetDescription, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Rendered:
    """One candidate's pre-built evidence: `page` is None only when unavailable."""

    candidate: EditorialCandidate
    page: ContactSheetPage | None
    motion: bool


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

    Assets that already have no banked answer are batched up to
    `DESCRIPTION_PACK_SIZE` per request (~4x fewer requests, measured safe for
    descriptions only -- never for pair verdicts). An asset with a banked
    answer already is cheaper solo than folded into a live packed call, so it
    is never packed; see `_has_warm_answer`.
    """
    if not 1 <= concurrency <= 8:
        raise ValueError("asset description concurrency must be between 1 and 8")
    visual_atlas = atlas or build_visual_atlas(
        prepared.visual_sources, frame_cache_dir=frame_cache_dir
    )
    limits = limits or _DEFAULT_LIMITS
    rendered = _render_candidates(prepared.candidates, atlas=visual_atlas, output_dir=output_dir)
    work_items = _work_items(rendered, requester=requester, limits=limits)
    describe = partial(_run_work_item, requester=requester, limits=limits)
    # ContextVars do not cross executor boundaries. Capture one independent
    # context per task so run-level LLM usage collection sees every paid image
    # call even when descriptions run concurrently.
    contextual_items = tuple((copy_context(), item) for item in work_items)
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        outcome_groups = tuple(
            executor.map(
                lambda entry: entry[0].run(describe, entry[1]),
                contextual_items,
            )
        )

    order = {candidate.asset_id: index for index, candidate in enumerate(prepared.candidates)}
    flat = sorted(
        (pair for group in outcome_groups for pair in group),
        key=lambda pair: order[pair[0]],
    )
    descriptions: list[AssetDescription] = []
    warnings: list[str] = []
    for _asset_id, outcome in flat:
        if isinstance(outcome, AssetDescription):
            descriptions.append(outcome)
        elif isinstance(outcome, str):
            _warn(prepared, warnings, outcome)
    return AssetDescriptionResult(tuple(descriptions), tuple(warnings))


def _render_candidates(
    candidates: tuple[EditorialCandidate, ...], *, atlas: VisualAtlas, output_dir: Path
) -> tuple[_Rendered, ...]:
    """Build each candidate's 400px sheet once, reused whether it ends up solo or packed."""
    rendered = []
    for candidate in candidates:
        tile = atlas.tile_for(candidate.asset_id)
        if tile.kind == "unavailable":
            rendered.append(_Rendered(candidate, None, False))
            continue
        page = build_contact_sheets(
            (tile,),
            scope_id=_scope_id(candidate.asset_id),
            output_dir=output_dir,
            per_sheet=1,
            tile_px=ASSET_DESCRIPTION_TILE_PX,
        )[0]
        motion = candidate.media_kind in {"video", "live_photo"} and tile.kind == "filmstrip"
        rendered.append(_Rendered(candidate, page, motion))
    return tuple(rendered)


def _work_items(
    rendered: tuple[_Rendered, ...], *, requester: EditorialGateway, limits: VisionRequestLimits
) -> tuple[tuple[_Rendered, ...], ...]:
    """Unavailable or already-warm assets dispatch alone; the rest pack by motion group."""
    items: list[tuple[_Rendered, ...]] = []
    cold: dict[bool, list[_Rendered]] = {True: [], False: []}
    for item in rendered:
        if item.page is None or _has_warm_answer(requester, item, limits=limits):
            items.append((item,))
        else:
            cold[item.motion].append(item)
    for motion in (False, True):
        group = cold[motion]
        items.extend(
            tuple(group[index : index + DESCRIPTION_PACK_SIZE])
            for index in range(0, len(group), DESCRIPTION_PACK_SIZE)
        )
    return tuple(items)


def _run_work_item(
    item: tuple[_Rendered, ...], *, requester: EditorialGateway, limits: VisionRequestLimits
) -> tuple[tuple[str, AssetDescription | str], ...]:
    if len(item) == 1:
        only = item[0]
        return ((only.candidate.asset_id, _describe_rendered(only, requester, limits)),)
    return _describe_packed(item, requester=requester, limits=limits)


def _describe_rendered(
    rendered: _Rendered, requester: EditorialGateway, limits: VisionRequestLimits
) -> AssetDescription | str:
    if rendered.page is None:
        return f"!! asset description unavailable: {rendered.candidate.asset_id}"
    return _describe_one(
        rendered.candidate.asset_id,
        rendered.page,
        requester,
        limits,
        classify_motion=rendered.motion,
    )


def _describe_packed(
    items: tuple[_Rendered, ...], *, requester: EditorialGateway, limits: VisionRequestLimits
) -> tuple[tuple[str, AssetDescription | str], ...]:
    """One combined request for up to four cold assets; a bad row falls back solo."""
    motion = items[0].motion
    aliases = tuple(str(index) for index in range(1, len(items) + 1))
    parsed_by_alias: dict[str, dict[str, Any]] = {}
    provenance: DecisionProvenance | None = None
    try:
        answer = requester.ask(_packed_request(items, aliases, motion=motion, limits=limits))
        parsed_by_alias = _read_packed_descriptions(answer.raw_text, aliases=aliases, motion=motion)
        provenance = answer.provenance
    except Exception:  # noqa: BLE001 - a broken packed call falls back per asset below
        pass
    outcomes: list[tuple[str, AssetDescription | str]] = []
    for alias, rendered in zip(aliases, items, strict=True):
        parsed = parsed_by_alias.get(alias)
        if parsed is None:
            outcomes.append(
                (rendered.candidate.asset_id, _describe_rendered(rendered, requester, limits))
            )
            continue
        assert provenance is not None  # parsed_by_alias is only non-empty once provenance is set
        outcomes.append(
            (
                rendered.candidate.asset_id,
                AssetDescription(
                    rendered.candidate.asset_id,
                    parsed["description"],
                    provenance,
                    motion_contribution=parsed.get("motion_contribution", "not_observed"),
                    motion_reason=parsed.get("motion_reason"),
                    setting=parsed.get("setting"),
                ),
            )
        )
        _bank_solo_extract(requester, rendered, parsed=parsed, motion=motion, limits=limits)
    return tuple(outcomes)


def _packed_request(
    items: tuple[_Rendered, ...],
    aliases: tuple[str, ...],
    *,
    motion: bool,
    limits: VisionRequestLimits,
) -> VisualEditorialRequest:
    return VisualEditorialRequest(
        pass_name="asset-description",  # noqa: S106
        pass_version=(
            ASSET_MOTION_DESCRIPTION_PACKED_PASS_VERSION
            if motion
            else ASSET_DESCRIPTION_PACKED_PASS_VERSION
        ),
        prompt=_packed_prompt(aliases, motion=motion),
        prompt_version=(
            ASSET_MOTION_DESCRIPTION_PACKED_PROMPT_VERSION
            if motion
            else ASSET_DESCRIPTION_PACKED_PROMPT_VERSION
        ),
        schema_version=(
            ASSET_MOTION_DESCRIPTION_PACKED_SCHEMA_VERSION
            if motion
            else ASSET_DESCRIPTION_PACKED_SCHEMA_VERSION
        ),
        pages=tuple(cast(ContactSheetPage, item.page) for item in items),
        ordered_input_ids=tuple(item.candidate.asset_id for item in items),
        ordered_group_ids=(),
        grounded_annotations=(),
        upstream_material=(),
        render_version=_MOTION_RENDER_VERSION if motion else _RENDER_VERSION,
        limits=limits,
        image_detail="high",
    )


def _solo_identity(
    asset_id: str,
    page: ContactSheetPage,
    *,
    motion: bool,
    limits: VisionRequestLimits,
    model: str | None,
    endpoint: str,
) -> VisualJudgmentIdentity:
    """Mirror VisualEditorialGateway.ask()'s identity for a hypothetical solo request.

    Lets packing check for, and seed, per-asset warm reuse without a live call.
    Keep in sync with editorial_gateway.py's own identity construction.
    """
    return VisualJudgmentIdentity(
        page_bytes=(page.jpeg_bytes,),
        ordered_input_ids=(asset_id,),
        ordered_group_ids=(),
        annotations=(),
        model=model,
        thinking=False,
        image_detail="high",
        pass_name="asset-description",  # noqa: S106
        pass_version=(
            ASSET_MOTION_DESCRIPTION_PASS_VERSION if motion else ASSET_DESCRIPTION_PASS_VERSION
        ),
        prompt_version=(
            ASSET_MOTION_DESCRIPTION_PROMPT_VERSION if motion else ASSET_DESCRIPTION_PROMPT_VERSION
        ),
        schema_version=(
            ASSET_MOTION_DESCRIPTION_SCHEMA_VERSION if motion else ASSET_DESCRIPTION_SCHEMA_VERSION
        ),
        render_version=_MOTION_RENDER_VERSION if motion else _RENDER_VERSION,
        layout_versions=(page.layout_version,),
        upstream_material=(),
        request_limits=(
            f"max_pages={limits.max_pages_per_request}",
            f"max_output_tokens={limits.max_output_tokens}",
            f"timeout_seconds={limits.timeout_seconds}",
        ),
        continuation_identity=(1, 1),
        endpoint=endpoint,
    )


def _has_warm_answer(
    requester: EditorialGateway, rendered: _Rendered, *, limits: VisionRequestLimits
) -> bool:
    """Best-effort: is a solo answer for this exact asset already banked?

    Packing only helps assets with no answer yet; one the cache already has
    is free to fetch solo and must not be folded into a live packed call just
    because it shares a request slot with a cold neighbour. Only the concrete
    gateway exposes the cache `.ask()` reads from; a bare Protocol double has
    nothing to check, so every candidate is treated as cold for it -- packing
    still produces correct answers, just without this warm short-circuit.
    """
    cache = getattr(requester, "cache", None)
    llm_config = getattr(requester, "llm_config", None)
    if cache is None or llm_config is None or rendered.page is None:
        return False
    identity = _solo_identity(
        rendered.candidate.asset_id,
        rendered.page,
        motion=rendered.motion,
        limits=limits,
        model=llm_config.model,
        endpoint=(llm_config.base_url or "").rstrip("/"),
    )
    return cache.answer_for(identity.key()) is not None


def _bank_solo_extract(
    requester: EditorialGateway,
    rendered: _Rendered,
    *,
    parsed: dict[str, Any],
    motion: bool,
    limits: VisionRequestLimits,
) -> None:
    """Seed the solo identity from a packed answer so a later regrouping still warms.

    Pack membership is not stable across runs (reservoir/cull churn), so the
    combined pack's own cache key will not reliably recur. Seeding the
    identity a solo request would have used means a later solo lookup, or a
    differently packed run, still gets a warm hit for this one asset.
    """
    cache = getattr(requester, "cache", None)
    llm_config = getattr(requester, "llm_config", None)
    if cache is None or llm_config is None or rendered.page is None:
        return
    identity = _solo_identity(
        rendered.candidate.asset_id,
        rendered.page,
        motion=motion,
        limits=limits,
        model=llm_config.model,
        endpoint=(llm_config.base_url or "").rstrip("/"),
    )
    key = identity.key()
    provenance = DecisionProvenance(
        pass_name="asset-description",  # noqa: S106
        pass_version=identity.pass_version,
        schema_version=identity.schema_version,
        model_identity=llm_config.model or "",
        input_ids=(rendered.candidate.asset_id,),
        sheet_hashes=(rendered.page.sha256,),
        request_key=key,
        cache_hit=False,
    )
    cache.remember(
        key, _solo_raw_text(parsed, motion=motion), json.dumps(provenance.__dict__, sort_keys=True)
    )


def _describe_one(
    asset_id: str,
    page: ContactSheetPage,
    requester: EditorialGateway,
    limits: VisionRequestLimits,
    *,
    classify_motion: bool,
) -> AssetDescription | str:
    """Describe one rendered asset; callers restore chronological result order."""
    try:
        motion_expected = classify_motion
        answer = requester.ask(
            VisualEditorialRequest(
                pass_name="asset-description",  # noqa: S106
                pass_version=(
                    ASSET_MOTION_DESCRIPTION_PASS_VERSION
                    if motion_expected
                    else ASSET_DESCRIPTION_PASS_VERSION
                ),
                prompt=_MOTION_PROMPT if motion_expected else _PROMPT,
                prompt_version=(
                    ASSET_MOTION_DESCRIPTION_PROMPT_VERSION
                    if motion_expected
                    else ASSET_DESCRIPTION_PROMPT_VERSION
                ),
                schema_version=(
                    ASSET_MOTION_DESCRIPTION_SCHEMA_VERSION
                    if motion_expected
                    else ASSET_DESCRIPTION_SCHEMA_VERSION
                ),
                pages=(page,),
                ordered_input_ids=(asset_id,),
                ordered_group_ids=(),
                grounded_annotations=(),
                upstream_material=(),
                render_version=_MOTION_RENDER_VERSION if motion_expected else _RENDER_VERSION,
                limits=limits,
                image_detail="high",
            )
        )
    except Exception:  # noqa: BLE001 - missing evidence cannot change membership
        return f"!! asset description failed: {asset_id}"
    if motion_expected:
        motion = _read_motion_description(answer.raw_text)
        if motion is None:
            return f"!! asset description unreadable: {asset_id}"
        text, contribution, reason, setting = motion
        return AssetDescription(
            asset_id,
            text,
            answer.provenance,
            motion_contribution=contribution,
            motion_reason=reason,
            setting=setting,
        )
    static = _read_description(answer.raw_text)
    if static is None:
        return f"!! asset description unreadable: {asset_id}"
    text, setting = static
    return AssetDescription(asset_id, text, answer.provenance, setting=setting)


def _read_description(raw: str) -> tuple[str, str | None] | None:
    payload = final_json_object(raw)
    required = {"schema_version", "description"}
    if (
        payload is None
        or not required <= set(payload)
        or not set(payload) <= {*required, "setting"}
    ):
        return None
    if payload.get("schema_version") != ASSET_DESCRIPTION_SCHEMA_VERSION:
        return None
    description = bounded_model_text(
        payload.get("description"), max_chars=ASSET_DESCRIPTION_MAX_CHARS
    )
    if description is None:
        return None
    return description, bounded_model_text(
        payload.get("setting"), max_chars=ASSET_SETTING_MAX_CHARS
    )


def _read_motion_description(
    raw: str,
) -> tuple[str, Literal["meaningful", "still_sufficient"], str | None, str | None] | None:
    payload = final_json_object(raw)
    required = {
        "schema_version",
        "description",
        "motion_contribution",
    }
    allowed = {*required, "motion_reason", "setting"}
    if payload is None or not required <= set(payload) or not set(payload) <= allowed:
        return None
    if payload.get("schema_version") != ASSET_MOTION_DESCRIPTION_SCHEMA_VERSION:
        return None
    description = bounded_model_text(
        payload.get("description"), max_chars=ASSET_DESCRIPTION_MAX_CHARS
    )
    contribution = payload.get("motion_contribution")
    reason = bounded_model_text(
        payload.get("motion_reason"), max_chars=ASSET_MOTION_REASON_MAX_CHARS
    )
    setting = bounded_model_text(payload.get("setting"), max_chars=ASSET_SETTING_MAX_CHARS)
    if description is None or contribution not in {"meaningful", "still_sufficient"}:
        return None
    return description, contribution, reason, setting


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
